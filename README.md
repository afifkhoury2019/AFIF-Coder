# AFIF Coder

A Claude-Code-style coding assistant for Windows 10/11 that runs entirely on your own PC. It comes as one installer with its own built-in AI engine (AFIF Engine), so there's nothing else to install, no account, no subscription and no usage limit. Your code never leaves your computer.

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
| 32 GB+ RAM | Qwen3 Coder 30B-A3B (18.6 GB) |

You can also add any `.gguf` model file you already have.

## Using it

- **Open folder** picks the project. Recent folders are listed under the folder name in the header.
- Type a task and press Enter. Shift+Enter adds a new line. Esc or **Stop** halts it.
- Each action appears as a row (List, Read, Edit, Run…). Click **Show output** to see what it returned.
- **Allow edits and commands without asking** skips approval for the current session.
- Put a `CODER.md` file in a project root with standing instructions (coding style, how to build, what not to touch). It's loaded into every new chat.
- **Settings**: graphics card on/off, context length, temperature, step limit, theme.

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
