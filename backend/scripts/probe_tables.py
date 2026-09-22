"""探测 PDF 里有没有表格（用 PyMuPDF 内置的 find_tables）。

顺便回答一个实际问题：我们的报纸 PDF 到底需不需要做表格抽取？
用法：python scripts/probe_tables.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

PDF_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "museum"


def main() -> int:
    print("=" * 70)
    print("表格探测")
    print("=" * 70)
    for path in sorted(PDF_DIR.glob("*.pdf")):
        with pymupdf.open(path) as doc:
            counts = []
            detail: list[str] = []
            for page_no, page in enumerate(doc, start=1):
                finder = page.find_tables()
                found = list(finder.tables)
                counts.append(len(found))
                for table in found:
                    cells = table.extract()
                    detail.append(
                        f"      p{page_no}: {len(cells)} 行 x "
                        f"{len(cells[0]) if cells else 0} 列, bbox={tuple(round(v) for v in table.bbox)}"
                    )
            print(f"\n  {path.name}: 每页表格数 {counts}")
            for line in detail[:5]:
                print(line)
            if not any(counts):
                print("      （无表格 —— 说明规则法在这些版面上没找到网格）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
