"""Activity watcher — JARVIS's eyes on the laptop, local-first.

A daemon thread samples the frontmost app (via LaunchServices — no extra
permissions) and, when the Mac allows it, the window title; every change is
appended to jarvis_data/activity.sqlite3. Rule-based reactions let JARVIS
comment on what you're doing without being asked — all throttled so he stays
the polite butler, not the nagging one. Nothing is uploaded: the timeline
never leaves this disk, and stopping the watcher silences him completely."""
from __future__ import annotations

import os
import random
import re
import sqlite3
import threading
import time
from datetime import datetime

from jarvis.config import DATA_DIR, log
from jarvis.core import ostools as ot

ACTIVITY_DB = DATA_DIR / "activity.sqlite3"

SAMPLE_S = 5                 # poll cadence
FOCUS_ALERT_S = 25 * 60      # one app this long → JARVIS remarks
FOCUS_COOLDOWN = 3 * 3600    # per-app remark cooldown
STRETCH_S = 90 * 60          # continuous watching this long → break nudge
LOW_MEM_MB = 220             # free RAM under this → offer to clean
IDLE_CAP_S = 15 * 60         # gaps this long are not attributed to any app
YT_DWELL_S = 8               # a video must hold the tab this long before his first remark
YT_CHAT_S = 75               # then a fresh comment this often while the same video plays
YT_MAX_COMMENTS = 5          # per video — a mate on the couch, not a monologue
YT_COOLDOWN = 45             # global floor: never two YouTube remarks back to back
WEB_DWELL_S = 15             # a page must hold the tab this long before he reads it
WEB_CHAT_S = 100             # then one fresh aside this often while the same page holds
WEB_MAX_COMMENTS = 2         # per page — a digest and a side note, then he lets you read
WEB_COOLDOWN = 90            # global floor: browsing is diffuse, so far less trigger-happy

_lock = threading.Lock()
_speaker = None
_conn: sqlite3.Connection | None = None
_state: dict = {"thread": None, "stop": None, "since": None, "events": 0,
                "last_react": {}, "reactions": [],
                "yt": {"key": "", "since": 0.0, "ready_at": 0.0, "count": 0,
                       "busy": False, "url": "", "title": "", "vid": ""},
                "web": {"key": "", "since": 0.0, "ready_at": 0.0, "count": 0,
                        "busy": False, "url": "", "title": ""}}
_YT_REACT = os.getenv("JARVIS_YT_REACT", "1") == "1"   # react to browsed YouTube tabs
_WEB_REACT = os.getenv("JARVIS_WEB_REACT", "0") == "1"  # react to pages being read (opt-in)


def set_speaker(sp) -> None:
    """main.py hands the session Speaker down so reactions can be voiced."""
    global _speaker
    _speaker = sp


def announce(text: str) -> None:
    """Public door for any subsystem (e.g. timers) to make JARVIS say + surface
    a line through the same feed the watcher uses — no monitoring required."""
    _react(text)


def enable_yt(on: bool = True) -> None:
    """Turn real-time reactions to browsed YouTube tabs on/off (env default on)."""
    global _YT_REACT
    _YT_REACT = on
    if on:
        st = _state["yt"]
        st["count"] = 0                      # let him speak about the current tab
        st["ready_at"] = time.time()


def enable_web(on: bool = True) -> None:
    """Turn real-time reactions to pages being read on/off (env default OFF —
    browsing is diffuse, so this only runs when explicitly asked)."""
    global _WEB_REACT
    _WEB_REACT = on
    if on:
        st = _state["web"]
        st["count"] = 0
        st["ready_at"] = time.time()


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(ACTIVITY_DB), check_same_thread=False)
        _conn.execute("CREATE TABLE IF NOT EXISTS events("
                      " id INTEGER PRIMARY KEY, ts REAL, app TEXT, title TEXT)")
        _conn.commit()
    return _conn


def _cool(key: str, secs: float) -> bool:
    """True if this reaction key is off cooldown (and starts a new window)."""
    now = time.time()
    if _state["last_react"].get(key, 0) + secs <= now:
        _state["last_react"][key] = now
        return True
    return False


def _react(text: str) -> None:
    """Proactive one-liner: logged, kept for the UI feed, spoken if able."""
    _state["reactions"] = ([{"ts": time.time(), "text": text}]
                           + _state["reactions"])[:8]
    log(f"Reaction: {text}")
    if _speaker is not None:
        try:
            _speaker.say(text, block=False)
        except Exception as exc:
            log(f"reaction speak failed: {exc}")


FOCUS_LINES = [
    "You've been with {app} for {mins} minutes, sir. Shall I keep notes?",
    "{mins} minutes straight in {app} — deep work, sir? Noted.",
    "Still {app}, sir. {mins} minutes. Anything I can take off your hands?",
]
STRETCH_LINES = [
    "Ninety minutes of steady work, sir. Even Iron Man stretches.",
    "You've been at this for an hour and a half — water, then a short walk, sir.",
]
LOWMEM_LINES = [
    "Memory on this machine is running thin, sir. Say 'free up memory' and I'll declutter.",
    "Low on RAM, sir — 'free up memory' works wonders if you'd like me to act.",
]


def _clean_yt_title(s: str) -> str:
    """Strip only the trailing site suffix (' - YouTube', ' | YouTube…') — never a
    hyphen inside the title itself, so 'Lo-fi beats' survives intact."""
    return re.sub(r"\s*[-–|]\s*YouTube\b.*$", "", s).strip()[:160]


def _youtube_scan(app: str, title: str, now: float) -> None:
    """Look at the active browser tab; while the same YouTube video holds focus,
    run a running commentary — first remark after a short dwell, then a fresh
    comment every YT_CHAT_S (max YT_MAX_COMMENTS per video, YT_COOLDOWN global
    floor). Handing to a worker keeps the slow LLM lookup off the sampling thread."""
    if not _YT_REACT:
        return
    st = _state["yt"]
    url, tab = ot.active_tab(app)
    vid = title_txt = ""
    if url and ("youtube.com" in url or "youtu.be" in url):
        import jarvis.core.youtube as ytm
        vid = ytm.video_id(url) or ""
        title_txt = _clean_yt_title(tab) if tab else ""
    elif title and "youtube" in title.lower():
        title_txt = _clean_yt_title(title)
    if not (url or title_txt):
        return
    key = vid or title_txt
    st.update(url=url, title=title_txt, vid=vid)
    if not key:
        return
    if key != st["key"]:                          # new video: reset the commentary arc
        st.update(key=key, since=now, ready_at=now + YT_DWELL_S, count=0)
    if (st["count"] < YT_MAX_COMMENTS and now >= st["ready_at"]
            and not st["busy"] and _cool("youtube", YT_COOLDOWN)):
        first = st["count"] == 0
        st["count"] += 1
        st["ready_at"] = now + YT_CHAT_S
        st["busy"] = True
        threading.Thread(target=_yt_react_worker, args=(vid, title_txt, first),
                         daemon=True, name="jarvis-yt-react").start()


def _yt_react_worker(vid: str, title_txt: str, first: bool = True) -> None:
    """Fetch the video, log it once locally, and speak one persona remark."""
    st = _state["yt"]
    text = ""
    try:
        import jarvis.core.youtube as ytm
        info = None
        if vid and ytm.have_key():
            try:
                info = ytm.video_info(vid)
            except Exception as exc:
                log(f"yt scan: {exc}")
        if info:
            if first:
                ytm.log_watch(vid, info["title"], info["channel"])
            if first:
                topic = (f"'{info['title']}' by {info['channel']}, "
                         f"{ytm._fmt_n(info['views'])} views, {info['secs'] // 60} min. "
                         f"About: {ytm._plain(info['desc'])}")
            else:
                topic = (f"'{info['title']}' by {info['channel']} — you are still "
                         f"watching it, minutes in. Comment with a fresh angle "
                         f"(a detail, the pacing, where it's going), not a repeat "
                         f"first impression. About: {ytm._plain(info['desc'])}")
            text = ytm.react(topic)
        elif title_txt:
            text = ytm.react(title_txt)          # no id/API — react on the tab title
    except Exception as exc:
        log(f"yt react worker: {exc}")
    finally:
        st["busy"] = False
    if text:
        _react(text)


def _web_scan(app: str, title: str, now: float) -> None:
    """The browsing-together counterpart of _youtube_scan: while a normal (not
    YouTube) web page holds the active tab, fetch it and offer a short remark —
    one digest after a dwell, then a single aside. Off the sampling thread via a
    worker, since fetching and summarising are slow."""
    if not _WEB_REACT:
        return
    st = _state["web"]
    url, tab = ot.active_tab(app)
    if not url or not re.match(r"https?://", url):
        return
    if "youtube.com" in url or "youtu.be" in url:
        return                                   # YouTube has its own scan
    st["url"], st["title"] = url, (tab or "")[:160]
    key = url.split("#")[0].rstrip("/")           # ignore fragment + trailing slash
    if key != st["key"]:                          # new page: reset the arc
        st.update(key=key, since=now, ready_at=now + WEB_DWELL_S, count=0)
    if (st["count"] < WEB_MAX_COMMENTS and now >= st["ready_at"]
            and not st["busy"] and _cool("web", WEB_COOLDOWN)):
        first = st["count"] == 0
        st["count"] += 1
        st["ready_at"] = now + WEB_CHAT_S
        st["busy"] = True
        threading.Thread(target=_web_react_worker, args=(url, first),
                         daemon=True, name="jarvis-web-react").start()


def _web_react_worker(url: str, first: bool) -> None:
    """Fetch the page headlessly and speak one over-the-shoulder remark. Stays
    silent if the fetch fails or the page has nothing readable."""
    st = _state["web"]
    text = ""
    try:
        import jarvis.core.browser as br
        page = br.load(url, keep_history=False)   # don't disturb the manual browse stack
        st["title"] = (page.get("title") or st["title"])[:160]
        text = br.remark(page, first=first)
    except Exception as exc:
        log(f"web react worker: {exc}")
    finally:
        st["busy"] = False
    if text:
        _react(text)


def _watch(stop: threading.Event) -> None:
    conn = _db()
    last_app = last_title = ""
    focus_app, focus_since = "", time.time()
    started = time.time()
    while not stop.is_set():
        app, title = ot.frontmost_app()
        now = time.time()
        if app and (app != last_app or title != last_title):
            with _lock:
                conn.execute("INSERT INTO events (ts, app, title) VALUES (?,?,?)",
                             (now, app, title[:160]))
                conn.commit()
            _state["events"] += 1
            if app != last_app:
                focus_app, focus_since = app, now
            last_app, last_title = app, title
        # --- rule-based reactions (every one throttled via _cool) ---
        if focus_app and now - focus_since >= FOCUS_ALERT_S \
                and _cool("focus:" + focus_app, FOCUS_COOLDOWN):
            _react(random.choice(FOCUS_LINES).format(
                app=focus_app, mins=int((now - focus_since) / 60)))
            focus_since = now
        if now - started >= STRETCH_S and _cool("stretch", 3600):
            _react(random.choice(STRETCH_LINES))
            started = now
        if ot.mem().get("free_mb", 9999) < LOW_MEM_MB and _cool("lowmem", 1800):
            _react(random.choice(LOWMEM_LINES))
        _youtube_scan(app, title, now)
        _web_scan(app, title, now)
        stop.wait(SAMPLE_S)


# ------------------------------------------------------------------ commands
def start() -> str:
    if running():
        return "I'm already watching, sir — timeline running since " \
               f"{_since_text()}."
    stop_ev = threading.Event()
    th = threading.Thread(target=_watch, args=(stop_ev,), daemon=True,
                          name="jarvis-watcher")
    _state.update(thread=th, stop=stop_ev, since=time.time(), events=0,
                  last_react={}, reactions=[],
                  yt={"key": "", "since": 0.0, "ready_at": 0.0, "count": 0,
                      "busy": False, "url": "", "title": "", "vid": ""},
                  web={"key": "", "since": 0.0, "ready_at": 0.0, "count": 0,
                       "busy": False, "url": "", "title": ""})
    _db()                                   # ensure table exists up front
    th.start()
    _react("Eyes open, sir. I'm logging app activity locally — say "
           "'stop monitoring' any time.")
    return ("Monitoring started. Your timeline stays on this machine in "
            "jarvis_data — I'll remark now and then unless I hear otherwise.")


def stop() -> str:
    if not running():
        return "I wasn't watching, sir."
    _state["stop"].set()
    _state["thread"].join(timeout=3)
    mins = int((time.time() - _state["since"]) / 60)
    ev = _state["events"]
    _state.update(thread=None, stop=None, since=None)
    return (f"Monitoring stopped after {mins} minute{'s' if mins != 1 else ''} "
            f"— {ev} scene{'s' if ev != 1 else ''} logged, then silence, sir.")


def running() -> bool:
    th = _state["thread"]
    return bool(th and th.is_alive())


def _since_text() -> str:
    if not _state["since"]:
        return "—"
    return datetime.fromtimestamp(_state["since"]).strftime("%H:%M")


def status_text() -> str:
    if not running():
        return "Monitoring is off, sir. Say 'start monitoring' and I'll keep a local timeline."
    cur_app, cur_title = ot.frontmost_app()
    where = f"You're in {cur_app}" + (f" — “{cur_title}”" if cur_title else "")
    return (f"Watching since {_since_text()}, {int((time.time() - _state['since']) / 60)} "
            f"minutes, {_state['events']} scene changes logged. {where}.")


# ------------------------------------------------------------------ reports
def _dur(sec: float) -> str:
    m = int(sec / 60)
    return f"{m}m" if m < 60 else f"{m // 60}h{m % 60:02d}m"


def stats(seconds: float = 86400) -> tuple[list[dict], float]:
    """([{'app', 'mins'}...] by time, total_seconds_accounted) over last `seconds`."""
    since = time.time() - seconds
    conn = _db()
    with _lock:
        rows = conn.execute(
            "SELECT ts, app FROM events WHERE ts >= ? ORDER BY ts",
            (since,)).fetchall()
    if not rows:
        return [], 0.0
    per: dict[str, float] = {}
    ends = [r[0] for r in rows[1:]] + [min(time.time(), since + seconds)]
    for (ts, app), end in zip(rows, ends):
        per[app] = per.get(app, 0) + min(max(end - ts, 0), IDLE_CAP_S)
    top = sorted(per.items(), key=lambda kv: -kv[1])[:6]
    return ([{"app": a, "mins": round(s / 60, 1)} for a, s in top],
            sum(per.values()))


def report(seconds: float = 86400) -> str:
    """Per-app time over the last `seconds`, then the current scene."""
    top, total = stats(seconds)
    if not top:
        return ("No activity logged in that stretch, sir"
                + ("" if running() else " — monitoring is off; say 'start monitoring'."))
    breakdown = ", ".join(f"{t['app']} {_dur(t['mins'] * 60)}" for t in top)
    tail = status_text() if running() else ""
    return (f"Time spent: {_dur(total)} accounted. {breakdown}. {tail}").strip()


def panel() -> dict:
    """Compact snapshot for the hologram UI / /api/activity."""
    app, title = ot.frontmost_app()
    top, total = stats(86400)
    yt = _state["yt"]
    web = _state["web"]
    return {"running": running(),
            "since": _since_text(), "events": _state["events"],
            "current": {"app": app, "title": title},
            "youtube": {"watching": bool(yt.get("url") or yt.get("title")),
                        "title": yt.get("title", ""), "url": yt.get("url", ""),
                        "reacting": _YT_REACT},
            "browsing": {"reading": bool(web.get("url")),
                         "title": web.get("title", ""), "url": web.get("url", ""),
                         "reacting": _WEB_REACT},
            "today": top, "total_mins": round(total / 60, 1),
            "feed": feed(8), "reactions": _state["reactions"]}


def feed(n: int = 8) -> list[dict]:
    """Latest timeline scenes — for the hologram panel."""
    conn = _db()
    with _lock:
        rows = conn.execute(
            "SELECT ts, app, title FROM events ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    return [{"ts": round(ts, 1), "app": app, "title": title} for ts, app, title in rows]
