---
name: afif-app-rebuild
description: Short version of the AFIF-TECH app rebuild playbook, for models with small memory.
---

# AFIF-TECH app rebuild (short version)

Rules:
1. Rebuild, don't copy: same features and feel, original code. Never copy another app's code, images,
   icons, logo, name or text. Third-party apps: study public behaviour only, no decompiling.
   The owner's own apps may be decompiled with REA.
2. Brand everything AFIF-TECH: navy #1E2D80, sky blue #1E96D2, night #070B24, Arabic font Cairo,
   app name "AFIF <Product>".
3. Build in GitHub Actions, publish a signed release. Same signing key for every version.

Steps (one at a time, check each result):
1. List the target's features (screens, data, controls, settings, language/RTL).
2. Write a short spec into the project (SPEC.md).
3. Repo: `gh repo create afifkhoury2019/<APP-NAME> --private --source . --push` (if GitHub is connected).
4. Android architecture that works: Kotlin WebView shell + HTML/CSS/JS UI in assets/www + a JavaScript
   bridge (Native.fetch for network, share, openArticle, setStatusBar, haptic). No AndroidX.
   compileSdk 34, minSdk 26, AGP 8.5.2, Kotlin 1.9.24.
5. Premium effects: animated splash, staggered card entrance, shimmer loading, ripple, glass bottom bar,
   pull-to-refresh. Always respect prefers-reduced-motion.
6. Test the UI with Playwright (skill afif-playwright-ui-check) and look at the screenshots (view_image).
7. Workflow .github/workflows/build.yml: setup-java 17, setup-gradle (gradle 8.9), assembleRelease,
   upload, `gh release create v1.0.<run>`. versionCode = GITHUB_RUN_NUMBER.
8. Push, then `gh run watch <id> --exit-status`; on failure `gh run view <id> --log-failed`, fix, push again.
9. Deliver: the release file, a 3-5 line summary, one next step.

After each milestone call save_progress (goal, decisions, done, next, repo/release links).
