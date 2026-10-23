# AFIF Coder

A Claude-Code-style coding assistant for Windows 10/11. It comes as one installer with its own built-in AI engine (AFIF Engine), so local models need nothing else installed, no account and no usage limit, and your code never leaves your computer. When you want more power, connect Claude, Gemini, DeepSeek or any OpenAI-compatible service with your own API key.

It works inside one project folder and can list, read, search, create and edit files, and run commands (builds, tests, scripts). Edits and commands ask for your approval first, showing a diff or the exact command.

## Install

1. Download **AFIF-Coder-Setup-x.y.z.exe** from the [Releases](../../releases) page.
2. Run it. It installs for your user without admin rights (or choose "all users").
3. On first start, the **Models** window opens. Download the model it recommends for your PC. This happens once; after that AFIF Coder works offline.

| Your PC | Model |
|---|---|
| 8 GB RAM | Qwen2.5 Coder 3B (1.8 GB) |
| 16 GB RAM, or a 6–8 GB graphics card | Qwen2.5 Coder 7B (4.4 GB) |
| 24 GB+ RAM or a 12–16 GB graphics card | Qwen2.5 Coder 14B (8.4 GB) |
| 32 GB+ RAM | Qwen3 Coder 30B-A3B (17.3 GB) |

The **On this PC** tab also offers Llama 3.1, Qwen3, DeepSeek R1 and DeepSeek Coder V2 Lite, Google's Gemma 3, OpenAI's gpt-oss 20B and Mistral's Devstral. You can add any `.gguf` file you have, or paste a Hugging Face link to download one.

## Cloud models (Claude, Gemini, DeepSeek)

Open **Manage models… → Cloud**, paste your API key and click **Save and connect**. The provider's models then appear in the model list under "Cloud".

| Provider | Get a key | Cost |
|---|---|---|
| Claude (Anthropic) | platform.claude.com | Billed per use, separate from a Claude.ai subscription |
| Gemini (Google) | aistudio.google.com/apikey | Free tier with daily limits |
| DeepSeek | platform.deepseek.com | Billed per use, low prices |
| Other | OpenRouter, OpenAI, Groq, Mistral, LM Studio… | Depends on the service |

With a cloud model, your messages, attachments and the files the model reads are sent to that provider. The status bar shows the tokens each message used. Keys are stored encrypted for your Windows account.

Manus isn't included: it's a hosted agent that works on its own servers, so it can't edit files in your project folder.

## Attaching files

Click **Attach files**, drag files onto the window, or paste an image. Text and code files, PDF, Word (.docx) and Excel (.xlsx) are read into the message. Files over about 60,000 characters are also saved in the project's `.afif/attachments` folder so the model can read the rest. Images are sent to models that can see them (Claude, Gemini); local models get a note instead.

## Building and cloning apps (skills, GitHub, Drive, REA)

AFIF Coder 1.2 can follow the **AFIF-TECH App Rebuild playbook** end to end with any model:

- **Skills** (header → Skills): built-in skills for the App Rebuild playbook, REA reverse engineering,
  Word, Excel, PowerPoint, PDF and headless UI checks. A skill loads automatically when your message
  matches it ("clone…", "rebuild…", "decompile my APK…"), so even small models get the instructions.
  Tick **Always use in this project** to give a skill to every chat in a project. Add your own skill
  folders (a `SKILL.md` with `name` and `description`) from the same window.
- **Lessons learned:** when a model hits a pitfall or you correct it, it records the lesson on the skill.
  Every model sees those lessons the next time it uses the skill. Review or edit them under Skills → Lessons.
- **Continuing work:** each project keeps `.afif/PROGRESS.md` (goal, status, decisions, repo and builds,
  next steps), updated by the models. A new chat, a different model, or another AI tool reads it first.
  Reopening a project continues its last chat; **New chat** starts fresh (the old one is archived in `.afif/chats`).
- **Connections** (header → Connections): sign in to **GitHub** (browser code, via the bundled GitHub CLI)
  and **Google Drive** (browser approval, via the bundled rclone). Models can then create repos, push,
  follow Actions builds (`gh run watch`), download releases, and read or upload Drive files.
  **Open a repository…** clones one of your repos and opens it as the project.
- **REA:** bundled with Node.js, Java JDK 21 and the pinned JADX engine. It connects automatically when
  the REA skill loads. Use it only on apps you own or are authorised to analyse.
- **Bundled tools** (full install): Git, GitHub CLI, Node.js 24, Python 3.12 with python-docx, openpyxl,
  python-pptx, reportlab, pypdf and Playwright with Chromium, Java JDK 21, rclone. They're on PATH for
  every command the models run; nothing is added to your system PATH.
- **Web:** models can search the web and read pages (DuckDuckGo by default; add a Brave Search key in
  Connections for steadier results), and look at screenshots they take (Claude and Gemini).

## Using it

- **Open folder** picks the project. Recent folders are listed under the folder name in the header.
- Type a task and press Enter. Shift+Enter adds a new line. Esc or **Stop** halts it.
- Each action appears as a row (List, Read, Edit, Run…). Click **Show output** to see what it returned.
- **Allow edits and commands without asking** skips approval for the current session.
- Put a `CODER.md` file in a project root with standing instructions (coding style, how to build, what not to touch). It's loaded into every new chat.
- **Settings**: graphics card on/off, context length, temperature, step limit, theme.
- Choosing a cloud model unloads the local model to free your PC's memory.
- Commands can run for up to an hour with live output (builds, `gh run watch`); **Stop** ends them.
- **Speed** (next to the approval box): *Fast* makes models think less and answer sooner (good for chat
  and small edits), *Balanced* is the default, *Thorough* thinks deepest. While a model thinks, its
  thinking scrolls by in grey; the status bar shows how long the first words took.
- Local models get a shorter prompt and pre-read it while loading, so the first reply starts quickly.
- **Report** (header): a debug report of every message, model reply, tool step, model load and error, with
  timings (time to first words, engine reading speed, cache use) and full error details. Every error in
  the chat has a **Details** link to it. **Export report…** saves one zip to send when something is slow
  or fails. API keys and sign-in tokens are never recorded. Logs are kept 14 days.

The engine uses your graphics card through Vulkan (NVIDIA, AMD and Intel) and falls back to the CPU automatically if that doesn't work.

## Honest limits

A local 7B–14B model is noticeably weaker than Claude on large, multi-file tasks. It's best for focused jobs: fix this function, add a feature to one file, write a script, explain code. If it forgets files it read earlier, raise the context length in Settings (uses more memory) or start a new chat.

## Where things are

- App: `%LOCALAPPDATA%\Programs\AFIF-TECH\AFIF Coder` (per-user) or `C:\Program Files\AFIF-TECH\AFIF Coder`
- Models, settings, engine log: `%LOCALAPPDATA%\AFIF-Coder`

## Building

Every push to `main` runs `.github/workflows/build.yml` on GitHub's Windows machines. It fetches the engine, checks the model links, runs a real model through the agent, builds the app, self-tests it, builds the installer, test-installs and uninstalls it, then publishes a release.

Run from source: `pip install -r requirements.txt`, then set `AFIF_ENGINE` to a `llama-server` executable and run `python main.py`.

AFIF-TECH
