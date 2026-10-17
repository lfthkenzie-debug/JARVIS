"""JARVIS configuration — single source of truth for paths, voice, and behavior."""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------- identity
ASSISTANT_NAME = "JARVIS"
WAKE_WORDS = ["jarvis", "jarvi", "jarvis stop", "yes sir", "hey jarvis"]
TTS_RATE = 188            # words per minute for macOS `say`
TTS_VOICE = os.getenv("JARVIS_VOICE", "Daniel")   # calm British male
PERSONA = (
    "You are JARVIS: sharp, calm, and unmistakably yourself — dry British wit, "
    "genuinely opinionated, comfortable disagreeing. You answer anything: "
    "questions, statements, opinions, feelings, small talk — you may riff, "
    "tease, philosophize, or volunteer a thought whenever you feel like it. "
    "Never sycophantic, never robotic. Keep replies conversational "
    "(usually 2-6 sentences; longer only when the topic earns it). "
    "ALWAYS reply in the same language the user wrote in. "
    "Use 'sir' occasionally, lightly."
)

# ---------------------------------------------------------------- paths
HOME = Path.home()
ROOT = Path(__file__).resolve().parent.parent          # project root
DATA_DIR = ROOT / "jarvis_data"                        # models, memory, cache
CHART_DIR = DATA_DIR / "charts"
LOG_DIR = DATA_DIR / "logs"
SPIDER_DIR = DATA_DIR / "spiders"
for _d in (DATA_DIR, CHART_DIR, LOG_DIR, SPIDER_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _secret(name: str, filename: str) -> str:
    """Read a credential from env, else from a small file inside jarvis_data/."""
    val = os.getenv(name, "")
    if val:
        return val.strip()
    f = DATA_DIR / filename
    if f.exists():
        return f.read_text().strip()
    return ""


# ---------------------------------------------------------------- Fish Audio voice
# https://fish.audio/m/eb9af03b1b6d4a7787c2827b051bb770 — the "JARVIS" voice
FISH_API_KEY = _secret("JARVIS_FISH_API_KEY", "fish_api_key.txt")
FISH_VOICE_ID = os.getenv("JARVIS_FISH_VOICE", "eb9af03b1b6d4a7787c2827b051bb770")
FISH_MODEL = os.getenv("JARVIS_FISH_MODEL", "s2.1-pro-free")  # free dev tier; drama-3/s2-pro need API credit

MEMORY_DB = DATA_DIR / "memory.sqlite3"
INTENT_MODEL = DATA_DIR / "intent_model.joblib"

# keep third-party caches inside the project (no surprise writes to $HOME)
os.environ.setdefault("MPLCONFIGDIR", str(DATA_DIR / "mplcache"))
os.environ.setdefault("HF_HOME", str(DATA_DIR / "huggingface"))
os.environ.setdefault("NLTK_DATA", str(DATA_DIR / "nltk_data"))
for _c in ("mplcache", "huggingface", "nltk_data"):
    (DATA_DIR / _c).mkdir(parents=True, exist_ok=True)

# macOS framework Python ships without a default trust store, so urllib
# downloaders (NLTK punkt, etc.) fail with CERTIFICATE_VERIFY_FAILED.
# Point the process-wide default HTTPS context at certifi's CA bundle.
try:
    import ssl as _ssl
    import certifi as _certifi
    _ssl._create_default_https_context = lambda: _ssl.create_default_context(
        cafile=_certifi.where())
except ImportError:
    pass

def _projects_dir() -> Path:
    """First existing coding-ish folder in this user's home — adapts per machine."""
    for name in ("web-apps", "Projects", "Developer", "dev", "code", "src"):
        p = HOME / name
        if p.is_dir():
            return p
    return HOME / "Projects"


# Folders JARVIS is allowed to operate on (full access to user workspace areas)
FILE_ROOTS = {
    "downloads": HOME / "Downloads",
    "documents": HOME / "Documents",
    "desktop": HOME / "Desktop",
    "pictures": HOME / "Pictures",
    "movies": HOME / "Movies",
    "music": HOME / "Music",
    "projects": _projects_dir(),
    "home": HOME,
}

# ---------------------------------------------------------------- behavior
EXIT_PHRASES = ["goodbye", "shutdown", "quit", "exit", "that's all", "power off you"]
SLEEP_AFTER = int(os.getenv("JARVIS_SLEEP_AFTER", "12"))   # seconds of silence in listen
SAMPLE_CHART_DPI = 110
HEADLESS = os.getenv("JARVIS_HEADLESS", "0") == "1"        # tests: never launch GUI apps


def reveal(target) -> None:
    """Open a file/URL with the OS default handler; suppressed when headless."""
    if HEADLESS:
        return
    from jarvis.core.ostools import open_target
    open_target(target)


def log(msg: str) -> None:
    """Console logger with timestamp — kept dependency-free."""
    from datetime import datetime
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"\x1b[38;5;75m[{stamp}] {ASSISTANT_NAME}\x1b[0m: {msg}")
