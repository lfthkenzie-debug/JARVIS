#!/bin/bash
# JARVIS — one-line installer for macOS.
#   curl -fsSL https://raw.githubusercontent.com/lfthkenzie-debug/JARVIS/main/install.sh | bash
# or simply:  git clone <repo> && cd <repo-dir> && ./install.sh
set -uo pipefail

REPO="${JARVIS_REPO:-}"                 # your public repo URL (or pass as $1)
[[ -n "${1:-}" ]] && REPO="$1"
TARGET_DIR="${JARVIS_HOME:-$HOME/JARVIS}"

echo "╔══════════════════════════════════════════╗"
echo "║   J.A.R.V.I.S — installer               ║"
echo "╚══════════════════════════════════════════╝"

# ---------------------------------------------------------------- guardrails
case "$(uname -s)" in
  Darwin) echo "▸ macOS detected — full feature set." ;;
  Linux)  echo "▸ Linux detected — system voice needs espeak-ng or spd-say." ;;
  *) echo "✗ This shell installer covers macOS and Linux. Windows: run install.bat instead."; exit 1 ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
  echo "✗ Python 3 not found. Install the Command Line Tools first:"
  echo "    xcode-select --install"
  exit 1
fi
PYVER=$(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if python3 -c 'import sys;sys.exit(0 if sys.version_info >= (3,9) else 1)'; then
  echo "▸ Python $PYVER ✓"
else
  echo "✗ JARVIS needs Python >= 3.9 (you have $PYVER)."
  echo "  brew install python@3.12   — or download from python.org"
  exit 1
fi

# ── fetch the code ──────────────────────────────────────────────────────────
HERE="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$HERE/setup.sh" && -d "$HERE/jarvis" ]]; then
  cd "$HERE"                            # already inside a clone
  echo "▸ Using existing checkout at $HERE"
elif [[ -n "$REPO" ]]; then
  echo "▸ Cloning $REPO → $TARGET_DIR"
  git clone --depth 1 "$REPO" "$TARGET_DIR" || exit 1
  cd "$TARGET_DIR"
else
  echo "✗ No repo URL. Either run this script from inside a JARVIS clone,"
  echo "  or set it:  JARVIS_REPO=https://github.com/lfthkenzie-debug/JARVIS.git bash install.sh"
  exit 1
fi

# ── install + train ─────────────────────────────────────────────────────────
./setup.sh || { echo "✗ setup.sh failed — scroll up for the culprit."; exit 1; }

cat <<EOF

▸▸ JARVIS is ready. First launch:
     ./run.sh --ui          holographic control panel (recommended)
     ./run.sh               full voice mode — say "jarvis"
     ./run.sh --text        typed conversation
   macOS will ask for Microphone permission on first voice use.
   Every file & command JARVIS touches stays on YOUR machine.
EOF
