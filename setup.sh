#!/bin/bash
# JARVIS installer — tiered, tolerant of packages without wheels for this Python.
# Usage: ./setup.sh          (core + voice + data + web)
#        ./setup.sh --full   (also torch/transformers/whisper + big-data stacks)
set -uo pipefail
cd "$(dirname "$0")"

FULL=0
[[ "${1:-}" == "--full" ]] && FULL=1
PY=${PYTHON:-python3}

echo "▸ Python: $($PY --version)"
if [[ ! -d .venv ]]; then
  echo "▸ Creating virtualenv .venv"
  $PY -m venv .venv || { echo "venv failed"; exit 1; }
fi
source .venv/bin/activate
pip -q install --upgrade pip setuptools wheel

install_file() {   # required tier: fail loudly, keep going
  echo "▸ Installing $1"
  pip install -r "$1" || echo "  ⚠ some packages in $1 failed — JARVIS degrades gracefully."
}

try_pkg() {        # optional single package: skip if no wheel for this Python
  if pip install -q "$1" 2>/dev/null; then
    echo "  ✓ $1"
  else
    echo "  ✗ $1 unavailable for Python $(python -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")') — feature will run in fallback mode."
  fi
}

install_file requirements-core.txt
install_file requirements-web.txt

if [[ $FULL == 1 ]]; then
  echo "▸ Deep-learning tier"
  for p in torch transformers safetensors "spacy>=3.8" "nltk>=3.9" openai-whisper; do try_pkg "$p"; done
  echo "▸ Data / time-series tier"
  for p in "dask[dataframe]" distributed ray kafka-python pyspark sktime prophet kats; do try_pkg "$p"; done
fi

echo "▸ Language models (spaCy en_core_web_sm)"
python -m spacy download en_core_web_sm >/dev/null 2>&1 || echo "  ⚠ spaCy model download skipped."

echo "▸ NLTK data (punkt, stopwords, wordnet)"
python - <<'PY'
import jarvis.config  # installs certifi SSL context for the downloader
import nltk
for pkg in ("punkt", "punkt_tab", "stopwords", "wordnet"):
    nltk.download(pkg, quiet=True)
PY

echo "▸ Training JARVIS intent models"
python - <<'PY'
from jarvis.main import _boot
_boot()
PY

cat <<'EOF'

────────────────────────────────────────────────────────
 JARVIS is installed.
   ./run.sh                 voice mode (say "jarvis" …)
   ./run.sh --ui            holographic control panel in your browser
   ./run.sh --text          type commands
   ./run.sh --selftest      verify skill routing
 Grant microphone + accessibility permissions when macOS asks.
 Optional: JARVIS FISH AUDIO voice — put your API key in
 jarvis_data/fish_api_key.txt (or export JARVIS_FISH_API_KEY).
────────────────────────────────────────────────────────
EOF
