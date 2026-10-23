---
name: afif-ui-check
description: Test a web UI (an app's assets/www, an HTML page, an Electron renderer) headlessly with the bundled Playwright + Chromium, mocking native bridges, catching JavaScript errors and taking phone-size screenshots in light and dark mode to inspect with view_image. Use after building or changing any HTML/CSS/JS interface.
---

# Headless UI check (playbook §8)

Bundled Python has **playwright**, and Chromium is already installed (PLAYWRIGHT_BROWSERS_PATH is set).
Save this as `tools/ui_check.py` in the project, adapt the mock, and run it with run_command.

```python
import asyncio, json, sys, pathlib
from playwright.async_api import async_playwright

PAGE = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "app/src/main/assets/www/index.html").resolve()
OUT = pathlib.Path("ui-shots"); OUT.mkdir(exist_ok=True)
MOCK = {  # url -> response text the fake bridge returns
  "https://feeds.bbci.co.uk/arabic/rss.xml": "<rss><channel><item><title>خبر تجريبي</title>"
      "<link>https://example.com/a</link><pubDate>Thu, 22 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>",
}
BRIDGE = """window.Native = {
  fetch(id, url) { const x = (window.__MOCK||{})[url]; setTimeout(() => window.__nf(id, !!x, x || '404'), 150) },
  openArticle(u) { console.log('OPEN ' + u) }, share() {}, setStatusBar() {}, haptic() {} };"""

async def main():
    errors = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for scheme in ("light", "dark"):
            pg = await b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, color_scheme=scheme)
            pg.on("pageerror", lambda e: errors.append(f"{scheme}: {e}"))
            pg.on("console", lambda m: m.type == "error" and errors.append(f"{scheme} console: {m.text}"))
            await pg.add_init_script(f"window.__MOCK = {json.dumps(MOCK)};" + BRIDGE)
            await pg.goto(PAGE.as_uri()); await pg.wait_for_timeout(2500)
            await pg.screenshot(path=OUT / f"home-{scheme}.png")
            # interact: await pg.click("text=Settings"); await pg.screenshot(path=OUT / f"settings-{scheme}.png")
        await b.close()
    print("JS errors:", errors or "none")
    sys.exit(1 if errors else 0)

asyncio.run(main())
```

Then:
1. `python tools/ui_check.py path/to/index.html` - must print `JS errors: none`.
2. `view_image ui-shots/home-light.png` and `ui-shots/home-dark.png`; check layout, RTL, contrast,
   cut-off text, empty states. Fix and repeat.
3. Before pushing JavaScript, also run `node --check app.js`.
4. Note in PROGRESS.md what was checked and what still needs a real device.

If Chromium is missing (custom install without it), run `python -m playwright install chromium` once.
