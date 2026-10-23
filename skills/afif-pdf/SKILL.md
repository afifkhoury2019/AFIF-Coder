---
name: afif-pdf
description: Create PDFs (reports, datasheets, certificates) with reportlab, and read, merge, split, rotate, extract text from or fill existing PDFs with pypdf. Use whenever the user wants a PDF made or changed.
---

# PDF files

Bundled Python has **reportlab** (create) and **pypdf** (read/modify).

## Create
```python
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet

NAVY, SKY = colors.HexColor("#1E2D80"), colors.HexColor("#1E96D2")
ss = getSampleStyleSheet(); ss["Title"].textColor = NAVY; ss["Heading2"].textColor = NAVY
doc = SimpleDocTemplate("out/report.pdf", pagesize=A4, leftMargin=2*cm, rightMargin=2*cm,
                        title="Report", author="AFIF-TECH")
story = [Paragraph("Earthing study - Al-Rama 132/33 kV", ss["Title"]), Spacer(1, 12),
         Paragraph("Results", ss["Heading2"])]
t = Table([["Item", "Value"], ["Grid resistance", "0.42 Ω"], ["Touch voltage", "312 V"]], colWidths=[8*cm, 6*cm])
t.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), NAVY), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                       ("GRID", (0,0), (-1,-1), 0.4, colors.grey), ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#EEF3FB")])]))
story.append(t)
def footer(c, d):
    c.setFont("Helvetica", 8); c.setFillColor(SKY); c.drawString(2*cm, 1.2*cm, f"AFIF-TECH  ·  page {d.page}")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
```
- **Arabic:** reportlab doesn't shape Arabic by itself. Register a TTF that has Arabic glyphs
  (`C:\Windows\Fonts\arial.ttf` via `pdfmetrics.registerFont(TTFont("Arial", path))`), and
  reshape + reorder text first if `arabic_reshaper` and `python-bidi` are available
  (`pip install arabic-reshaper python-bidi`). Otherwise make a Word file and export it.

## Modify (pypdf)
```python
from pypdf import PdfReader, PdfWriter
r = PdfReader("in.pdf"); w = PdfWriter()
for p in r.pages[:3]: w.add_page(p)          # split
w.pages[0].rotate(90)                         # rotate
w.write("out/first3.pdf")
text = "\n".join(p.extract_text() or "" for p in r.pages)       # extract text
m = PdfWriter(); [m.append(f) for f in ("a.pdf", "b.pdf")]; m.write("out/merged.pdf")   # merge
fields = r.get_fields()                                          # form fields
w = PdfWriter(clone_from="form.pdf"); w.update_page_form_field_values(w.pages[0], {"Name": "Afif"}); w.write("out/filled.pdf")
```
Scanned PDFs have no text layer; say so rather than guessing (OCR isn't bundled).

## Verify
Re-open the output with PdfReader: page count, and extracted text of page 1 contains the title.
