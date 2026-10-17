"""JARVIS holographic control panel — stdlib HTTP server + JSON API + web UI.

Launch with:  ./run.sh --ui    (opens http://127.0.0.1:8777 in your browser)

Endpoints (all JSON, localhost only):
  GET  /api/system            live Mac vitals (cpu, memory, disk, battery, uptime)
  GET  /api/history?n=        recent interactions from memory
  GET  /api/skills            skill registry listing
  GET  /api/files?root=&path= folder listing / ?q= filename search
  GET  /api/tasks             task list
  POST /api/command           {text, voice} -> {reply, skill, audio?}
  POST /api/files/open        {root, path}  -> reveal in Finder
  POST /api/tasks             {action: add|toggle|delete|clear_done, ...}
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jarvis.config import DATA_DIR, FILE_ROOTS, HOME, log, reveal
from jarvis.core.registry import ordered_commands

WEB_DIR = Path(__file__).resolve().parent / "web"
TASKS_FILE = DATA_DIR / "tasks.json"
UI_PORT = int(os.getenv("JARVIS_UI_PORT", "8777"))

# UI may browse the whole volume; voice skills stay scoped to FILE_ROOTS
UI_ROOTS = {**FILE_ROOTS, "disk": Path("/")}


def _sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout
    except OSError:
        return ""


# ------------------------------------------------------------------ system vitals
def system_stats() -> dict:
    cores = os.cpu_count() or 1
    load1, load5, load15 = os.getloadavg()

    mem_total = 0
    out = _sh(["sysctl", "-n", "hw.memsize"])
    m = re.search(r"\d+", out)
    if m:
        mem_total = int(m.group())
    mem_used_pct = 0.0
    vs = _sh(["vm_stat"])
    pg = re.search(r"page size of (\d+)", vs)
    ps = int(pg.group(1)) if pg else 16384
    def pages(label: str) -> int:
        mm = re.search(rf"{label}:\s+(\d+)", vs)
        return int(mm.group(1)) if mm else 0
    if mem_total:
        used = (pages("Pages active") + pages("Pages wired down")
                + pages("Pages occupied by compressor")) * ps
        mem_used_pct = max(0.0, min(100.0, used / mem_total * 100))

    disk = {}
    df = _sh(["df", "-h", "/"]).splitlines()
    if len(df) > 1:
        p = df[1].split()
        disk = {"total": p[1], "used": p[2], "free": p[3], "pct": p[4]}

    battery = {"pct": None, "state": "n/a"}
    batt = _sh(["pmset", "-g", "batt"])
    bm = re.search(r"(\d+)%", batt)
    if bm:
        low = batt.lower()
        battery = {"pct": int(bm.group(1)),
                   "state": ("charging" if "charging" in low
                             else "charged" if "charged" in low else "discharging")}

    uptime_s = 0
    bt = re.search(r"sec = (\d+)", _sh(["sysctl", "-n", "kern.boottime"]))
    if bt:
        uptime_s = max(0, int(time.time()) - int(bt.group(1)))
    d, rem = divmod(uptime_s, 86400)
    h, rem2 = divmod(rem, 3600)
    uptime_str = f"{d}d {h}h {rem2 // 60}m"

    ip = _sh(["ipconfig", "getifaddr", "en0"]).strip()
    return {
        "host": socket.gethostname(),
        "ip": ip or "—",
        "cores": cores,
        "cpu": {"pct": round(min(100.0, load1 / cores * 100), 1),
                "load": [round(load1, 2), round(load5, 2), round(load15, 2)]},
        "mem": {"pct": round(mem_used_pct, 1),
                "total_gb": round(mem_total / 1024**3, 1)},
        "disk": disk,
        "battery": battery,
        "uptime_s": uptime_s,
        "uptime": uptime_str,
        "time": datetime.now().isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------------ task store
def _load_tasks() -> list[dict]:
    if TASKS_FILE.exists():
        try:
            return json.loads(TASKS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_tasks(tasks: list[dict]) -> None:
    TASKS_FILE.write_text(json.dumps(tasks, indent=1))


# ------------------------------------------------------------------ file listing
def _safe_root(root: str) -> Path | None:
    base = UI_ROOTS.get(root.lower()) if root else HOME
    if base is None or not base.exists():
        return None
    return base


def _entry(p: Path) -> dict:
    try:
        st = p.stat()
        return {"name": p.name, "dir": p.is_dir(), "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}
    except OSError:
        return {"name": p.name, "dir": p.is_dir(), "size": 0, "mtime": ""}


def list_folder(root: str, sub: str) -> dict:
    base = _safe_root(root)
    if base is None:
        return {"error": f"Unknown root '{root}'"}
    target = (base / sub).resolve() if sub else base
    if not target.is_relative_to(base.resolve()):
        return {"error": "Path escapes the allowed folder."}
    if not target.is_dir():
        return {"error": f"{target.name} is not a folder."}
    dirs, files = [], []
    try:
        for p in sorted(target.iterdir(), key=lambda x: x.name.lower()):
            if p.name.startswith("."):
                continue
            (dirs if p.is_dir() else files).append(_entry(p))
    except PermissionError:
        return {"error": f"Permission denied: {target}"}
    entries = (dirs[:80] + files[:120])
    return {"root": root, "path": str(target), "home": str(base),
            "at_home": not sub, "entries": entries}


def search_files(query: str) -> dict:
    from jarvis.skills.files import _search          # pruned, safety-scoped walk
    hits = []
    for i, p in enumerate(_search(query, [r for r in FILE_ROOTS.values() if r.exists()])):
        if i >= 60:
            break
        e = _entry(p)
        e["path"] = str(p)                           # absolute — /api/files/open accepts it
        hits.append(e)
    return {"root": "search", "query": query, "path": "", "entries": hits}


# ------------------------------------------------------------------ API core
class JarvisAPI:
    def __init__(self, brain, memory, speaker):
        self.brain, self.memory, self.speaker = brain, memory, speaker
        self.lock = threading.Lock()
        self.started = time.time()
        from jarvis.skills.system import device_profile
        self.device = device_profile()
        try:
            memory.remember("device_profile", self.device)   # JARVIS remembers it forever
        except Exception:
            pass

    def command(self, text: str, voice: bool) -> dict:
        with self.lock:
            cmd, parsed = self.brain.route(text)
            if cmd is None:
                reply = "I didn't catch a command in that, sir."
            else:
                try:
                    reply = cmd.handler(parsed)
                except Exception as exc:
                    reply = f"{cmd.name} failed: {exc}"
                    log(f"Skill error in {cmd.name}: {exc!r}")
            self.memory.record(text, reply, cmd.name if cmd else "")
        audio = None
        if voice:
            data = self.speaker.synthesize(reply)
            if data:
                audio = base64.b64encode(data).decode()
        return {"reply": reply, "skill": cmd.name if cmd else "", "audio": audio}

    def skills(self) -> dict:
        return {"skills": [{"name": c.name, "help": c.help,
                            "examples": c.examples[:2]} for c in ordered_commands()]}

    def history(self, n: int) -> dict:
        return {"items": [{"ts": t, "heard": h, "reply": r}
                          for t, h, r in self.memory.recent(n)]}

    def tasks(self, action: str = "", **kw) -> dict:
        tasks = _load_tasks()
        if action == "add":
            title = str(kw.get("title", "")).strip()
            if title:
                tasks.append({"id": int(time.time() * 1000), "title": title,
                              "done": False, "ts": datetime.now().isoformat(timespec="seconds")})
        elif action == "toggle":
            for t in tasks:
                if t["id"] == kw.get("id"):
                    t["done"] = not t["done"]
        elif action == "delete":
            tasks = [t for t in tasks if t["id"] != kw.get("id")]
        elif action == "clear_done":
            tasks = [t for t in tasks if not t["done"]]
        _save_tasks(tasks)
        return {"tasks": tasks}


# ------------------------------------------------------------------ HTTP plumbing
class Handler(BaseHTTPRequestHandler):
    api: JarvisAPI = None          # injected before serve()

    def log_message(self, *a):     # silence default access logging
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    # ---- GET
    def do_GET(self) -> None:
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/system":
            s = system_stats()
            s["jarvis"] = {"uptime_s": int(time.time() - self.api.started),
                           "interactions": self.api.memory.stats()[0]}
            s["roots"] = [k for k, p in UI_ROOTS.items() if p.exists()]
            s["device"] = self.api.device
            if q.get("privacy") in ("1", "true", "yes"):      # tester/demo mask
                s["host"] = s["ip"] = "••• private"
                for k in ("cores", "cpu", "mem", "disk", "battery", "uptime", "uptime_s"):
                    s.pop(k, None)
                s["device"] = {k: "•••" for k in s["device"]}
                s["privacy"] = True
            return self._json(s)
        if u.path == "/api/history":
            return self._json(self.api.history(int(q.get("n", "20"))))
        if u.path == "/api/skills":
            return self._json(self.api.skills())
        if u.path == "/api/tasks":
            return self._json(self.api.tasks())
        if u.path == "/api/files":
            if q.get("q"):
                return self._json(search_files(q["q"]))
            return self._json(list_folder(q.get("root", "home"), q.get("path", "")))
        # static UI files
        name = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        f = (WEB_DIR / name).resolve()
        if not f.is_relative_to(WEB_DIR.resolve()) or not f.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
                 ".svg": "image/svg+xml", ".png": "image/png"}.get(f.suffix, "text/plain")
        self._send(200, f.read_bytes(), ctype)

    # ---- POST
    def do_POST(self) -> None:
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except (json.JSONDecodeError, ValueError):
            return self._json({"error": "bad json"}, 400)
        if u.path == "/api/command":
            text = str(body.get("text", "")).strip()
            if not text:
                return self._json({"error": "empty command"}, 400)
            return self._json(self.api.command(text, bool(body.get("voice"))))
        if u.path == "/api/tasks":
            return self._json(self.api.tasks(
                action=str(body.get("action", "")), id=body.get("id"),
                title=str(body.get("title", ""))))
        if u.path == "/api/files/open":
            base = _safe_root(str(body.get("root", "home")))
            rel = str(body.get("path", ""))
            if base is None:
                return self._json({"error": "unknown root"}, 400)
            target = (base / rel).resolve() if rel else base
            if not target.is_relative_to(base.resolve()) or not target.exists():
                return self._json({"error": "not found"}, 404)
            reveal(target)
            return self._json({"opened": str(target)})
        self._json({"error": "no such endpoint"}, 404)


def serve(brain, memory, speaker) -> ThreadingHTTPServer:
    """Start the UI server on a free port and open the browser."""
    Handler.api = JarvisAPI(brain, memory, speaker)
    port = UI_PORT
    for _ in range(10):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    else:
        raise RuntimeError("No free port found for the JARVIS UI.")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    log(f"Holographic interface online:  {url}")
    reveal(url)
    return httpd
