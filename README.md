# J.A.R.V.I.S — your computer's holographic assistant

A local-first voice & control assistant for **macOS, Windows and Linux** with a
sci-fi blue web interface. 39 skills: system control, files, memory, weather,
data analysis, ML, and a free-talking chat brain. **Nothing ever leaves your
machine** — no cloud, no telemetry. Every file JARVIS touches is *your* file,
on *your* computer.

## Requirements

| | macOS 12+ | Windows 10/11 | Linux (deb/rpm) |
|---|---|---|---|
| Voice control & hologram UI | ✓ | ✓ | ✓ |
| Open/quit apps, files, trash | ✓ | ✓ | ✓ |
| Volume / mute | ✓ | needs NirCmd | pactl/amixer |
| Screenshot, lock, sleep, stay-awake | ✓ | ✓ | ✓ (systemd) |
| System vitals & device profile | ✓ | ✓ | ✓ |
| Built-in TTS voice | `say` | PowerShell voice | espeak-ng / spd-say |
| Fish Audio "JARVIS" voice | ✓ | ✓ | ✓ (optional key) |

Python 3.9+ on any of them (`xcode-select --install` on a bare Mac,
python.org on Windows — tick *Add to PATH*, `apt install python3` on Linux).

## Install

macOS / Linux:

```bash
git clone https://github.com/lfthkenzie-debug/JARVIS.git && cd JARVIS && ./install.sh
```

Or let the one-liner do it (after the repo is public):

```bash
curl -fsSL https://raw.githubusercontent.com/lfthkenzie-debug/JARVIS/main/install.sh | JARVIS_REPO=https://github.com/lfthkenzie-debug/JARVIS.git bash
```

Windows: clone (or download ZIP), then double-click **`install.bat`**.

`install.sh` creates a private virtualenv, installs dependency tiers,
prefetches language models, and trains JARVIS's intent router. Packages that
don't exist for your Python version are skipped — JARVIS degrades gracefully.

## Run

| Command (macOS/Linux) | Command (Windows) | What you get |
|---|---|---|
| `./run.sh --ui` | `run.bat --ui` | Holographic control panel (http://127.0.0.1:8777) |
| `./run.sh` | `run.bat` | Full voice mode — say *"jarvis"* |
| `./run.sh --text` | `run.bat --text` | Typed conversation, spoken replies |
| `./run.sh --selftest` | `run.bat --selftest` | Verify all skill routing (39 skills) |
| `./run.sh --oneshot "open safari"` | `run.bat --oneshot "open notepad"` | One command, then exit |

Grant **microphone** permission when your OS asks — the app only appears in
the privacy list after its first mic attempt.

## Optional power-ups

- **Chat brain** — [Ollama](https://ollama.com): `brew install ollama` (macOS),
  `winget install Ollama.Ollama` (Windows) or the install script at ollama.com
  (Linux), then `ollama pull llama3.2`. Without it, JARVIS
  still routes and executes every command, just answers open chit-chat dryly.
  Prefer the cloud: `export JARVIS_OPENAI_KEY=sk-…`
- **The real JARVIS voice** — a British baritone via Fish Audio: put your key
  in `jarvis_data/fish_api_key.txt`. Without a key he uses macOS `say`.

## Privacy

- All memory, models, and logs live in `jarvis_data/`, which is git-ignored
  and never uploaded anywhere.
- The web UI binds to `127.0.0.1` only.
- The interface has a **PRIVACY** toggle (or `?privacy=1`): masks vitals,
  hostname, serial, and locks File Access — for screen shares and demos.
- *"Clear screen"* wipes the chat display; JARVIS's memory vault keeps the
  transcript so he can still answer *"what did I ask earlier?"*

## Troubleshooting

- **`No such file or directory: ./run.sh`** → you're outside the folder;
  `cd` into the clone first.
- **NLTK/model downloads fail with SSL errors** → handled automatically via
  certifi; if a corporate proxy blocks it, run on a normal network once.
- **Port 8777 busy** → `JARVIS_UI_PORT=8899 ./run.sh --ui`
- **Voice silent** → check output device; Fish Audio falls back to `say`
  on any API error.

Built with stdlib-only web stack (no Node, no frameworks) — Python 3, SQLite,
scikit-learn, optional PyTorch.
