---
name: afif-excel
description: Create or edit Excel workbooks (.xlsx) - schedules, calculations, BOQs, trackers - with openpyxl, using real formulas and AFIF-TECH styling. Use whenever the user wants a spreadsheet or to change an .xlsx/.csv.
---

# Excel workbooks (.xlsx)

Use the bundled Python with **openpyxl**. Write a script in the project, run it, verify.

## Rules
- Put calculations in **Excel formulas** (`=SUM(D2:D40)`, `=B2*C2`), not values computed in Python,
  so the sheet stays live when the user edits inputs. openpyxl does not calculate formulas; Excel does on open.
- Inputs in one place (an "Inputs" sheet or a clearly coloured block), results referencing them.
- Freeze the header row, set column widths, number formats, and add an autofilter on tables.
- Never overwrite the user's workbook unless asked; save as a new file.

## Building blocks
```python
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

NAVY, SKY = "1E2D80", "1E96D2"
wb = Workbook(); ws = wb.active; ws.title = "Cables"
head = ["Circuit", "Cable", "Qty", "Unit price (JOD)", "Total (JOD)"]
ws.append(head)
for c in ws[1]:
    c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor=NAVY)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
rows = [("TR1", "1x630 XLPE", 3, 42.5), ("TR2", "1x630 XLPE", 3, 42.5)]
for i, (a, b, q, u) in enumerate(rows, start=2):
    ws.append([a, b, q, u, f"=C{i}*D{i}"])
end = len(rows) + 1
ws.append(["", "", "", "Total", f"=SUM(E2:E{end})"])
ws.cell(end + 1, 5).font = Font(bold=True)
for col in ("D", "E"):
    for c in ws[col][1:]: c.number_format = '#,##0.00'
widths = [12, 22, 8, 16, 16]
for i, w in enumerate(widths, 1): ws.column_dimensions[get_column_letter(i)].width = w
ws.freeze_panes = "A2"; ws.auto_filter.ref = f"A1:E{end}"
wb.save("out/boq.xlsx")
```
- Read existing data: `load_workbook(p, data_only=True)` gives last-saved values; without
  `data_only` you get formulas.
- Charts: `from openpyxl.chart import BarChart, Reference`.
- CSV in, Excel out: read with the `csv` module, then style as above.

## Verify
Re-open the saved file and print the header row, row count and every formula cell in the totals
row; check references point at the right rows. State that values appear when Excel opens it.
