"""Conversational layer — self-knowledge commands plus an optional LLM fallback
(local Ollama, or OpenAI if JARVIS_OPENAI_KEY is set). Keeps JARVIS useful when
an utterance isn't a known command."""
from __future__ import annotations

import os
import random
import re

from jarvis.config import PERSONA, log
from jarvis.core.memory import Memory
from jarvis.core.registry import all_commands, command

_mem = Memory()


# ------------------------------------------------------------------ help / capabilities
@command("jarvis.help", "List capabilities and example commands.",
         patterns=[r"\bhelp\b|\bwhat can you do\b|\bcapabilities\b|\bcommands list\b"],
         examples=["what can you do", "help", "list your commands", "show capabilities"])
def help_cmd(utterance: str) -> str:
    cmds = sorted(all_commands().values(), key=lambda c: c.name)
    lines = [f"{c.name.split('.')[0]}: {c.help}" for c in cmds]
    text = " | ".join(lines)
    print("\n".join(f"  • {c.name:16} {c.help}" for c in cmds))
    return f"I have {len(cmds)} skills across files, system control, data, " \
           f"time-series, scraping, NLP, and web. Full list printed on screen."


@command("jarvis.memory", "Recall recent interactions and saved facts.",
         patterns=[r"what did i (?:say|ask)|last (?:command|request)|remember that|you forget"],
         examples=["what did I ask earlier", "show the last command", "what do you remember about me"])
def memory_cmd(utterance: str) -> str:
    if "remember" in utterance:
        m = re.search(r"remember (?:that )?(.+)", utterance)
        if m:
            facts = _mem.recall("facts", [])
            facts.append(m.group(1))
            _mem.remember("facts", facts[-50:])
            return "Noted, sir."
        return "Tell me what to remember."
    recent = _mem.recent(3)
    n, first = _mem.stats()
    facts = _mem.recall("facts", [])
    out = [f"Logged {n} interactions since {first[:10]}."]
    if recent:
        out.append("Last requests: " + "; ".join(r[1][:40] for r in recent) + ".")
    if facts:
        out.append("Saved facts: " + "; ".join(facts[-3:]) + ".")
    return " ".join(out)


# ------------------------------------------------------------------ LLM fallback
class LLM:
    """Optional generative backend. Order: Ollama local → OpenAI → none."""

    def __init__(self):
        from jarvis.core import net
        self.net = net
        self.mode = "none"
        self.model = os.getenv("JARVIS_OLLAMA_MODEL", "llama3.2")
        if os.getenv("JARVIS_OPENAI_KEY"):
            self.mode = "openai"
            log("LLM fallback active: OpenAI.")
        else:
            try:
                data = net.get_json("http://localhost:11434/api/tags", timeout=2)
                names = [m.get("name", "") for m in data.get("models", [])]
                if names:
                    self.model = names[0]
                    self.mode = "ollama"
                    log(f"LLM fallback active: Ollama ({self.model}).")
            except Exception:
                log("No LLM attached — free-form questions get a scripted reply. "
                    "Install Ollama or set JARVIS_OPENAI_KEY.")

    def _history(self) -> list[tuple[str, str]]:
        """Last conversational exchanges only, so JARVIS remembers the flow of
        *chat* without command turns (open/quit/analyse) bleeding into his replies."""
        try:
            return [(h, r) for _, h, r in _mem.recent(8, skill_prefix="jarvis.chat")]
        except Exception:
            return []

    def ask(self, text: str, history: bool = True) -> str | None:
        if self.mode == "none":
            return None
        hist = self._history() if history else []
        try:
            if self.mode == "ollama":
                transcript = "\n\n".join(
                    [f"User: {h}\nJARVIS: {r}" for h, r in hist]
                    + [f"User: {text}\nJARVIS:"])
                resp = self.net.post_json(
                    "http://localhost:11434/api/generate",
                    {"model": self.model, "prompt": transcript, "stream": False,
                     "system": PERSONA, "options": {"temperature": 0.85,
                                                    "top_p": 0.9,
                                                    "keep_alive": "10m"}},
                    timeout=180)
                return resp.get("response", "").strip()
            messages = [{"role": "system", "content": PERSONA}]
            for h, r in hist:
                messages.append({"role": "user", "content": h})
                messages.append({"role": "assistant", "content": r})
            messages.append({"role": "user", "content": text})
            payload = {
                "model": os.getenv("JARVIS_OPENAI_MODEL", "gpt-4o-mini"),
                "messages": messages,
                "max_tokens": 400}
            resp = self.net.post_json(
                "https://api.openai.com/v1/chat/completions", payload, timeout=60,
                headers={"Authorization": f"Bearer {os.environ['JARVIS_OPENAI_KEY']}"})
            return resp["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            log(f"LLM error: {exc}")
        return None


_llm = LLM()


# A refusal-shaped reply. Local instruct models (llama et al.) sometimes emit
# these for perfectly harmless inputs when the conversation context contains a
# stray "risky" token — and then the refusal gets logged and fed back, so it
# cascades forever. We never want a false refusal to become spoken canon.
_REFUSAL_RE = re.compile(
    r"\bi (?:can'?t|cannot|wo?n'?t|am unable)\b|\bi'?m unable\b|unable to\b|"
    r"\bas an ai\b|explicit content|can'?t (?:fulfill|provide|help|assist)|"
    r"not (?:able|comfortable) to\b", re.I)


def _history_clean() -> list[tuple[str, str]]:
    """Chat-only turns, minus any that contain a prior refusal — so one bad
    reply can't seed the context and cascade into a wall of refusals."""
    try:
        return [(h, r) for _, h, r in _mem.recent(8, skill_prefix="jarvis.chat")
                if not _REFUSAL_RE.search(r or "")]
    except Exception:
        return []


LLM._history = lambda self: _history_clean()   # one source of truth, no cascade


@command("jarvis.chat", "Free-form conversation — questions, statements, opinions, chitchat. "
                        "Routed to the local LLM with full persona. "
                        "This is the fallback when nothing else matches.",
         patterns=[],  # never regex-matched; Brain routes unrouted speech here
         examples=["explain backpropagation simply", "write a haiku about rain",
                   "how do I center a div", "what is gradient boosting",
                   "how are you doing today", "what do you think about artificial intelligence",
                   "im feeling bored tell me something interesting",
                   "who would win in a fight between you and siri",
                   "i dont sleep enough lately", "tell me a joke",
                   "whats your favorite movie", "do you enjoy helping people"])
def chat(utterance: str) -> str:
    answer = _llm.ask(utterance)
    # If the model balks, it may be context poisoning rather than a genuine
    # objection. Retry with a clean slate: harmless inputs answer normally,
    # genuinely problematic ones still refuse — and then we keep that refusal.
    if answer and _REFUSAL_RE.search(answer):
        fresh = _llm.ask(utterance, history=False)
        if fresh and not _REFUSAL_RE.search(fresh):
            return fresh[:1500]
    if answer:
        return answer[:1500]
    fallbacks = [
        "No language model is attached, sir. Install Ollama or set JARVIS_OPENAI_KEY and I'll answer anything.",
        "That's beyond my scripted skills. With a local LLM wired in, it wouldn't be.",
        "I can route commands, not conversation — until you attach Ollama or an API key.",
    ]
    return random.choice(fallbacks)
