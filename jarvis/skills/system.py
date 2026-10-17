"""macOS system control — volume, brightness, apps, power, screenshots, info.
Uses osascript/AppleScript and native CLIs. No third-party deps."""
from __future__ import annotations

import os
import re
import subprocess
import time

from jarvis.config import log
from jarvis.core.registry import command


def _osascript(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


# ------------------------------------------------------------------ volume
@command("sys.volume", "Set or read output volume.",
         patterns=[r"\bvol(ume)?\b"],
         examples=["set volume to fifty", "turn it up", "make it quieter",
                   "mute", "volume up", "what is the volume", "set volume eighty"])
def volume(utterance: str) -> str:
    u = utterance
    if re.search(r"mute", u):
        _osascript("set volume output muted true")
        return "Muted."
    if re.search(r"unmute", u):
        _osascript("set volume output muted false")
        return "Unmuted."
    m = re.search(r"volume (?:to |at )?(\d{1,3})", u)
    if not m:
        m = re.search(r"(\d{1,3}) ?(?:percent )?volume", u)
    if m:
        v = max(0, min(100, int(m.group(1))))
        _osascript(f"set volume output volume {v}")
        return f"Volume {v} percent."
    if "up" in u:
        cur = int(_osascript("output volume of (get volume settings)"))
        _osascript(f"set volume output volume {min(100, cur + 15)}")
        return "Louder."
    if "down" in u or "quieter" in u or "lower" in u:
        cur = int(_osascript("output volume of (get volume settings)"))
        _osascript(f"set volume output volume {max(0, cur - 15)}")
        return "Softer."
    cur = int(_osascript("output volume of (get volume settings)"))
    return f"Volume is {cur} percent."


# ------------------------------------------------------------------ apps
_APPS = ("safari|chrome|firefox|arc|spotify|music|terminal|iterm|vscode|"
         "visual studio code|xcode|finder|notes|mail|messages|calendar|"
         "preview|slack|discord|zoom|obs|intellij|pycharm|docker|activity monitor")


@command("sys.open_app", "Open or quit an application by name.",
         patterns=[rf"\b(?:open|launch|start|quit|close)\s+(?:the\s+)?(?:app\s+)?{_APPS}\b"],
         examples=["open safari", "launch spotify", "start visual studio code",
                   "open system settings", "quit spotify", "close finder windows",
                   "open terminal", "launch xcode", "quit slack"],
         priority=10)
def open_app(utterance: str) -> str:
    m = re.search(r"(?:open|launch|start)\s+(?:the\s+|app\s+)?([\w ]+?)(?:\s+(?:app|application))?$",
                  utterance)
    if not m:
        m = re.search(r"(?:quit|close|kill)\s+(?:the\s+|app\s+)?([\w ]+)", utterance)
        if m:
            name = m.group(1).strip()
            _osascript(f'tell application "{name}" to quit')
            return f"Quit {name}."
        return "Which application, sir?"
    name = m.group(1).strip()
    if name in ("safari", "finder", "messages", "music", "mail", "calendar",
                "notes", "terminal", "activity monitor"):
        pass  # common names map directly
    try:
        _osascript(f'tell application "{name}" to activate')
        return f"Opening {name}."
    except RuntimeError:
        # try by file name in /Applications
        r = subprocess.run(["open", "-a", name], capture_output=True, text=True)
        if r.returncode == 0:
            return f"Opening {name}."
        return f"I couldn't find {name}."


# ------------------------------------------------------------------ screenshots
@command("sys.screenshot", "Capture the screen to Downloads.",
         patterns=[r"screenshot|screen ?shot|capture (?:the )?screen"],
         examples=["take a screenshot", "capture the screen", "screen shot everything"])
def screenshot(utterance: str) -> str:
    path = os.path.expanduser(f"~/Desktop/JARVIS_shot_{int(time.time())}.png")
    r = subprocess.run(["screencapture", "-x", path], capture_output=True, text=True)
    if r.returncode == 0:
        return f"Screenshot saved to your Desktop."
    return f"Screenshot failed: {r.stderr.strip()}"


# ------------------------------------------------------------------ power
@command("sys.power", "Manage sleep: stay awake, sleep, or lock.",
         patterns=[r"caffeinate|\bawake\b|(?:dont|don't|do not) sleep\s*$"
                   r"|to sleep|sleep the (?:mac|system|computer)"
                   r"|lock (?:the )?(?:screen|mac|computer)|wake up"],
         examples=["keep the mac awake for an hour", "dont let the mac sleep",
                   "put the system to sleep", "lock the screen", "stop staying awake"])
def power(utterance: str) -> str:
    if "lock" in utterance:
        _osascript('tell application "System Events" to key code 12 using {control down, command down}')
        return "Screen locked."
    if "stay awake" in utterance or "caffeinate" in utterance or "don't sleep" in utterance \
            or "dont sleep" in utterance:
        m = re.search(r"(\d+)\s*(?:minute|hour|min|h)", utterance)
        secs = int(m.group(1)) * 60 if m else 3600
        subprocess.Popen(["caffeinate", "-dis", str(secs)])
        return f"I'll keep this Mac awake for {secs // 60} minute(s)."
    if "stop" in utterance or "cancel" in utterance:
        subprocess.run(["pkill", "-f", "caffeinate"], check=False)
        return "Normal sleep behaviour restored."
    if "sleep" in utterance:
        _osascript('tell application "System Events" to sleep')
        return "Going to sleep. Goodbye."
    return "Say: stay awake for thirty minutes, lock the screen, or sleep."


# ------------------------------------------------------------------ system info
@command("sys.info", "Report CPU, memory, battery, network, uptime.",
         patterns=[r"\b(battery|cpu|memory|ram|uptime|ip address|disk|weather system)\b",
                   r"how('?s| is) (?:the )?system|system (?:status|report)"],
         examples=["what is the battery level", "how's the cpu", "system status",
                   "how much ram is left", "what is my ip address", "disk space",
                   "whats the uptime"])
def sysinfo(utterance: str) -> str:
    u = utterance
    if "battery" in u:
        pct = subprocess.run(["pmset", "-g", "batt"],
                             capture_output=True, text=True).stdout
        m = re.search(r"(\d+)%", pct)
        if not m:
            return "This Mac has no battery, sir."
        low = pct.lower()
        state = ("charging" if "charging" in low else
                 "fully charged" if "charged" in low else "on battery")
        cond = re.search(r"(?:Now charging|[-*]\s*(\d+:\d+)\s*(?:remaining))", low)
        extra = f", {cond.group(1)} remaining" if cond else ""
        return f"Battery at {m.group(1)} percent, {state}{extra}."
    if "ip" in u:
        ip = subprocess.run(["ipconfig", "getifaddr", "en0"],
                            capture_output=True, text=True).stdout.strip()
        return f"Local IP is {ip or 'not available'}."
    if "uptime" in u:
        boot = subprocess.run(["sysctl", "-n", "kern.boottime"],
                              capture_output=True, text=True).stdout
        m = re.search(r"sec = (\d+)", boot)
        if not m:
            return "Boot time unknown."
        secs = int(time.time()) - int(m.group(1))
        days, rem = divmod(secs, 86400)
        hours, minutes = divmod(rem, 3600)
        return f"Uptime {days} days, {hours} hours, {minutes} minutes."
    if "disk" in u:
        out = subprocess.run(["df", "-h", "/"], capture_output=True, text=True).stdout
        parts = out.splitlines()[1].split()   # Fs Size Used Avail %cap Mount
        return f"Disk: {parts[2]} used of {parts[1]}, {parts[3]} available ({parts[4]} used)."
    # general report
    load = os.getloadavg()
    mem = subprocess.run(["memory_pressure"], capture_output=True, text=True).stdout
    used = re.search(r"compressor[^\d]*(\d+)", mem)
    pages = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    free_m = re.search(r"Pages free:\s+(\d+)", pages)
    free_mb = int(free_m.group(1)) * 4096 // 1024 // 1024 if free_m else 0
    return (f"Load {load[0]:.2f}, {load[1]:.2f}, {load[2]:.2f}. "
            f"Free memory {free_mb} megabytes. All systems nominal, sir.")


# ------------------------------------------------------------------ free memory
@command("sys.freemem", "Actively free RAM: unload idle LLM models, purge stale clips, report top hogs.",
         patterns=[r"free[ ,\w]{0,14}?(?:memory|ram)|(?:memory|ram)[ ,\w]{0,6}\bup\b"
                   r"|clean(?: ?up)? (?:my |the )?(?:ram|memory)|purge|out of memory"
                   r"|eating (?:my |up my )?ram|unused files"],
         examples=["free some of my memory up", "clean up my ram", "my mac is out of memory",
                   "purge caches", "whats eating my ram", "free memory now"])
def freemem(utterance: str) -> str:
    def free_mb() -> int:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
        ps = re.search(r"page size of (\d+)", out)
        ps = int(ps.group(1)) if ps else 16384
        n = lambda pat: int(re.search(pat, out).group(1)) if re.search(pat, out) else 0
        return (n(r"Pages free:\s+(\d+)") + n(r"Pages speculative:\s+(\d+)")) * ps // 1048576

    before = free_mb()
    did = []
    # 1 — unload Ollama models hoarding RAM (they reload on next chat, first answer slower)
    try:
        listing = subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout
        for line in listing.splitlines()[1:]:
            name = line.split()[0] if line.split() else ""
            if name and "/" not in name and name != "Error":
                subprocess.run(["ollama", "stop", name], capture_output=True)
                did.append(f"unloaded {name}")
    except OSError:
        pass
    # 2 — purge stale JARVIS voice clips from temp
    removed = 0
    import glob, time as _t
    for clip in glob.glob("/tmp/jarvis_tts_*/*.mp3"):
        try:
            if _t.time() - os.path.getmtime(clip) > 3600:
                os.remove(clip)
                removed += 1
        except OSError:
            pass
    if removed:
        did.append(f"purged {removed} stale voice clips")
    # 3 — report the real hogs
    rows = subprocess.run(["ps", "aux", "-m"], capture_output=True, text=True).stdout.splitlines()[1:6]
    hogs = []
    for l in rows:
        c = l.split()
        if len(c) > 10:
            hogs.append(f"{c[10].split('/')[-1]} {float(c[3]):.0f}%")
    after = free_mb()
    gained = max(0, after - before)
    if not did:
        did.append("no idle models to unload")
    hint = " Say 'find files untouched for 6 months' if you want a stale-file scan (nothing deleted without approval)." \
        if "unused files" in utterance else ""
    return (f"Freed {gained} MB — memory now at {after} MB free. Did: {', '.join(did)}. "
            f"Top memory users: {', '.join(hogs[:4])}, sir.{hint}")


# ------------------------------------------------------------------ device identity
def device_profile() -> dict:
    """Full laptop fingerprint — collected once, remembered forever."""
    prof = {"name": _osascript_safe("scutil", "--get", "ComputerName")}
    prof["os"] = _osascript_safe("sw_vers", "-productName")
    prof["os_version"] = _osascript_safe("sw_vers", "-productVersion")
    prof["build"] = _osascript_safe("sw_vers", "-buildVersion")
    prof["model"] = _osascript_safe("sysctl", "-n", "hw.model")
    prof["chip"] = _osascript_safe("sysctl", "-n", "machdep.cpu.brand_string")
    prof["cores"] = os.cpu_count() or 0
    try:
        prof["memory_gb"] = round(int(_osascript_safe("sysctl", "-n", "hw.memsize")) / 1024**3, 1)
    except ValueError:
        prof["memory_gb"] = 0
    try:
        ior = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                             capture_output=True, text=True).stdout
        m = re.search(r'"IOPlatformSerialNumber"\s*=\s*"([^"]+)"', ior)
        prof["serial"] = m.group(1) if m else "—"
        m = re.search(r'"board-id"\s*=\s*"([^"]+)"', ior)
        prof["board"] = m.group(1) if m else "—"
    except OSError:
        prof["serial"] = prof["board"] = "—"
    return prof


def _osascript_safe(*cmd: str) -> str:
    try:
        return subprocess.run(list(cmd), capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


@command("sys.device", "Full laptop identity: macOS version, model, chip, memory, serial.",
         patterns=[r"device (?:profile|info|status)|laptop (?:status|specs?|info|profile)"
                   r"|what (?:os|mac|computer)\b|machine (?:specs?|info)|serial number"
                   r"|(?:tell me about|whats) my mac|system profile|hardware"],
         examples=["what os am i running", "device profile", "laptop specs",
                   "what is my mac serial number", "tell me about my mac hardware",
                   "what machine is this"])
def device(utterance: str) -> str:
    from jarvis.core.memory import Memory
    m = Memory()
    p = m.recall("device_profile")
    if not p:
        p = device_profile()
        m.remember("device_profile", p)   # remember it from now on
    return (f"{p.get('name', 'Mac')}: {p.get('os', 'macOS')} {p.get('os_version', '')} "
            f"(build {p.get('build', '?')}), {p.get('model', 'Mac')} with "
            f"{p.get('chip', 'Apple Silicon')}, {p.get('cores', '?')} cores, "
            f"{p.get('memory_gb', '?')} GB RAM. Serial {p.get('serial', '—')}, "
            f"board {p.get('board', '—')}. All recorded, sir.")


# ------------------------------------------------------------------ do not disturb
@command("sys.dnd", "Toggle Focus / Do Not Disturb.",
         patterns=[r"do not disturb|focus mode"],
         examples=["turn on do not disturb", "enable focus", "disable do not disturb"])
def dnd(utterance: str) -> str:
    on = any(k in utterance for k in ("on", "enable", "turn on", "start"))
    verb = "enable" if on else "disable"
    r = subprocess.run([
        "osascript", "-e",
        f'tell application "Control Center" to click menu item "{verb} Focus"'],
        capture_output=True, text=True)
    if r.returncode == 0:
        return f"Do Not Disturb {verb}d."
    return "Open Control Center manually, sir — Focus toggles need Accessibility permission."
