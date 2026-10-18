#!/bin/bash
# JARVIS — one-line installer for macOS.
#   curl -fsSL https://raw.githubusercontent.com/lfthkenzie-debug/JARVIS/main/install.sh | bash
# or simply:  git clone <repo> && cd <repo-dir> && ./install.sh
set -uo pipefail

# Default repo lets `curl | bash` self-clone with no arguments.
REPO="${1:-${JARVIS_REPO:-https://github.com/lfthkenzie-debug/JARVIS.git}}"
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
ZIP="${JARVIS_ZIP:-}"
if [[ -f "$HERE/setup.sh" && -d "$HERE/jarvis" ]]; then
  cd "$HERE"                            # already inside a clone
  echo "▸ Using existing checkout at $HERE"
elif [[ -n "$ZIP" ]]; then
  # Laptop-mirror mode: grab a prebuilt bundle served over a shared URL, so
  # testers install the CURRENT build without waiting for a git push.
  echo "▸ Downloading installer bundle from $ZIP"
  TMP="$(mktemp -t jarvis.XXXXXX.zip)"
  curl -fsSL "$ZIP" -o "$TMP" || { echo "✗ bundle download failed"; exit 1; }
  PARENT="$(dirname "$TARGET_DIR")"; mkdir -p "$PARENT"
  unzip -q -o "$TMP" -d "$PARENT" || { echo "✗ could not unpack bundle"; exit 1; }
  rm -f "$TMP"
  # bundles are rooted at ./JARVIS; honour a custom JARVIS_HOME if set
  if [[ "$(basename "$TARGET_DIR")" != "JARVIS" && -d "$PARENT/JARVIS" ]]; then
    mv "$PARENT/JARVIS" "$TARGET_DIR"
  fi
  cd "$TARGET_DIR" || exit 1
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
