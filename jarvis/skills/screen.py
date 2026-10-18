"""Screen awareness skills — JARVIS can watch (locally), report, react,
and — when a vision model is attached — literally look at the screen."""
from __future__ import annotations

import re

from jarvis.core import activity, vision
from jarvis.core.registry import command


@command("screen.watch_on", "Start the local activity watcher (app timeline + remarks).",
         patterns=[r"start (?:monitoring|watching|the watcher)",
                   r"begin (?:to )?(?:monitor|watch)",
                   r"monitor (?:my|the) (?:activity|screen|laptop|usage|productivity|actions)",
                   r"watch what i(?:'m| am) doing",
                   r"keep (?:an eye|tabs|watch) on my (?:activity|screen|laptop|work)",
                   r"see what i'?m doing"],
         examples=["start monitoring my activity", "watch what I'm doing",
                   "keep an eye on my screen", "monitor my laptop usage",
                   "start the activity watcher", "I want you to see what I do"],
         priority=9)
def watch_on(utterance: str) -> str:
    return activity.start()


@command("screen.watch_off", "Stop the activity watcher.",
         patterns=[r"stop (?:monitoring|watching|the watcher|keeping (?:an eye|tabs|watch))",
                   r"(?:pause|end|deactivate|disable) (?:the )?(?:monitor|watcher|watching|monitoring)",
                   r"you can (?:stop|quit) watching", r"stop spying", r"stop (?:looking|observing)"],
         examples=["stop monitoring", "pause the watcher", "you can stop watching now",
                   "stop tracking my activity"],
         priority=9)
def watch_off(utterance: str) -> str:
    return activity.stop()


@command("screen.watch_status", "Report whether JARVIS is watching and the current scene.",
         patterns=[r"(?:are|will) you (?:be )?(?:monitoring|watching)",
                   r"watcher status|status of (?:the )?(?:watcher|monitor)",
                   r"(?:is|are) (?:the )?(?:monitor|monitoring|watching) (?:on|active|running)"],
         examples=["are you monitoring me", "watcher status", "is the activity monitor on"],
         priority=9)
def watch_status(utterance: str) -> str:
    return activity.status_text()


@command("screen.report", "Time spent per app — 'activity report' or 'what was I doing'.",
         patterns=[r"activity (?:report|log|timeline|summary|history)",
                   r"what (?:have|was|were|i) (?:i )?(?:been )?doing",
                   r"how (?:much time|long).{0,25}(?:spent|on|in)\b",
                   r"time (?:spent|distribution|per app)",
                   r"(?:what|which) apps?.{0,20}(?:today|lately|recently)",
                   r"my (?:day|evening|morning) in (?:apps|review)"],
         examples=["what have I been doing today", "activity report",
                   "how much time did I spend on safari", "time spent per app",
                   "summarise my day in apps", "what was I doing an hour ago"],
         priority=9)
def report(utterance: str) -> str:
    m = re.search(r"(?:last|past) (\d+) ?(?:min|minutes?)", utterance)
    if m:
        return activity.report(int(m.group(1)) * 60)
    m = re.search(r"(?:last|past) (\d+) ?(?:hour|hrs?)", utterance)
    if m:
        return activity.report(int(m.group(1)) * 3600)
    if re.search(r"(?:hour|recent|lately|just)", utterance):
        return activity.report(3600)
    return activity.report(86400)


@command("screen.look", "Look at the screen with a vision model and describe it.",
         patterns=[r"look at (?:my|the) (?:screen|display|laptop)",
                   r"what (?:do|can) you see",
                   r"what am i (?:looking at|watching|viewing)\b",
                   r"describe (?:my|the) (?:screen|display)",
                   r"can you see (?:my|the) (?:screen|what)",
                   r"tell me about (?:my|the) screen"],
         examples=["look at my screen", "what do you see", "describe my screen",
                   "what am I looking at", "can you see my screen"],
         priority=9)
def look(utterance: str) -> str:
    q = re.sub(r".*?\b(?:screen|see|looking at|watching|describe|display)\b"
               r"|(?:jarvis|please|now|and|can you|could you|tell me)\b",
               "", utterance.lower()).strip(" ?.,")
    text, src = vision.describe_screen(q or "")
    if text:
        return text
    # graceful degradation: no eyes — but the watcher still knows the context
    hint = {"no-vision-model": "Attach some eyes: `ollama pull llava` (local) "
                               "or set JARVIS_OPENAI_KEY for gpt-4o-mini vision.",
            "capture-unavailable": "The screen-capture tool refused, sir — check "
                                   "Screen Recording permission.",
            "vision-silent": "The vision model gave me nothing to work with, sir.",
            "vision-error": "The vision service errored, sir — try again shortly."}
    ctx = activity.status_text() if activity.running() else ""
    return f"I can't see the screen right now — {hint.get(src, src)}. " + \
           (f"But from my watch: {ctx}" if ctx else
            "Say 'start monitoring' and I'll at least track your apps, sir.")
