"""Screen vision — lets JARVIS describe what's on the laptop.

Grabs a silent screenshot, then asks a multimodal model what it sees:
local Ollama first (llava / moondream / qwen-vl / gemma3 / minicpm-vision),
OpenAI (gpt-4o) if JARVIS_OPENAI_KEY is set and no local vision model is loaded.
The image is read into memory and removed immediately — no Desktop litter."""
from __future__ import annotations

import base64
import os
import re
from pathlib import Path

from jarvis.config import log
from jarvis.core import net
from jarvis.core import ostools as ot

OLLAMA = "http://localhost:11434"
# model names that ship with vision support
_VISION_HINT = re.compile(r"llava|moondream|minicpm|qwen.*vl|gemma3|vision|vl-", re.I)


def _ollama_models() -> list[str]:
    try:
        data = net.get_json(f"{OLLAMA}/api/tags", timeout=3)
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def vision_model() -> str | None:
    """Pick a locally-loaded multimodal model, or None if there isn't one."""
    for name in _ollama_models():
        if _VISION_HINT.search(name):
            return name
    return None


def backend() -> str:
    """'ollama' | 'openai' | 'none' — how describe_screen would answer today."""
    if vision_model():
        return "ollama"
    if os.getenv("JARVIS_OPENAI_KEY"):
        return "openai"
    return "none"


def describe_screen(question: str = "") -> tuple[str | None, str]:
    """Return (reply, source). reply None → source is the reason (for fallbacks)."""
    path = ot.grab_screen_image()
    if not path:
        return None, "capture-unavailable"
    try:
        b64 = base64.b64encode(Path(path).read_bytes()).decode()
    except OSError as exc:
        log(f"vision read failed: {exc}")
        return None, "capture-unavailable"
    finally:
        Path(path).unlink(missing_ok=True)          # never keep the screenshot

    prompt = ("You are JARVIS, looking at your user's laptop screen. "
              + (question.strip() + " " if question else
                 "Say what the user appears to be working on. ")
              + "Answer in one or two dry, observant British sentences; call him 'sir' once at most.")

    model = vision_model()
    if model:
        try:
            resp = net.post_json(f"{OLLAMA}/api/chat",
                                 {"model": model, "stream": False,
                                  "messages": [{"role": "user", "content": prompt,
                                                "images": [b64]}]}, timeout=90)
            text = resp.get("message", {}).get("content", "").strip()
            if text:
                return text, f"ollama:{model}"
        except Exception as exc:
            log(f"Ollama vision error: {exc}")
        return None, "vision-silent"

    key = os.getenv("JARVIS_OPENAI_KEY")
    if key:
        try:
            resp = net.post_json("https://api.openai.com/v1/chat/completions",
                                 {"model": os.getenv("JARVIS_VISION_MODEL", "gpt-4o-mini"),
                                  "max_tokens": 200,
                                  "messages": [{"role": "user", "content": [
                                      {"type": "text", "text": prompt},
                                      {"type": "image_url", "image_url":
                                       {"url": f"data:image/png;base64,{b64}"}}]}]},
                                 timeout=60, headers={"Authorization": f"Bearer {key}"})
            text = resp["choices"][0]["message"]["content"].strip()
            if text:
                return text, "openai"
        except Exception as exc:
            log(f"OpenAI vision error: {exc}")
        return None, "vision-error"
    return None, "no-vision-model"
