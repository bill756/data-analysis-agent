"""Generate the example data files under examples/ (sales semantics, Chinese headers).

Usage: python scripts/make_examples.py
Dependencies: openpyxl (xlsx), xlwt (xls)
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from openpyxl import Workbook

ROWS = [
    ("研发", 500, "2026-01"),
    ("市场", 300, "2026-01"),
    ("研发", 1300, "2026-02"),
    ("市场", 800, "2026-02"),
    ("销售", 200, "2026-02"),
    ("研发", 400, "2026-03"),
    ("市场", 700, "2026-03"),
]
HEADERS = ["部门", "金额", "月份"]

ROOT = Path(__file__).resolve().parent.parent / "examples"
ROOT.mkdir(exist_ok=True)


def main() -> None:
    # csv: comma-separated, utf-8-sig (Excel-friendly)
    with (ROOT / "sales.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADERS)
        writer.writerows(ROWS)

    # txt: tab-separated
    with (ROOT / "sales.txt").open("w", encoding="utf-8") as fh:
        fh.write("\t".join(HEADERS) + "\n")
        fh.writelines("\t".join(map(str, row)) + "\n" for row in ROWS)

    # json: array of objects
    records = [dict(zip(HEADERS, row)) for row in ROWS]
    (ROOT / "sales.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    # xlsx
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "销售数据"
    sheet.append(HEADERS)
    for row in ROWS:
        sheet.append(row)
    workbook.save(ROOT / "sales.xlsx")

    # xls (legacy binary, via xlwt)
    import xlwt

    book = xlwt.Workbook()
    sheet = book.add_sheet("销售数据")
    for r, row in enumerate([HEADERS, *ROWS]):
        for c, value in enumerate(row):
            sheet.write(r, c, value)
    book.save(ROOT / "sales.xls")

    for path in sorted(ROOT.iterdir()):
        print(path.name, path.stat().st_size)


if __name__ == "__main__":
    main()
