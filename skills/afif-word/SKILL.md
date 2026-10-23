---
name: afif-word
description: Create or edit Word documents (.docx) - reports, specs, letters, tender documents - with python-docx, in AFIF-TECH style. Use whenever the user wants a Word file or to change a .docx.
---

# Word documents (.docx)

Use the bundled Python (`python` on PATH) with **python-docx**. Write a script file in the project
(e.g. `tools/make_doc.py`), run it with run_command, then verify.

## Workflow
1. Gather the content first (read files, attachments, Drive). Decide the outline.
2. Write the script; build the document from code, never by hand-editing XML unless needed.
3. Run it, then **verify**: re-open the file and print headings and the first lines of each section;
   check tables have the right row/column counts.
4. Tell the user the file path. Upload to Drive only if asked (`drive_upload`).

## Building blocks
```python
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

NAVY, SKY = RGBColor(0x1E, 0x2D, 0x80), RGBColor(0x1E, 0x96, 0xD2)
doc = Document()
for s in doc.sections:
    s.left_margin = s.right_margin = Cm(2.2); s.top_margin = s.bottom_margin = Cm(2)
st = doc.styles["Normal"]; st.font.name = "Calibri"; st.font.size = Pt(11)
for lvl in (1, 2):
    h = doc.styles[f"Heading {lvl}"]; h.font.color.rgb = NAVY; h.font.name = "Calibri"

doc.add_heading("Bayader 132/33 kV Substation - Cable Schedule", 0)
p = doc.add_paragraph("Prepared by AFIF-TECH"); p.runs[0].font.color.rgb = SKY
doc.add_heading("1. Scope", 1)
doc.add_paragraph("First point", style="List Bullet")
t = doc.add_table(rows=1, cols=3); t.style = "Light Grid Accent 1"; t.alignment = WD_TABLE_ALIGNMENT.CENTER
for c, txt in zip(t.rows[0].cells, ("Circuit", "Cable", "Length (m)")): c.text = txt
row = t.add_row().cells; row[0].text, row[1].text, row[2].text = "TR1", "1x630 mm² XLPE", "185"
doc.add_page_break()
doc.save("out/report.docx")
```
- Header/footer: `doc.sections[0].header.paragraphs[0].text = "AFIF-TECH"`.
- Images: `doc.add_picture("logo.png", width=Cm(4))`.
- **Arabic / right-to-left:** set the paragraph RTL and the complex-script font:
  ```python
  p = doc.add_paragraph("نص عربي"); p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
  p.paragraph_format.element.get_or_add_pPr().append(p.paragraph_format.element.makeelement(qn("w:bidi"), {}))
  r = p.runs[0]; r.font.rtl = True; r.font.cs_size = Pt(12)
  r._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:cs"), "Arial")
  ```
- Editing an existing .docx: open with `Document(path)`, change paragraphs/tables, save to a **new**
  file name unless the user asked to overwrite. Formatting lives in runs; replace text run by run.
- Page numbers, tables of contents and tracked changes need raw XML fields; keep them simple and
  tell the user Word will update the TOC on open (References > Update Table).
