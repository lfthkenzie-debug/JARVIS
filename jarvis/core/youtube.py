"""YouTube client — YouTube Data API v3 with an API key for reads (search,
trending, video metadata, watch history) and OAuth2 for writes (uploads).

Local-first where it can be: the watch log is our own sqlite file, nothing
leaves the machine except ordinary API calls to googleapis.com.

Credentials (all optional, all read via config._secret — env var or file):
  JARVIS_YT_KEY          → youtube_api_key.txt      (API key: reads)
  JARVIS_YT_CLIENT       → youtube_client_secret.json (OAuth client: uploads)
Tokens persist in jarvis_data/youtube_oauth.json.
"""
from __future__ import annotations

import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from jarvis.config import DATA_DIR, _secret, log, reveal
from jarvis.core import net

API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3/videos"
AUTH_PAGE = "https://accounts.google.com/o/oauth2/auth"
TOKEN_PAGE = "https://oauth2.googleapis.com/token"
SCOPES = "https://www.googleapis.com/auth/youtube.upload " \
         "https://www.googleapis.com/auth/youtube"
REDIRECT_PORT = 8778
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/"

YT_KEY = _secret("JARVIS_YT_KEY", "youtube_api_key.txt")
CLIENT_FILE = DATA_DIR / "youtube_client_secret.json"
TOKEN_FILE = DATA_DIR / "youtube_oauth.json"
WATCH_DB = DATA_DIR / "youtube_watch.sqlite3"

_VID_RE = re.compile(r"[A-Za-z0-9_-]{11}")


# ------------------------------------------------------------------ basics
def have_key() -> bool:
    return bool(YT_KEY)


def _get(path: str, params: dict) -> dict:
    if not YT_KEY:
        raise RuntimeError("no-key")
    params = {"key": YT_KEY, **params}
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    return net.get_json(url, timeout=15)


def video_id(text: str) -> str | None:
    """Pull a video id out of a URL of any YouTube shape, or a bare id."""
    m = re.search(r"(?:v=|be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})", text)
    if m:
        return m.group(1)
    t = text.strip()
    return t if _VID_RE.fullmatch(t) else None


def watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def _iso_dur_to_secs(d: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    days, hrs, mins, secs = (int(x) if x else 0 for x in m.groups())
    return ((days * 24 + hrs) * 60 + mins) * 60 + secs


# ------------------------------------------------------------------ reads
def search(q: str, max_results: int = 5, order: str = "relevance") -> list[dict]:
    """Return [{id,title,channel,published}] for actual videos."""
    data = _get("search", {
        "part": "snippet", "q": q, "type": "video", "maxResults": max_results,
        "order": order, "safeSearch": "none"})
    out = []
    for it in data.get("items", []):
        if it.get("id", {}).get("kind") != "youtube#video":
            continue  # chart=search leaks playlists/channels otherwise
        s = it["snippet"]
        out.append({"id": it["id"]["videoId"], "title": s.get("title", ""),
                    "channel": s.get("channelTitle", ""),
                    "published": (s.get("publishedAt") or "")[:10]})
    return out


def trending(region: str = "ID", max_results: int = 10) -> list[dict]:
    data = _get("videos", {"part": "snippet,statistics", "chart": "mostPopular",
                           "regionCode": region, "maxResults": max_results})
    out = []
    for it in data.get("items", []):
        s, st = it["snippet"], it.get("statistics", {})
        out.append({"id": it["id"], "title": s.get("title", ""),
                    "channel": s.get("channelTitle", ""),
                    "views": int(st.get("viewCount", 0)),
                    "likes": int(st.get("likeCount", 0))})
    return out


def video_info(vid: str) -> dict | None:
    data = _get("videos", {"part": "snippet,statistics,contentDetails", "id": vid})
    items = data.get("items", [])
    if not items:
        return None
    it = items[0]
    s, st, cd = it["snippet"], it.get("statistics", {}), it.get("contentDetails", {})
    return {"id": vid, "title": s.get("title", ""),
            "channel": s.get("channelTitle", ""),
            "desc": (s.get("description") or "")[:1200],
            "views": int(st.get("viewCount", 0)),
            "likes": int(st.get("likeCount", 0)),
            "secs": _iso_dur_to_secs(cd.get("duration", "")),
            "published": (s.get("publishedAt") or "")[:10]}


def _fmt_n(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


# ------------------------------------------------------------------ open + watch log
def open_video(vid: str) -> None:
    reveal(watch_url(vid))
    title = channel = ""
    try:
        info = video_info(vid)
        if info:
            title, channel = info["title"], info["channel"]
    except Exception:
        pass
    log_watch(vid, title, channel)


def _db() -> "sqlite3.Connection":  # noqa: F821
    import sqlite3
    conn = sqlite3.connect(str(WATCH_DB), timeout=5)
    conn.execute("""CREATE TABLE IF NOT EXISTS watches(
        ts TEXT, vid TEXT, title TEXT, channel TEXT)""")
    return conn


def log_watch(vid: str, title: str = "", channel: str = "") -> None:
    try:
        with _db() as conn:
            conn.execute("INSERT INTO watches VALUES(?,?,?,?)",
                         (datetime.now().isoformat(timespec="seconds"),
                          vid, title, channel))
    except Exception as exc:
        log(f"yt watch log: {exc}")


def watch_history(limit: int = 20) -> list[dict]:
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT ts,vid,title,channel FROM watches "
                "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "vid": r[1], "title": r[2], "channel": r[3]}
                for r in rows]
    except Exception:
        return []


def last_watched() -> dict | None:
    h = watch_history(1)
    return h[0] if h else None


def watch_stats() -> str:
    h = watch_history(1000)
    if not h:
        return "I haven't logged any videos for you yet, sir. Say 'play ... on youtube' and we'll begin."
    from collections import Counter
    today = [x for x in h if x["ts"].startswith(datetime.now().strftime("%Y-%m-%d"))]
    top = Counter((x["channel"] or x["title"][:30]) for x in h).most_common(3)
    tops = ", ".join(f"{name} ({n})" for name, n in top)
    return (f"Logged {len(h)} opens all-time, {len(today)} today. "
            f"Usual suspects: {tops}.")


# ------------------------------------------------------------------ reactions (LLM)
_REFUSAL_RE = re.compile(r"can'?t (?:provide|help|assist)|unable to|explicit content|"
                         r"as an ai|not (?:able|comfortable)", re.I)


def react(topic: str) -> str:
    """One persona-flavoured remark about a video/topic. topic is free text
    usually built from video_info(). Falls back to a dry line without an LLM."""
    from jarvis.skills.chat import _llm
    prompt = ("Write a short, good-natured remark — pure creative commentary, nothing "
              f"sensitive here. The video: {topic}. One or two sentences, witty and "
              "honest, your own taste. Don't open with 'Oh' or 'Well'.")
    out = _llm.ask(prompt)
    if out and not _REFUSAL_RE.search(out):
        return out.strip()
    return "No language model attached — watch it yourself, sir, and tell me over dinner."


def _plain(text: str, limit: int = 240) -> str:
    """Strip URLs/whitespace so a long description can't trip the LLM's filters."""
    text = re.sub(r"https?://\S+|www\.\S+", "", text or "")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def react_to_video(vid: str) -> str:
    try:
        info = video_info(vid)
    except Exception as exc:
        return f"YouTube API unreachable, sir ({exc})."
    if not info:
        return "I couldn't find that video, sir."
    topic = (f"'{info['title']}' by {info['channel']}, "
             f"{_fmt_n(info['views'])} views, {info['secs'] // 60} min long. "
             f"About: {_plain(info['desc'])}")
    return react(topic)


def pick_for_me(mood: str = "") -> str:
    """Trending list filtered by like-ratio, an LLM shortlist-pick, then opened."""
    if not YT_KEY:
        return "I need a YouTube API key to browse, sir — jarvis_data/youtube_api_key.txt."
    try:
        cand = [v for v in trending(max_results=15)
                if v["views"] > 0 and v["likes"] / max(v["views"], 1) > 0.01]
    except Exception as exc:
        return f"Trending feed refused me, sir ({exc})."
    if not cand:
        return "Trending had nothing worth my standards, sir."
    listing = "\n".join(f"{i + 1}. {v['title']} ({v['channel']})"
                        for i, v in enumerate(cand[:8]))
    from jarvis.skills.chat import _llm
    q = ("From this trending list, choose the ONE video you would genuinely "
         f"watch yourself{f' — in the mood for: {mood}' if mood else ''}. "
         f"Answer with just its number.\n{listing}")
    ans = _llm.ask(q) or ""
    m = re.search(r"\b([1-8])\b", ans)
    chosen = cand[int(m.group(1)) - 1] if m else cand[0]
    open_video(chosen["id"])
    why = react(f"'{chosen['title']}' by {chosen['channel']}") or "Because I said so."
    return f"I'm putting this on, sir: {chosen['title']} — {why}"


# ------------------------------------------------------------------ oauth (uploads)
def _client_id_secret() -> tuple[str, str] | None:
    if not CLIENT_FILE.exists():
        return None
    try:
        data = json.loads(CLIENT_FILE.read_text())
        for kind in ("installed", "web"):
            cfg = data.get(kind)
            if cfg and cfg.get("client_id"):
                return cfg["client_id"], cfg["client_secret"]
    except Exception as exc:
        log(f"yt client file: {exc}")
    return None


def _load_tokens() -> dict | None:
    try:
        if TOKEN_FILE.exists():
            tok = json.loads(TOKEN_FILE.read_text())
            if tok.get("access_token"):
                return tok
    except Exception:
        pass
    return None


def _save_tokens(tok: dict) -> None:
    tok = dict(tok)
    if "expires_in" in tok:
        tok["expires_at"] = time.time() + int(tok["expires_in"]) - 120
    try:
        TOKEN_FILE.write_text(json.dumps(tok, indent=2))
        TOKEN_FILE.chmod(0o600)
    except Exception as exc:
        log(f"yt tokens: {exc}")


def _token_post(payload: dict) -> dict:
    req = urllib.request.Request(
        TOKEN_PAGE, data=urllib.parse.urlencode(payload).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20, context=net._context()) as r:
        return json.loads(r.read())


def _fresh_token() -> dict | None:
    tok = _load_tokens()
    if not tok:
        return None
    if time.time() < tok.get("expires_at", 0) or not tok.get("refresh_token"):
        return tok
    cs = _client_id_secret()
    if not cs:
        return tok
    try:
        new = _token_post({"client_id": cs[0], "client_secret": cs[1],
                           "refresh_token": tok["refresh_token"],
                           "grant_type": "refresh_token"})
        new["refresh_token"] = tok["refresh_token"]
        _save_tokens(new)
        return new
    except Exception as exc:
        log(f"yt token refresh: {exc}")
        return tok


def is_authorized() -> bool:
    return _load_tokens() is not None


def authorize() -> str:
    """Loopback OAuth2 code flow — opens the browser, waits ~120s on localhost
    for Google's redirect, exchanges the code, stores tokens locally."""
    cs = _client_id_secret()
    if not cs:
        return ("No OAuth client, sir. Drop an installed-app client secret at "
                "jarvis_data/youtube_client_secret.json (Cloud console → "
                "credentials → OAuth client ID), then say 'authorise youtube'.")
    state = secrets.token_urlsafe(8)
    box: dict = {}

    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Catch(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            box.update({k: v[0] for k, v in qs.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h3>JARVIS has the keys. Close this tab, sir.</h3>")

        def log_message(self, *a):  # silence
            pass

    try:
        srv = HTTPServer(("localhost", REDIRECT_PORT), _Catch)
    except OSError as exc:
        return f"Can't bind the callback port {REDIRECT_PORT}: {exc}"
    url = AUTH_PAGE + "?" + urllib.parse.urlencode({
        "client_id": cs[0], "redirect_uri": REDIRECT_URI, "scope": SCOPES,
        "response_type": "code", "access_type": "offline",
        "prompt": "consent", "state": state})
    reveal(url)
    srv.timeout = 2
    deadline = time.time() + 120
    while time.time() < deadline and not box:
        srv.handle_request()
    srv.server_close()
    if not box:
        return "I waited two minutes and you never signed, sir. Try again."
    if box.get("state") != state:
        return "The callback came back with the wrong state — I've dropped it."
    if "error" in box:
        return f"Google said no: {box.get('error')}."
    try:
        tok = _token_post({"client_id": cs[0], "client_secret": cs[1],
                           "code": box["code"], "grant_type": "authorization_code",
                           "redirect_uri": REDIRECT_URI})
    except Exception as exc:
        return f"Code exchange failed, sir ({exc})."
    _save_tokens(tok)
    return "Authorised, sir. Uploads unlocked — tokens live on this disk, nowhere else."


def upload(path: str, title: str = "", desc: str = "", tags: str = "",
           privacy: str = "private") -> str:
    """Single-pass multipart upload. Needs OAuth; returns a JARVIS-flavoured line."""
    p = Path(path).expanduser()
    if not p.is_file():
        return f"There's no file at {p}, sir."
    if p.suffix.lower() not in (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"):
        return f"{p.suffix or 'That file'} doesn't look like a video, sir."
    tok = _fresh_token()
    if not tok:
        return ("Uploading needs OAuth, sir. Say 'authorise youtube' "
                "(and drop the client secret in jarvis_data/ first).")
    title = title or p.stem.replace("_", " ").replace("-", " ")
    meta = {"snippet": {"title": title[:100], "description": desc[:4000],
                        "tags": [t.strip() for t in tags.split(",") if t.strip()],
                        "categoryId": "22"},
            "status": {"privacyStatus": privacy, "selfMade": True}}
    boundary = "----jarvis" + secrets.token_hex(8)
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            + json.dumps(meta)
            + f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n").encode() \
        + p.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        UPLOAD_API + "?" + urllib.parse.urlencode({"uploadType": "multipart"}),
        data=body, method="POST", headers={
            "Authorization": f"Bearer {tok['access_token']}",
            "Content-Type": f"multipart/related; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=600,
                                    context=net._context()) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read()).get("error", {})
        except Exception:
            err = {"message": str(exc)}
        return f"Google refused the upload, sir: {err.get('message', exc)}"
    except Exception as exc:
        return f"Upload failed, sir ({exc})."
    vid = res.get("id", "")
    log_watch(vid, title, "self")
    return (f"Uploaded '{title}' — {watch_url(vid)} "
            f"(status: {privacy}; processing takes a minute).")
