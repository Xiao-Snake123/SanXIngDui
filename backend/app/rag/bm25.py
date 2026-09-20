"""BM25（Okapi）稀疏检索 —— 纯 Python 实现，零依赖。

在「领域专名密集 + 语料规模千级」这个场景下，BM25 的召回质量经常优于小维度向量，
且：
- 无需任何网络调用（无 Key 时它是唯一可用的召回通道）
- 倒排索引构建 < 50ms（千级语料），可进程内常驻
- 提供**可解释的**命中证据（哪些 term 命中了哪条史料），便于排查坏 case
"""

from __future__ import annotations

import math
import pickle
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.rag.text import tokenize


@dataclass(slots=True)
class ScoredDoc:
    doc_id: str
    score: float
    matched_terms: list[str]


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.doc_ids: list[str] = []
        self.doc_len: dict[str, int] = {}
        self.term_freq: dict[str, Counter[str]] = {}
        self.postings: dict[str, set[str]] = defaultdict(set)
        self.idf: dict[str, float] = {}
        self.avg_len: float = 0.0

    # ── 构建 ────────────────────────────────────────────────────────────────
    def add(self, doc_id: str, text: str) -> None:
        tokens = tokenize(text)
        counter = Counter(tokens)
        self.doc_ids.append(doc_id)
        self.term_freq[doc_id] = counter
        self.doc_len[doc_id] = max(len(tokens), 1)
        for term in counter:
            self.postings[term].add(doc_id)

    def build(self) -> "BM25Index":
        total = len(self.doc_ids)
        if total == 0:
            self.avg_len = 1.0
            return self
        self.avg_len = sum(self.doc_len.values()) / total
        for term, docs in self.postings.items():
            df = len(docs)
            # 带 +0.5 平滑的 Robertson-Sparck Jones IDF，并对负值截断
            self.idf[term] = max(math.log(1.0 + (total - df + 0.5) / (df + 0.5)), 1e-6)
        return self

    # ── 检索 ────────────────────────────────────────────────────────────────
    def search(self, query: str, top_k: int = 10) -> list[ScoredDoc]:
        if not self.doc_ids:
            return []
        query_terms = [term for term in dict.fromkeys(tokenize(query)) if term in self.postings]
        if not query_terms:
            return []

        scores: dict[str, float] = defaultdict(float)
        matches: dict[str, list[str]] = defaultdict(list)

        for term in query_terms:
            idf = self.idf.get(term, 0.0)
            for doc_id in self.postings[term]:
                freq = self.term_freq[doc_id].get(term, 0)
                if freq == 0:
                    continue
                norm = 1.0 - self.b + self.b * (self.doc_len[doc_id] / self.avg_len)
                scores[doc_id] += idf * (freq * (self.k1 + 1.0)) / (freq + self.k1 * norm)
                matches[doc_id].append(term)

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]
        return [
            ScoredDoc(doc_id=doc_id, score=score, matched_terms=matches[doc_id][:8])
            for doc_id, score in ranked
        ]

    # ── 持久化 ──────────────────────────────────────────────────────────────
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            pickle.dump(self, handle, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def load(cls, path: Path) -> "BM25Index | None":
        if not path.exists():
            return None
        with path.open("rb") as handle:
            return pickle.load(handle)  # noqa: S301 - 索引文件由本服务自己产出
