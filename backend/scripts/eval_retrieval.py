"""检索质量评估：recall@k / MRR / nDCG。

为什么必须有这个脚本
--------------------
本项目此前**完全没有检索质量的度量** —— `app/eval/harness.py` 评的是图像风格错配率，
与检索无关。后果是无法回答三个基本问题：「recall@5 是多少」「这次改动让检索变好还是
变坏」「换成真实 embedding 值不值得」。没有度量，一切调参都是凭感觉，
而「凭感觉」在本项目里已经被证明不可靠（回炉 bug、繁简 bug、校勘注污染，
三次都是靠实测数据才定位的）。

判分方式：按**证据片段**，不按 doc_id
--------------------------------------
每条评测项长这样：

    {"query": "青铜纵目面具的眼睛有多凸", "evidence": ["16 厘米", "柱状向外凸"]}

只要 top-k 里有一段的正文包含任一 `evidence` 片段，就算这条问句被成功检索到。

为什么不用 doc_id：

1. **跨语料可比**。v1（旧 50 条）与 v2（新 150 条）的 doc_id 完全不同，
   按 doc_id 判分就无法回答「改造带来了多少提升」——而那正是本次改造要证明的事。
2. **抗重新采集**。doc_id 会随采集变化（哈希前缀、序号都变），
   写死 doc_id 的评测集一次重采就全废。
3. **测的是真问题**。我们要回答的是「检索到的证据能不能回答这个提问」，
   而不是「有没有命中我预设的那张卡片」。

防「自己给自己出题」
--------------------
评测集由我生成，这本身有作弊风险（写一条语料里不存在的期望，测试就永远失败；
写一条只在问题里重复词句的期望，测的就只是词面匹配）。所以有两个机械约束：

1. `--validate-only` 会检查**每条 evidence 片段必须真的出现在语料里**，
   否则报错。这条约束让我无法凭空编造期望。
2. 评测集按类型分层（专名直查 / 语义改写 / 形制数值 / 跨文档比较），
   分类报告指标——某一类拉胯会立刻看出来，而不是被平均值盖住。

用法
----
    python scripts/eval_retrieval.py --corpus data/corpus/v2        # 评测（BM25 通道）
    python scripts/eval_retrieval.py --corpus data/corpus           # v1 基线
    python scripts/eval_retrieval.py --validate-only                # 只校验评测集本身
    python scripts/eval_retrieval.py --corpus data/corpus/v2 --dense  # 含向量通道（会写库）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.rag.corpus import Corpus, load_corpus  # noqa: E402
from app.rag.embedder import HashingEmbedder  # noqa: E402
from app.rag.store import HybridRetriever  # noqa: E402
from app.rag.text import fold, normalize  # noqa: E402

GOLDEN_PATH = settings.resolve("data/eval/golden_retrieval.jsonl")
MAX_K = 10


def _canon(text: str) -> str:
    """与 `CorpusEntry.searchable_text` 相同的规范化：先 NFKC 再繁简折叠。

    两侧必须走**完全一样**的规范化，否则会出现假失配：语料的 `searchable_text`
    经过了 NFKC（全角→半角），而拿未规范化的证据片段去比，会因为一个全角逗号
    匹配不上。这类假失配看起来像「检索失败」，实际是评测代码的 bug。
    """
    return fold(normalize(text))


# ── 评测集 ──────────────────────────────────────────────────────────────────
def load_golden(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        try:
            item = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path.name}:{line_no} JSON 解析失败: {exc}") from exc
        for required in ("id", "query", "type", "evidence"):
            if not item.get(required):
                raise SystemExit(f"{path.name}:{line_no} 缺少字段 {required!r}")
        items.append(item)
    return items


def validate_golden(items: list[dict[str, Any]], corpus: Corpus) -> list[str]:
    """校验评测集**被语料支撑**：每条 evidence 片段必须能在语料里找到。

    这条检查是「不给自己出题」的机械约束。若某条 evidence 在语料里不存在，
    说明我写了一个凭想象编造的期望 —— 那种条目会让指标永久偏低，
    而且看起来像「检索不行」，实际是评测集错了。
    """
    haystack = _canon(" ".join(entry.searchable_text for entry in corpus.all()))
    problems: list[str] = []
    for item in items:
        missing = [frag for frag in item["evidence"] if _canon(frag) not in haystack]
        if missing:
            problems.append(f"{item['id']}: evidence 在语料里找不到 -> {missing}")
    ids = [item["id"] for item in items]
    duplicated = {x for x in ids if ids.count(x) > 1}
    if duplicated:
        problems.append(f"评测项 id 重复: {sorted(duplicated)}")
    return problems


# ── 指标 ────────────────────────────────────────────────────────────────────
def _hit_ranks(chunks: list[Any], evidence: list[str]) -> list[int]:
    """返回每个命中的排名（1-based），用于 MRR 与 nDCG。"""
    needles = [_canon(frag) for frag in evidence]
    ranks: list[int] = []
    for index, chunk in enumerate(chunks, start=1):
        body = _canon(chunk.text)
        if any(needle in body for needle in needles):
            ranks.append(index)
    return ranks


async def evaluate_async(
    retriever: HybridRetriever, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """评估的协程实现。**建索引必须与它在同一个事件循环里**——
    分两次 `asyncio.run()` 会让第二次拿到属于已关闭循环的连接池，
    向量通道会静默降级成纯 BM25（见 db.Database.session 的注释）。
    """
    rows = []
    dense_contributed = 0
    for item in items:
        chunks, diagnostics = await retriever.search(item["query"], top_n=MAX_K, rerank=False)
        rows.append((item, chunks))
        if diagnostics.dense_hits:
            dense_contributed += 1

    per_type: dict[str, dict[str, float]] = defaultdict(
        lambda: {"n": 0, "recall@5": 0.0, "recall@10": 0.0, "mrr": 0.0, "ndcg@10": 0.0}
    )
    overall = {"n": 0, "recall@1": 0.0, "recall@3": 0.0, "recall@5": 0.0,
               "recall@10": 0.0, "mrr": 0.0, "ndcg@10": 0.0}
    misses: list[str] = []

    for item, chunks in rows:
        ranks = _hit_ranks(chunks, item["evidence"])
        best = min(ranks) if ranks else 0
        overall["n"] += 1
        for k in (1, 3, 5, 10):
            if best and best <= k:
                overall[f"recall@{k}"] += 1
        if best:
            overall["mrr"] += 1.0 / best
            # 二值相关性的 nDCG@10：理想排序是把所有命中排在最前
            dcg = sum(1.0 / math.log2(rank + 1) for rank in ranks if rank <= MAX_K)
            ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(ranks), MAX_K) + 1))
            overall["ndcg@10"] += (dcg / ideal) if ideal else 0.0
        else:
            misses.append(item["id"])

        bucket = per_type[item["type"]]
        bucket["n"] += 1
        bucket["recall@5"] += 1.0 if (best and best <= 5) else 0.0
        bucket["recall@10"] += 1.0 if (best and best <= 10) else 0.0
        bucket["mrr"] += (1.0 / best) if best else 0.0

    n = overall["n"] or 1
    for key in ("recall@1", "recall@3", "recall@5", "recall@10", "mrr", "ndcg@10"):
        overall[key] = overall[key] / n
    for bucket in per_type.values():
        count = bucket["n"] or 1
        for key in ("recall@5", "recall@10", "mrr"):
            bucket[key] = bucket[key] / count

    return {
        "overall": overall,
        "per_type": dict(per_type),
        "misses": misses,
        # 向量通道是否真的参与了每一次检索。没有这个数字，
        # 「向量静默降级成纯 BM25」会完全不可见 —— 而那正是发生过的事故。
        "dense_contributed": dense_contributed,
        "queries": len(rows),
    }


def evaluate(retriever: HybridRetriever, items: list[dict[str, Any]]) -> dict[str, Any]:
    """同步包装。仅适用于「检索器已在当前循环之外建好」的场景（如只建 BM25）。

    带向量通道时请在同一协程里调用 `evaluate_async`，见它的注释。
    """
    return asyncio.run(evaluate_async(retriever, items))


def report(name: str, result: dict[str, Any]) -> None:
    overall = result["overall"]
    print(f"\n===== {name} =====")
    print(f"  条数        {overall['n']}")
    print(f"  recall@1    {overall['recall@1']:.3f}")
    print(f"  recall@3    {overall['recall@3']:.3f}")
    print(f"  recall@5    {overall['recall@5']:.3f}")
    print(f"  recall@10   {overall['recall@10']:.3f}")
    print(f"  MRR         {overall['mrr']:.3f}")
    print(f"  nDCG@10     {overall['ndcg@10']:.3f}")
    print("\n  -- 按类型 --")
    for type_name, bucket in sorted(result["per_type"].items()):
        print(
            f"  {type_name:<12} n={int(bucket['n']):<3} "
            f"recall@5={bucket['recall@5']:.3f} recall@10={bucket['recall@10']:.3f} "
            f"mrr={bucket['mrr']:.3f}"
        )
    if result["misses"]:
        print(f"\n  未召回 {len(result['misses'])} 条: {', '.join(result['misses'][:12])}")

    contributed = result["dense_contributed"]
    total = result["queries"]
    if contributed < total:
        print(
            f"\n  [注意] 有 {total - contributed}/{total} 条查询**没有向量通道参与**。\n"
            "  若这次是带 --dense 跑的，说明向量通道静默降级成了纯 BM25 ——\n"
            "  最常见原因是「连接池属于已关闭的事件循环」，见 db.Database.session 的注释。"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="检索质量评估")
    parser.add_argument("--corpus", default="data/corpus/v2", help="语料目录（相对 backend/）")
    parser.add_argument("--validate-only", action="store_true", help="只校验评测集，不跑检索")
    parser.add_argument("--dense", action="store_true", help="启用向量通道（会写入向量库）")
    args = parser.parse_args()

    items = load_golden(GOLDEN_PATH)
    corpus_dir = Path(args.corpus)
    if not corpus_dir.is_absolute():
        corpus_dir = settings.resolve(args.corpus)
    corpus = load_corpus(corpus_dir)
    print(f"评测集 {len(items)} 条 | 语料 {len(corpus)} 条 ({corpus_dir.name}) | 指纹 {corpus.fingerprint}")

    problems = validate_golden(items, corpus)
    if problems:
        grounded = len(items) - len(problems)
        print(
            f"\n[FAIL] 该语料只能支撑 {grounded}/{len(items)} 条评测项 —— "
            f"其余 {len(problems)} 条的期望证据在这份语料里不存在。"
        )
        print(
            "  注意怎么解读这个结果：它**不是**「这个语料检索得差」，\n"
            "  而是「这个语料里没有这些内容」。两者完全不同 ——\n"
            "  拿新语料的内容出题去问旧语料，测出来的是「语料缺内容」，不是检索能力。\n"
            "  跨语料比较要有意义，前提是两边的语料覆盖同一批事实。"
        )
        for problem in problems[:8]:
            print(f"  - {problem}")
        if len(problems) > 8:
            print(f"  ... 另有 {len(problems) - 8} 条")
        return 1
    print("[OK] 评测集校验通过：每条期望证据都能在语料中找到")

    if args.validate_only:
        return 0

    if args.corpus.endswith("v2") or corpus_dir.name == "v2":
        name = "v2 新语料"
    else:
        name = "v1 基线语料"

    if args.dense:
        # 走完整链路（含向量）。注意这会向 corpus_chunks 写入这批语料的向量。
        #
        # 关键：**建索引与评估必须在同一个协程里**（之前分了两次 asyncio.run，
        # 导致向量通道静默降级成纯 BM25，而报告上完全看不出来）。
        async def _dense() -> dict[str, Any]:
            retriever = await HybridRetriever.build(corpus_dir)
            return await evaluate_async(retriever, items)

        result = asyncio.run(_dense())
        label = f"{name}（BM25 + 真实 embedding）"
    else:
        # 只建 BM25：默认不写数据库，评测因此是**无副作用**的，可以随便跑
        retriever = HybridRetriever(corpus, HashingEmbedder())
        label = f"{name}（仅 BM25）"
        result = evaluate(retriever, items)

    report(label, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
