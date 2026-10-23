---
name: afif-powerpoint
description: Create or edit PowerPoint decks (.pptx) with python-pptx in AFIF-TECH style - project presentations, status updates, technical briefings. Use whenever the user wants slides or a .pptx changed.
---

# PowerPoint decks (.pptx)

Use the bundled Python with **python-pptx**. Plan the story first: one message per slide,
titles that state the point ("Cable route avoids the wadi crossing", not "Route").

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

NAVY, SKY, NIGHT = RGBColor(0x1E,0x2D,0x80), RGBColor(0x1E,0x96,0xD2), RGBColor(0x07,0x0B,0x24)
prs = Presentation(); prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)   # 16:9
blank = prs.slide_layouts[6]

def title_slide(title, sub):
    s = prs.slides.add_slide(blank)
    s.background.fill.solid(); s.background.fill.fore_color.rgb = NIGHT
    tb = s.shapes.add_textbox(Inches(0.8), Inches(2.6), Inches(11.5), Inches(1.4)).text_frame
    tb.text = title; tb.paragraphs[0].runs[0].font.size = Pt(44); tb.paragraphs[0].runs[0].font.bold = True
    tb.paragraphs[0].runs[0].font.color.rgb = RGBColor(255,255,255)
    p = tb.add_paragraph(); p.text = sub; p.runs[0].font.size = Pt(20); p.runs[0].font.color.rgb = SKY

def bullets(title, items):
    s = prs.slides.add_slide(blank)
    bar = s.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.12)); bar.fill.solid()
    bar.fill.fore_color.rgb = SKY; bar.line.fill.background()
    t = s.shapes.add_textbox(Inches(0.7), Inches(0.5), Inches(12), Inches(1)).text_frame
    t.text = title; r = t.paragraphs[0].runs[0]; r.font.size = Pt(30); r.font.bold = True; r.font.color.rgb = NAVY
    body = s.shapes.add_textbox(Inches(0.9), Inches(1.7), Inches(11.5), Inches(5)).text_frame
    body.word_wrap = True
    for i, it in enumerate(items):
        p = body.paragraphs[0] if i == 0 else body.add_paragraph()
        p.text = "•  " + it; p.runs[0].font.size = Pt(20); p.space_after = Pt(10)

title_slide("AFIF News", "Arabic news, one feed - AFIF-TECH")
bullets("What it does", ["Aggregates 15+ publishers", "Follows sources you choose", "Light and dark themes"])
prs.save("out/deck.pptx")
```
- Pictures: `s.shapes.add_picture("shot.png", Inches(7), Inches(1.6), height=Inches(5))`.
- Tables: `s.shapes.add_table(rows, cols, x, y, w, h).table`.
- Keep text inside the slide (about 6 bullets max, 20 pt+). Use screenshots from the app for demos.
- Editing: open `Presentation(path)`, change text in `shape.text_frame`, save under a new name.

## Verify
Re-open the deck and print each slide's title and text; check no textbox extends past
`prs.slide_width`/`slide_height`. If PowerPoint or LibreOffice is installed, you can export to PDF
for a visual check (`soffice --headless --convert-to pdf`), then render a page and `view_image` it.
