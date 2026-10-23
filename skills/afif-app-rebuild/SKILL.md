---
name: afif-app-rebuild
description: AFIF-TECH method for building an app or program "like" an existing one (clone/rebuild) with original code and AFIF-TECH branding, building and releasing it through GitHub Actions, and using REA to analyse the owner's own APK/EXE/Electron apps. Use for any request to clone, rebuild, recreate or make an app like another one, or to build and ship an Android APK or Windows program.
---

# Running this playbook inside AFIF Coder

This is Eng. Afif Khoury's playbook. Follow it exactly; the notes below only adapt it to AFIF Coder,
which runs on the owner's own Windows PC (not in a restricted cloud sandbox).

- **Start by reading `.afif/PROGRESS.md`** (it is also in your instructions). Continue from its "Next steps".
  Keep it current with `update_progress` after every milestone: spec written, repo created, first push,
  CI result, fixes, release. Another model may take over from you at any point and will rely on it.
- **GitHub (§1 step 3, §9):** if GitHub is connected, create the repo yourself instead of asking:
  `gh repo create afifkhoury2019/<APP-NAME> --private --source . --remote origin --push`.
  Follow builds with `gh run watch <id> --exit-status` (run_command, timeout_seconds 3600) and read
  failures with `gh run view <id> --log-failed`. Logs and artifacts are readable directly here, so the
  sandbox workarounds in §9.4 and §10 (feed-report branch, curl to the API) are optional.
  Download a release: `gh release download <tag> -p "*.apk" -D dist`.
- **Analysing the target (§2):** use `web_search` and `web_fetch` on the store listing and public pages.
  Check data sources with `web_fetch` before relying on them.
- **Local testing (§8):** use the bundled Python + Playwright (Chromium is installed). Load the
  `afif-ui-check` skill for a ready script, save screenshots inside the project, then look at them
  with `view_image` (models that can see images get the picture).
- **REA (§13):** REA is bundled and set up by AFIF Coder (Node, JDK 21, the pinned JADX JAR).
  Load the `reverse-engineer-anything` skill; that connects the REA tools automatically. Do not run
  `rea setup` or `doctor`. Owner's own apps only, as §0 says.
- **Google Drive:** if connected, use `drive_list` / `drive_download` to fetch the owner's brand kit,
  assets or old builds, and `drive_upload` to deliver a finished APK/EXE when asked.
- **Learning:** when you hit a new pitfall and fix it (a broken feed, a build error, an RTL bug), record it
  with `record_lesson` on this skill so the next model avoids it.
- **Approvals:** edits and commands may need the owner's approval. Explain briefly what each one does.
- Work in focused steps; one model can't hold a whole large app at once. Small local models should do
  one file or one fix at a time and leave clear notes in PROGRESS.md.

---

# AFIF-TECH — AI App Rebuild Playbook

> A guide for any AI assistant (Claude, ChatGPT, Gemini, Copilot, etc.) asked by **Eng. Afif Khoury / AFIF-TECH** to build an app "like" an existing one and deliver it through GitHub.
> Worked example: **AFIF News** (Arabic news aggregator, inspired by the *Nabd* app) — repo `afifkhoury2019/AFIF-News`.
> Written 22 Oct 2026 from a real build session.

---

## 0. Ground rules (read first)

1. **Rebuild, don't copy.** "Clone this app" means *reproduce the functionality and user experience with original code*. Never copy the target's code, images, icons, logo, name, fonts or text. Don't put the original brand name in the app or listing.
2. **Brand everything as AFIF-TECH.** Use the owner's logo and colours (§3), and give the app its own name (e.g. *AFIF News*, *AFIF Tank*, *AFIF-PBX*).
3. **Reverse-engineering scope.**
   - **The owner's own apps** (e.g. his MIT App Inventor builds): fully allowed. Decompile with `jadx` / `androguard` to recover topics, payloads and UI logic, then rewrite natively.
   - **Third-party apps:** study only public behaviour — the store listing, screenshots, features seen while using the app, and public data sources (RSS, open APIs). Do not decompile or extract their assets or private APIs.
   - **Tooling:** use the **REA (Reverse Engineer Anything)** skill for evidence-based analysis (§13). Its own disclaimer matches these rules: lawful analysis of software you own or are authorised to examine, and never copying proprietary code or assets.
4. **Content must come from legitimate public sources.** For news, use publishers' official RSS/Atom feeds and Google News RSS, and link every story back to the publisher.
5. **Build in GitHub, not locally.** AI sandboxes usually can't download the Android SDK or Gradle. Push the code to the owner's GitHub and let **GitHub Actions** build, sign and publish the APK as a Release. This is the owner's standing preference.

---

## 1. The workflow at a glance

```
Request ("clone app X with my branding + rendering effects")
  │
  ├─ 1. Analyse the target   → feature list from store page / public behaviour
  ├─ 2. Write the spec        → screens, data sources, effects, brand
  ├─ 3. Ask for an empty repo → owner creates  afifkhoury2019/<APP-NAME>  (private)
  ├─ 4. Build                 → native shell + web UI + native bridge
  ├─ 5. Test locally          → headless Chromium + mocked bridge, screenshots
  ├─ 6. Push → GitHub Actions → health checks + signed APK + Release
  ├─ 7. Read CI results       → fix dead sources / build errors, push again
  └─ 8. Deliver               → APK file + short summary + next step
```

Start building **immediately** while waiting for the repo. Ask for the repo in the first message, because an AI usually can't create repos itself.

---

## 2. Step 1–2: Analyse the target and write the spec

From the store listing and the app's public behaviour, list:

| Area | AFIF News example |
|---|---|
| Core purpose | Aggregate Arabic news from many publishers in one feed |
| Navigation | Category tabs on top, bottom nav: News / Sources / Saved / Settings |
| Content | Hero carousel, story cards (image, title, source, time-ago), breaking ticker |
| User controls | Follow/unfollow sources, add custom RSS, save stories, search, share |
| Personalisation | Light/dark/auto, 6 brand palettes, 4 font sizes, refresh interval |
| Reading | In-app reader with progress bar, share, open in browser |
| Locale | Arabic, RTL, Jordan focus |

Then decide the **rendering effects** (§6) and the **data sources** (§7).

---

## 3. AFIF-TECH brand kit

| Token | Value |
|---|---|
| Navy (primary) | `#1E2D80` |
| Sky blue (accent) | `#1E96D2` |
| Night (deep bg) | `#070B24` |
| Wordmark | `AFIF-TECH`; app names `AFIF <Product>` |
| Arabic font | **Cairo** (bundled from npm `@fontsource/cairo`, woff2) |
| Mark used in AFIF News | Stroked "A" with a sky-blue dot at the apex, plus signal rings |
| Extra palettes | emerald `#0E5E4E/#20C997`, crimson `#7A1028/#F0545C`, amber `#7A4A05/#F5A524`, violet `#3B1E80/#9B6BFF`, graphite `#22262E/#5BA7FF` |

If the owner uploads his official Brand & Design Kit (logo PNG/SVG, six themes, design tokens), use that. It takes priority over this table.

---

## 4. Architecture that works well for "clone" apps

**Kotlin native shell + bundled HTML/CSS/JS UI + JavaScript bridge.** There are no AndroidX dependencies, so builds stay fast and rarely break.

```
app/
 ├─ build.gradle.kts            AGP 8.5.2, Kotlin 1.9.24, compileSdk 34, minSdk 26
 ├─ afif-news.jks               release keystore (private repo only; better: GitHub Secret)
 └─ src/main/
     ├─ AndroidManifest.xml     INTERNET, cleartext allowed, two activities
     ├─ assets/www/             index.html · app.css · app.js · sources.js · fonts/
     ├─ java/com/afiftech/news/
     │    ├─ MainActivity.kt    WebView host + Bridge (fetch, openArticle, share, setStatusBar, haptic)
     │    ├─ Net.kt             HttpURLConnection fetcher (redirects, charset, byte cap) + Share
     │    └─ ReaderActivity.kt  branded reader, GlowBar progress, pulsing logo loader
     └─ res/                    adaptive vector icon, themes (+ Android 12 splash), slide/scale anims
```

**Why a native fetch bridge?** A WebView page can't fetch most RSS feeds because of CORS. The page calls `Native.fetch(id, url, maxBytes)`, Kotlin downloads the feed on a thread pool, and returns the result with `window.__nf(id, ok, text)`.

```kotlin
@JavascriptInterface
fun fetch(id: Int, url: String, maxBytes: Int) {
    pool.execute {
        try { val t = Net.getText(url, maxBytes)
              js("window.__nf($id,true,${JSONObject.quote(t)})") }
        catch (e: Exception) { js("window.__nf($id,false,${JSONObject.quote(e.message ?: "error")})") }
    }
}
```

```js
const pending = new Map(); let seq = 0;
window.__nf = (id, ok, text) => { const p = pending.get(id); if (!p) return;
  pending.delete(id); ok ? p.res(text) : p.rej(new Error(text)); };
const getText = (url, max = 0) => new Promise((res, rej) => {
  const id = ++seq; pending.set(id, { res, rej }); Native.fetch(id, url, max); });
```

Other bridge methods include:
- `openArticle(url, title, source, external, brandColor)`
- `share(title, url)`
- `setStatusBar(color, dark)`
- `haptic()`
- a back button that calls `window.__back()` and returns `true` if the web UI handled it

The same page also runs in a desktop browser with demo data, which makes previews and automated tests easy.

Other proven stacks used for AFIF-TECH projects:
- Native Kotlin with custom-drawn Views (VibeFM, AFIF Tank)
- Electron or native Windows apps built in Actions (AFIF PDF Pro, AFIF-PBX)
- iOS `.ipa` built on GitHub's macOS runners (VibeFM-iOS)

---

## 5. Parsing feeds (RSS 2.0 / Atom / RDF) — key points

- Parse with `DOMParser(xml, 'application/xml')`. If that fails, escape stray `&` and retry.
- Find items with `getElementsByTagName('item')` + `('entry')`. Get the link from `<link>` text, or for Atom from `<link rel="alternate" href>`.
- Pick the image in this order: `media:content` / `media:thumbnail` (largest width) → `enclosure[type^=image]` → first `<img>` in `content:encoded` / `description`.
- Missing image: when the card scrolls into view, fetch the first ~150 KB of the article and read `og:image` / `twitter:image`. Cache the result.
- **Google News RSS:** titles end in ` - Source`. Strip that, and use `<source url>` for the publisher name and favicon.
- Remove duplicates by normalised title, and sort by `pubDate`.
- Show favicons with `https://www.google.com/s2/favicons?domain=<host>&sz=64`.
- Arabic "time ago" needs proper plurals: دقيقة / دقيقتين / 3–10 دقائق / 11+ دقيقة.

---

## 6. Rendering-effects catalogue ("make it look premium")

| Effect | How |
|---|---|
| Animated splash | SVG logo drawn with `stroke-dashoffset`, dot pop-in, 3 expanding signal rings, then zoom-fade out |
| Android 12 splash | `windowSplashScreenBackground` + animated icon in `values-v31/themes.xml` |
| Hero carousel with parallax | `transform` slides, Ken-Burns scale on the current slide, gyroscope `deviceorientation` → `--px`, scroll offset → `translate` |
| Staggered card entrance | `@keyframes cardIn` with `animation-delay: calc(var(--i)*55ms)` and a spring-like cubic-bezier |
| Shimmer skeletons | Moving `linear-gradient` background while loading |
| Blur-up images | Start at `blur(12px) scale(1.15)`, remove the blur on load |
| Ripple | Span placed at the touch point, scaled ×4 and faded |
| Sliding glow tab indicator | Absolutely positioned "ink" bar with an overshoot cubic-bezier and `box-shadow` glow |
| Swipe between categories | Horizontal touch gesture → slide-out / slide-in of the feed |
| Pull-to-refresh | Custom spinner that follows the finger and rotates while loading |
| Circular-reveal search | `clip-path: circle(0%) → circle(150%)` |
| Glass bottom nav | `backdrop-filter: blur(18px) saturate(1.6)`, icon lift on select |
| Breaking ticker | Duplicated content + marquee keyframes, blinking "live" dot |
| Native activity transitions | `windowAnimationStyle` with slide + scale XML anims |
| Reader progress | Custom `GlowBar` View: gradient + travelling highlight; logo pulse loader fades into page |

Always add `@media (prefers-reduced-motion: reduce)`.

---

## 7. Data sources used (all verified by CI on 22 Oct 2026)

- **Google News RSS (ar, JO):**
  - Top headlines: `https://news.google.com/rss?hl=ar&gl=JO&ceid=JO:ar`
  - Topics `WORLD, BUSINESS, SPORTS, TECHNOLOGY, HEALTH, SCIENCE, ENTERTAINMENT`: `https://news.google.com/rss/headlines/section/topic/<TOPIC>?hl=ar&gl=JO&ceid=JO:ar`
  - Jordan: the `NATION` topic returned only 1 item, so use a search feed instead: `https://news.google.com/rss/search?q=الأردن+when:2d&hl=ar&gl=JO&ceid=JO:ar` (URL-encoded)
- **Publishers:**
  - BBC Arabic `feeds.bbci.co.uk/arabic/rss.xml`
  - DW `rss.dw.com/xml/rss-ar-all`
  - France 24 `france24.com/ar/rss`
  - RT `arabic.rt.com/rss/`
  - CNN Arabic `arabic.cnn.com/api/v1/rss/rss.xml`
  - Euronews `arabic.euronews.com/rss`
  - Sky News Arabia `skynewsarabia.com/web/rss`
  - Al Jazeera (aljazeerarss URL)
  - Roya `royanews.tv/rss`
  - AIT News `aitnews.com/feed/`
  - Tech-WD `tech-wd.com/wd/feed/`
- **Removed:** Ammon `rss.php` and Al Rai `rss.php` return HTML with 0 items.

---

## 8. Local testing without an Android SDK

Use Playwright + Chromium. Inject a fake `window.Native` that serves mock RSS strings, including `media:thumbnail` items with data-URI SVG images, a Google-News-style feed and an Atom feed. Then screenshot at 390×844 in light and dark mode:

```python
await page.add_init_script("""
  window.Native = { fetch(id,url){ const x = MOCK[url]; setTimeout(()=>__nf(id,!!x,x||'404'),200) },
                    openArticle(u){ console.log('OPEN '+u) }, setStatusBar(){}, haptic(){}, share(){} };""")
await page.goto('file:///…/assets/www/index.html')
```

Check for these:
- No `pageerror`
- Cards render and the hero shows
- Tabs, sources and settings work
- RTL layout is correct
- Run `node --check app.js` before every push

---

## 9. GitHub pipeline

### 9.1 Repo
The owner creates an **empty private** repo, e.g. `afifkhoury2019/<APP-NAME>`. The AI pushes `main`.

### 9.2 Workflow `.github/workflows/build.yml`
Two jobs run on every push to `main`:

1. **Health check:** a Python script reads `sources.js`, fetches every feed with a mobile User-Agent, and counts items and images. It writes a Markdown table to the run summary **and** force-pushes it to an orphan branch `feed-report`. That lets an AI read the result through the contents API (`/contents/feed-report.md?ref=feed-report`) even when log and artifact downloads are blocked by its sandbox.
2. **APK build:**
   - `actions/setup-java@v4` (Temurin 17)
   - `gradle/actions/setup-gradle@v4` with `gradle-version: 8.9`, no wrapper jar needed
   - `gradle :app:assembleRelease`
   - rename to `AFIF-News-v1.0.<run>.apk`
   - upload the artifact, then `gh release create v1.0.<run> … --latest` using `GITHUB_TOKEN` with `permissions: contents: write`

`versionCode` = `GITHUB_RUN_NUMBER`, so every build installs as an update.

### 9.3 Signing
- Generate once:
  ```
  keytool -genkeypair -keystore app/afif-news.jks -alias afifnews -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Afif Khoury, OU=AFIF-TECH, O=AFIF-TECH, L=Amman, C=JO"
  ```
- The same key must sign every version, or updates won't install.
- **Recommended:** store the keystore as a base64 GitHub Secret plus a password secret, and decode it in the workflow. Only commit the keystore to a **private** repo. Never share the passwords in chat logs or docs.

### 9.4 Reading results from an AI sandbox
- Poll `GET /repos/<o>/<r>/actions/runs?head_sha=<sha>` until `status == completed`.
- Download the APK with `GET /repos/<o>/<r>/releases/assets/<id>` and header `Accept: application/octet-stream`.
- Job logs and artifacts redirect to blob storage, which is often blocked, so publish anything you need to read to a branch.

---

## 10. Pitfalls found in this build (and fixes)

| Problem | Fix |
|---|---|
| Sandbox can't reach `dl.google.com` / Maven Google | Build only in GitHub Actions |
| `gh` CLI token invalid in sandbox | Use the git proxy for push and plain `curl` to `api.github.com` |
| RTL tab indicator misplaced inside a scroll container | Wrap the tabs in an inline-flex `.strip` with `position:relative` and measure `offsetLeft` there; scroll with `scrollIntoView({inline:'center'})` |
| RTL marquee scrolled out of view | Animate `translateX(0) → translateX(50%)`, not `-50% → 0` |
| `animation-fill-mode: forwards` blocked the `:active` press scale | On `animationend`, swap class `in` → `done` |
| Background-image URL broken by escaped quotes | Percent-encode `'` and `"` before HTML-escaping |
| Swipe detected while scrolling vertically | Track touch start Y separately from the pull-to-refresh Y |
| Google "NATION" topic nearly empty for Jordan | Use the Google News search feed |
| Some `rss.php` URLs return HTML | CI health check catches them; remove or replace |

---

## 11. Prompt template for another AI

```
You are building an Android app for AFIF-TECH (owner: Eng. Afif Khoury).
Target to rebuild (functionality only, original code, no copied assets/names): <store URL>
Brand: AFIF-TECH — navy #1E2D80, sky #1E96D2, Cairo font, app name "AFIF <Product>".
Must include premium rendering effects (see the catalogue in docs/AI-APP-BUILD-PLAYBOOK.md §6).
Architecture: Kotlin WebView shell + assets/www UI + JS↔native bridge (§4), no AndroidX deps.
Build ONLY via GitHub Actions in repo afifkhoury2019/<APP-NAME>; publish a signed APK as a Release (§9).
Add a CI health check for any external data source and publish its report to a branch.
Test the UI locally with Playwright + a mocked bridge before pushing (§8).
If reverse engineering is needed (owner's own APK/EXE/ASAR), use the REA skill and MCP server (§13); report findings with REA's Evidence and unknowns.
Deliver: the APK, a 3–5 line summary, and one next step.
```

---

## 12. Delivery checklist

- [ ] Original name, logo and colours; no target-brand assets
- [ ] Feature parity list covered (§2)
- [ ] Effects implemented and reduced-motion respected
- [ ] RTL/Arabic checked (if relevant)
- [ ] Local screenshots in light and dark mode, with no JS errors
- [ ] CI green, health report clean, signed APK released
- [ ] APK sent to the owner; repo private if the keystore is committed
- [ ] Note what wasn't tested on a real device
- [ ] If REA was used: Evidence and unknowns attached, owner-authorised target only

---

## 13. Reverse-engineering toolkit: REA ("Reverse Engineer Anything")

**Repo:** https://github.com/morluto/rea (MIT licence; third-party engines keep their own licences)
**Skill:** `reverse-engineer-anything` in `.agents/skills/`, plus `rea-analysis-optimization`
**What it is:** an MCP server + CLI that lets an AI agent inspect shipped software and return findings with **Evidence** (artifact digests, locations, coverage, unknowns). It covers:
- Android APKs (via JADX)
- JavaScript/Electron apps and ASAR packages
- native binaries (Ghidra / Hopper / IDA / Rizin / objdump)
- .NET assemblies, PyInstaller executables, Flutter apps
- firmware (Binwalk / Unblob), packet captures (TShark), SQLite databases
- websites and passive runtime observation

### 13.1 Install (on the owner's PC, not inside a chat sandbox)
REA needs local tools and an MCP-capable agent such as Claude Code, Claude Desktop, Codex, Cursor, Gemini CLI, Windsurf or VS Code.
```bash
# 1. Read-only diagnosis (change the client id to match your agent)
npx -y rea-agents@latest doctor --client claude_code --json
# 2. Preview the setup plan, then apply it
npx -y rea-agents@latest setup --client claude_code --dry-run --json
npx -y rea-agents@latest setup --client claude_code --yes
# 3. Restart the agent and check that the REA tools appear
```
- **Prerequisites:** Node.js 22+ and a full JDK 17+.
- **Android:** you must supply the headless **JADX MCP JAR 0.7.1** yourself and point `REA_JADX_MCP_JAR` to its absolute path. REA setup does not install Java, JADX, Ghidra or IDA.
- **Commercial engines** (IDA, Hopper, JEB) must be properly licensed.
- **Without MCP:** for a JavaScript/Electron tree or ASAR, the CLI works directly:
  ```bash
  npx -y rea-agents@latest analyze-javascript-application /abs/path/to/app --json
  ```

### 13.2 Android workflow (for the owner's own APKs, e.g. App Inventor builds)
1. `inspect_android_package` — package identity, manifest, permissions, coverage
2. `search_android_classes` → `inspect_android_class` → `inspect_android_method` — decompiled logic (MQTT topics, payloads, URLs, UI flow)
3. `trace_android_references` — who calls what (static only; reflection and dynamic loading stay *unknown*)
4. Write the findings into a spec (§2), then **rewrite natively** with AFIF-TECH branding and build via GitHub Actions (§9)

REA never launches the APK and needs no emulator. Treat decompiled text as static evidence, not proof of runtime behaviour.

### 13.3 Where REA fits in this playbook
| Situation | Use |
|---|---|
| Rebuilding the owner's own app (VibeFM, AFIF Tank, old App Inventor apps) | REA Android tools → spec → native rewrite |
| Comparing two versions of the owner's app | REA version comparison with Evidence |
| Owner's Electron/Windows tools (AFIF PDF Pro, AFIF ImgShrink) | `analyze_javascript_application`, `open_binary` |
| A third-party store app (e.g. Nabd) | **No decompiling.** Public behaviour and store listing only (§0) |
| A plain source repo | Normal repo tools; REA isn't needed |

### 13.4 Running REA through GitHub
The chat sandbox can't install JADX or Ghidra. If REA analysis must run without the owner's PC:
1. Add a workflow that installs Node 22, Temurin 17 and the JADX MCP JAR.
2. Have it run the REA CLI against an artifact committed to a **private** repo (the owner's own app only).
3. Push the JSON Evidence to a `rea-report` branch, using the same trick as `feed-report` in §9.2.

---
*AFIF-TECH · Eng. Afif Khoury · Amman, Jordan*
