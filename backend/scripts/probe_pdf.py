"""探测 PDF：能不能直接抽到文字（数字版 vs 扫描件）。

这一步决定后面走哪条路，所以必须先做：
  - **有文字层** -> 直接页面级切分（NVIDIA 基准里页面级分块准确率最高、方差最低）
  - **无文字层（扫描件）** -> 必须先 OCR（中文用 PaddleOCR），
    否则 PyMuPDF 抽出来是一片空白 —— 而"空白"很容易被误读成"解析失败"，
    实际是 PDF 里根本没有文字层，只有一整页图。

顺带报告每页字数：用来判断有没有页眉页脚噪声、以及页面级分块后
每块大概多大（决定要不要再二次切分）。

用法：python scripts/probe_pdf.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

PDF_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "museum"


def probe(path: Path) -> None:
    with fitz.open(path) as doc:
        pages = len(doc)
        lengths: list[int] = []
        for page in doc:
            lengths.append(len(page.get_text().strip()))
        total = sum(lengths)
        print(f"\n=== {path.name} ===")
        print(f"  页数 {pages} | 总字数 {total} | 平均每页 {total // max(pages, 1)} 字")
        print(f"  每页字数: {lengths}")
        if total == 0:
            print("  ⚠️ 无文字层 = 扫描件/图片版，必须先 OCR")
            return
        empty = [i + 1 for i, n in enumerate(lengths) if n == 0]
        if empty:
            print(f"  空白页（无文字）: {empty}")
        first = doc[0].get_text().strip()
        print(f"  第 1 页开头 300 字:\n{'-' * 60}\n{first[:300]}\n{'-' * 60}")


def main() -> int:
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"目录内没有 PDF: {PDF_DIR}")
        return 0
    print("=" * 70)
    print(f"PDF 探测（共 {len(pdfs)} 份）")
    print("=" * 70)
    for path in pdfs:
        try:
            probe(path)
        except Exception as exc:  # noqa: BLE001
            print(f"\n=== {path.name} ===")
            print(f"  打开失败: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
