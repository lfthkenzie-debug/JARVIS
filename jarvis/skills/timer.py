"""Timer skills — set, check, and cancel countdowns that go off on their own.

The engine lives in jarvis/core/timer.py; this module is all about understanding
the spoken request: parsing a duration out of prose (digits *and* spelled numbers,
because voice STT writes "ten minutes" as often as "10 minutes"), pulling an
optional label, and phrasing JARVIS's reply."""
from __future__ import annotations

import re

from jarvis.core import timer
from jarvis.core.registry import command

# Spelled numbers voice is liable to produce (a/an double as "one").
_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "dozen": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "ninety": 90,
}
_UNIT = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1, "s": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60, "m": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
}
_NUM = r"\d+(?:\.\d+)?|" + "|".join(sorted(_WORDS, key=len, reverse=True))
# a number (digit or word) with an optional one-char gap, then a unit word/abbrev
_NUM_UNIT = re.compile(rf"\b({_NUM})[\s-]?(seconds?|secs?|minutes?|mins?|hours?|hrs?|[smh])\b", re.I)


def parse_duration(text: str) -> int | None:
    """Total seconds from every '<number> <unit>' pair in the phrase; None if none.
    Handles idioms 'half an hour' and 'an hour and a half', and combos like
    '1 hour and 30 minutes'."""
    t = (text or "").lower()
    if re.search(r"hour and a half", t):          # 'an hour and a half'
        return 5400
    if re.search(r"half an?\s?hour", t):           # 'half an hour' / 'half hour'
        return 1800
    total = 0
    for num_tok, unit_tok in _NUM_UNIT.findall(t):
        n = int(num_tok) if num_tok.replace(".", "", 1).isdigit() else _WORDS.get(num_tok)
        unit = _UNIT.get(unit_tok.lower())
        if n and unit:
            total += n * unit
    return total or None


def _label_from(text: str) -> str:
    """Best-effort tag for the timer ('stretch', 'pasta') from 'remind me to X' or
    a trailing 'for X' that isn't itself the duration."""
    m = re.search(r"remind me to\s+([A-Za-z0-9 .'’-]{2,50}?)\s+in\s", text, re.I)
    if m:
        return m.group(1).strip()[:60]
    m = re.search(r"\b(?:called|named)\s+([A-Za-z][A-Za-z0-9 .'’-]{1,40})", text, re.I)
    if m:
        return m.group(1).strip()[:60]
    m = re.search(r"\bfor\s+([A-Za-z][A-Za-z0-9 .'’-]{1,40})\s*$", text.strip().rstrip(".?!"), re.I)
    if m and not _NUM_UNIT.search(m.group(1)):
        return m.group(1).strip()[:60]
    return ""


@command("timer.set", "Set a countdown timer that chimes and tells you when it lapses.",
         patterns=[
             r"\b(?:set|start|begin|put on|create|make|give me|do a)\b.{0,18}\b(?:a |an |the |one )?\b(?:timer|countdown)\b",
             r"\b(?:timer|countdown)\b.{0,10}\bfor\b\s*(?:\d|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|half)",
             r"\bfor\b\s*(?:\d|a|an|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty)\b[\s-]{0,3}(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)\b.{0,14}\b(?:timer|countdown)\b",
             r"\b(?:\d|a|an|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty)\b[\s-]{0,3}(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)\b.{0,14}\b(?:timer|countdown)\b",
             r"\b(?:timer|countdown)\b.{0,14}\b(?:\d|a|an|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty)\b[\s-]{0,3}(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",
             r"\bremind me\b",
             r"\bwake me (?:up )?\bin\b",
         ],
         examples=["set a timer for 10 minutes", "start a 25 minute timer",
                   "timer for 1 hour", "remind me in 20 minutes",
                   "set a 90 second timer", "wake me in 30 minutes",
                   "put on a five minute timer", "remind me to stretch in an hour"],
         # priority -1: beat sys.time's bare \btime\b hijack on phrases like
         # 'set a timer' that also say 'time'; still consulted before priority-0.
         priority=-1)
def timer_set(utterance: str) -> str:
    secs = parse_duration(utterance)
    if secs is None:
        return ("For how long, sir? Try something like 'set a timer for 10 minutes' "
                "or 'remind me in half an hour'.")
    label = _label_from(utterance)
    tid = timer.set_timer(secs, label)
    dur = timer.fmt_hms(secs)
    extra = f" for {label}" if label else ""
    return (f"Timer{extra} set for {dur}, sir — #{tid}. I'll chime and let you know "
            f"when it's up. Say 'cancel the timer' to call it off.")


@command("timer.status", "Check how much time is left on your running timers.",
         patterns=[
             r"\b(?:how much|how many|how long|what'?s|whats|remaining|left)\b.{0,22}\b(?:timer|countdown)\b",
             r"\b(?:timer|countdown)s?\b.{0,22}\b(?:left|remaining|time|due|up|going|running)\b",
             r"\b(?:list|show|check|status)\b.{0,16}\b(?:my|the|any|active|all)?\b.{0,8}\b(?:timers?|countdowns?)\b",
             r"\b(?:timers?|countdowns?)\b.{0,12}\bstatus\b",
             r"\bmy\b.{0,6}\b(?:timers?|countdowns?)\b",
             r"\bany\b.{0,10}\b(?:timers?|countdowns?)\b",
         ],
         examples=["how much time is left on my timer", "what's my timer status",
                   "list my timers", "do I have any timers going",
                   "how long until my timer goes off", "check my timers"],
         priority=-1)
def timer_status(utterance: str) -> str:
    rows = timer.active()
    if not rows:
        return "No timers running, sir. Say 'set a timer for ten minutes' and I'll start one."
    lines = [f"  #{r['id']}{' ' + r['label'] if r['label'] else ''}: "
             f"{timer.fmt_hms(r['remaining'])} left" for r in rows]
    lead = f"You have {len(rows)} timer{'s' if len(rows) != 1 else ''} going, sir:"
    return lead + "\n" + "\n".join(lines)


@command("timer.cancel", "Cancel a running timer — one, the last, or all of them.",
         patterns=[
             r"\b(?:cancel|stop|clear|delete|remove|kill|reset|end|abort|pause)\b.{0,22}\b(?:timers?|countdowns?)\b",
             r"\b(?:timers?|countdowns?)\b.{0,22}\b(?:cancel|stop|clear|deleted?)\b",
             r"\b(?:turn|switch|shut)\s+off\b.{0,12}\b(?:the |my |a )?\b(?:timer|countdown)\b",
             r"\b(?:cancel|stop|delete)\b.{0,14}\b(?:all|every|the last|that|this)\b.{0,12}\b(?:timers?|countdowns?)?\b",
             r"\b(?:cancel|stop|kill)\b.{0,10}\b(?:my|the)\b.{0,10}\b(?:timer|countdown)\b",
         ],
         examples=["cancel the timer", "stop my 10 minute timer", "clear all timers",
                   "delete the pasta timer", "cancel the last timer", "stop the timer"],
         priority=-1)
def timer_cancel(utterance: str) -> str:
    t = utterance.lower()
    if re.search(r"\ball\b|\bevery\b", t):
        which = "all"
    elif re.search(r"\blast\b", t):
        which = "last"
    else:
        # an id only when it follows '#' or the word 'timer' — never a leading
        # duration like the '10' in 'stop my 10 minute timer'
        m = re.search(r"#(\d{1,3})\b|\b(?:timer|countdown)\s*(?:no\.?|number\s*)?(\d{1,3})\b", t)
        which = (m.group(1) or m.group(2)) if m else "last"   # bare 'cancel the timer' -> most recent
    gone = timer.cancel(which)
    if not gone:
        return "There weren't any timers to cancel, sir."
    if len(gone) == 1:
        g = gone[0]
        lbl = f" ({g['label']})" if g["label"] else ""
        return f"Cancelled timer #{g['id']}{lbl}, sir — {timer.fmt_hms(g['total'])} called off."
    return f"Cancelled all {len(gone)} timers, sir. Peace and quiet restored."
