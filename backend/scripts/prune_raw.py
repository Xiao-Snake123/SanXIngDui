"""收尾清理：删掉体检发现的两类冗余，并按「只要简体中文」移除英文 PDF。

每一项先**核对再删**，不凭猜测：

A. 百科里 4 篇与《三星堆遗址》共用同一摘要的文档
   体检只看到"都是 403 字"就怀疑重复 —— 字数相同不等于内容相同。
   所以这里真去比对正文前 200 字，确认为同一份摘要才删。

B. 2 篇极短百科（21 字 / 30 字）
   短于最小切块长度，卡片在检索里近乎噪声。

C. 英文 PDF（政策：只要简体中文）
   它们还没解析进知识库，但留着会在"PDF 解析"那一步引入外语正文。
   移到 `data/raw/_excluded/` 而不是直接删除 —— `manifest.csv` 里有原始 URL，
   随时可重新下载，但移动能避免误删后还得再找一遍来源。

用法：python scripts/prune_raw.py [--apply]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.text import fold  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

# 待核对：先看是否与《三星堆遗址》同一份摘要
DUPLICATE_CANDIDATES = ("三星堆", "三星堆文化", "三星堆文明", "三星堆考古")
KEEP_AS_MASTER = "三星堆遗址"
ULTRA_SHORT = ("石璧", "青铜扭头跪坐人像")
# 英文来源（manifest.csv 记录过来源，均为英文文献）
ENGLISH_PDFS = (
    "antiquity_2022_new_discoveries.pdf",
    "isccac_2024_sanxingdui_civilization.pdf",
    "semanticscholar_2024_microbial_diversity.pdf",
)


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _baike_dir() -> Path:
    return _root() / "data" / "raw" / "baike"


def _cards_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "corpus" / "v2"


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    return text[end + 5 :] if end != -1 else text


def _index_rows() -> list[dict]:
    path = _baike_dir() / "_index.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def _write_index(rows: list[dict]) -> None:
    path = _baike_dir() / "_index.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )


def prune_duplicates(*, apply: bool) -> list[str]:
    """A. 核对后再删：只有正文前 200 字与《三星堆遗址》一致，才认定为同一摘要。"""
    master = _baike_dir() / f"{KEEP_AS_MASTER}.md"
    if not master.exists():
        print(f"  A. 主条目 {KEEP_AS_MASTER}.md 不存在，跳过")
        return []
    master_head = fold(_body(master).strip()[:200])
    confirmed: list[str] = []
    different: list[str] = []
    for name in DUPLICATE_CANDIDATES:
        path = _baike_dir() / f"{name}.md"
        if not path.exists():
            continue
        head = fold(_body(path).strip()[:200])
        if head == master_head:
            confirmed.append(name)
        else:
            different.append(name)
    print(f"  A. 与《{KEEP_AS_MASTER}》摘要相同的: {confirmed}")
    if different:
        print(f"     字数相同但内容不同、因此**保留**: {different}")
    return confirmed


def prune_ultra_short(*, apply: bool) -> list[str]:
    found = []
    for name in ULTRA_SHORT:
        path = _baike_dir() / f"{name}.md"
        if not path.exists():
            continue
        found.append(f"{name}({len(_body(path).strip())}字)")
    print(f"  B. 极短文摘: {found}")
    return [name for name in ULTRA_SHORT if (_baike_dir() / f"{name}.md").exists()]


def move_english_pdfs(*, apply: bool) -> None:
    academic = _root() / "data" / "raw" / "academic"
    excluded = _root() / "data" / "raw" / "_excluded"
    moved = []
    for name in ENGLISH_PDFS:
        path = academic / name
        if not path.exists():
            continue
        moved.append(name)
        if apply:
            excluded.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(excluded / name))
    print(f"  C. 英文 PDF 移出: {moved if moved else '（无）'}")


def _drop_cards(drop_titles: list[str], *, apply: bool) -> None:
    path = _cards_dir() / "baike_crawl.jsonl"
    if not path.exists():
        return
    kept_lines: list[str] = []
    dropped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("//"):
            kept_lines.append(stripped)
            continue
        try:
            card = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if fold(str(card.get("title") or "")) in {fold(t) for t in drop_titles}:
            dropped += 1
            continue
        kept_lines.append(json.dumps(card, ensure_ascii=False))
    print(f"  D. 卡片同步删除: {dropped} 张")
    if apply:
        path.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="收尾清理：删除冗余与英文材料")
    parser.add_argument("--apply", action="store_true", help="真正写入；不加则只核对")
    args = parser.parse_args()
    print("=" * 72)
    print("收尾清理（A 同摘要去重 / B 极短文摘 / C 英文 PDF / D 卡片同步）")
    print("=" * 72)

    duplicates = prune_duplicates(apply=args.apply)
    shorts = prune_ultra_short(apply=args.apply)
    move_english_pdfs(apply=args.apply)

    drop = duplicates + shorts
    rows = _index_rows()
    kept_rows = [row for row in rows if fold(str(row.get("title") or "")) not in {fold(t) for t in drop}]
    print(f"  D. 索引同步: {len(rows)} -> {len(kept_rows)} 行")

    if args.apply:
        for name in drop:
            path = _baike_dir() / f"{name}.md"
            if path.exists():
                path.unlink()
        _write_index(kept_rows)
        _drop_cards(drop, apply=True)

    print("=" * 72)
    print("已写入" if args.apply else "核对（未写入，加 --apply 生效）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
