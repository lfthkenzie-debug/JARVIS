# J.A.R.V.I.S — your Mac's holographic assistant

A local-first voice & control assistant for macOS with a sci-fi blue web interface.
39 skills: system control, files, memory, weather, data analysis, ML, and a
free-talking chat brain. **Nothing ever leaves your machine** — no cloud, no
telemetry. Every file JARVIS touches is *your* file, on *your* laptop.

## Requirements

- macOS (JARVIS drives AppleScript, `say`, `pmset`, Homebrew)
- Python 3.9+ (`xcode-select --install` gets you python3 on a bare Mac)
- A microphone — optional, everything works from the keyboard too

## Install

```bash
git clone https://github.com/<your-org>/JARVIS.git && cd JARVIS && ./install.sh
```

Or let the one-liner do it (after the repo is public):

```bash
curl -fsSL https://raw.githubusercontent.com/<your-org>/JARVIS/main/install.sh | JARVIS_REPO=https://github.com/<your-org>/JARVIS.git bash
```

`install.sh` creates a private virtualenv, installs dependency tiers,
prefetches language models, and trains JARVIS's intent router. Packages that
don't exist for your Python version are skipped — JARVIS degrades gracefully.

## Run

| Command | What you get |
|---|---|
| `./run.sh --ui` | Holographic control panel in your browser (http://127.0.0.1:8777) |
| `./run.sh` | Full voice mode — say *"jarvis"* |
| `./run.sh --text` | Typed conversation, spoken replies |
| `./run.sh --selftest` | Verify all skill routing (39 skills) |
| `./run.sh --oneshot "open safari"` | One command, then exit |

Grant **Microphone** permission when macOS asks (Privacy & Security →
Microphone) — the app only appears in that list after its first mic attempt.

## Optional power-ups

- **Chat brain** — [Ollama](https://ollama.com): `brew install ollama &&
  brew services start ollama && ollama pull llama3.2`. Without it, JARVIS
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
