"""Timers — countdowns that announce themselves when they lapse.

A small, local, non-blocking engine: each timer runs on its own daemon thread
that simply waits on an Event with a timeout. Cancelling sets the Event (so the
thread wakes early and stays silent); letting it lapse fires the notification.
When a timer goes off it (a) plays a short chime where the OS can, and (b) hands
the line to activity.announce(), which is already wired to the session Speaker
and the hologram UI feed — so a finished timer is both spoken and shown without
any extra plumbing, and works whether or not activity monitoring is running.
Nothing is scheduled outside this process: timers live only while JARVIS runs."""
from __future__ import annotations

import threading
import time

from jarvis.config import log
from jarvis.core import ostools as ot

_LOCK = threading.Lock()
_TIMERS: dict[int, dict] = {}
_NEXT = [1]

# A pleasant, near-universal macOS chime; other platforms just get the voice.
_CHIME = "/System/Library/Sounds/Glass.aiff"


def fmt_hms(seconds: int) -> str:
    """Human duration for speech and the panel: '1 hour 5 minutes', '45 seconds'."""
    seconds = max(int(seconds), 0)
    if seconds == 0:
        return "momentarily"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} hour" + ("s" if h != 1 else ""))
    if m:
        parts.append(f"{m} minute" + ("s" if m != 1 else ""))
    if s and not h:                       # skip stray seconds on long timers
        parts.append(f"{s} second" + ("s" if s != 1 else ""))
    return " ".join(parts) or "0 seconds"


def _notify(tid: int, seconds: int, label: str) -> None:
    """Chime (best effort) then speak/surface the completion line."""
    try:
        if ot.IS_MAC and ot.playback_available():
            ot.spawn(["afplay", _CHIME])
    except Exception as exc:
        log(f"timer chime: {exc}")
    what = f" ({label})" if label else ""
    try:
        from jarvis.core import activity
        activity.announce(f"Time's up{what}, sir — your timer of {fmt_hms(seconds)} has run out.")
    except Exception as exc:
        log(f"timer announce: {exc}")


def _run(tid: int, seconds: int, label: str, ev: threading.Event) -> None:
    if ev.wait(seconds):                  # True => cancelled before the deadline
        return
    with _LOCK:
        _TIMERS.pop(tid, None)
    log(f"timer {tid} fired ({seconds}s)")
    _notify(tid, seconds, label)


def set_timer(seconds: int, label: str = "") -> int:
    """Start a countdown; returns its id. A timer of <=0 is clamped to 1 second."""
    seconds = max(int(seconds), 1)
    with _LOCK:
        tid = _NEXT[0]
        _NEXT[0] += 1
        ev = threading.Event()
        _TIMERS[tid] = {"id": tid, "label": label, "total": seconds,
                        "ends": time.time() + seconds, "ev": ev}
    th = threading.Thread(target=_run, args=(tid, seconds, label, ev),
                          daemon=True, name=f"jarvis-timer-{tid}")
    with _LOCK:
        _TIMERS[tid]["thread"] = th
    th.start()
    return tid


def cancel(which: str = "all") -> list[dict]:
    """Cancel 'all', 'last', or a numeric id. Returns the timers removed."""
    with _LOCK:
        if not _TIMERS:
            return []
        if which == "all":
            ids = list(_TIMERS)
        elif which == "last":
            ids = [max(_TIMERS)]
        else:
            try:
                ids = [int(which)]
            except (TypeError, ValueError):
                ids = []
        gone = []
        for i in ids:
            t = _TIMERS.pop(i, None)
            if t:
                t["ev"].set()
                gone.append({k: t[k] for k in ("id", "label", "total", "ends")})
    return gone


def active() -> list[dict]:
    """Live timers with computed seconds remaining, soonest first."""
    now = time.time()
    with _LOCK:
        rows = [{k: t[k] for k in ("id", "label", "total", "ends")} for t in _TIMERS.values()]
    for r in rows:
        r["remaining"] = max(int(round(r["ends"] - now)), 0)
    rows.sort(key=lambda r: r["ends"])
    return rows
