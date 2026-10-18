"""System control — volume, apps, power, screenshots, info.
Speaks macOS (osascript), Windows (PowerShell) and Linux (CLI) through
jarvis.core.ostools. No third-party deps; unsupported tricks degrade gracefully."""
from __future__ import annotations

import glob
import os
import re
import subprocess
import tempfile
import time

from jarvis.config import log
from jarvis.core import ostools as ot
from jarvis.core.registry import command

# keep the old import path working for webserver & tests
from jarvis.core.ostools import device_profile  # noqa: F401

_MAC = ot.IS_MAC


def _osascript(script: str) -> str:
    if not _MAC:
        raise RuntimeError(f"AppleScript is not available on {ot.SYSTEM}")
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
    if "unmute" in u:
        return ot.mute(False)
    if re.search(r"mute", u):
        return ot.mute(True)
    m = re.search(r"volume (?:to |at )?(\d{1,3})", u)
    if not m:
        m = re.search(r"(\d{1,3}) ?(?:percent )?volume", u)
    if m:
        return ot.set_volume(int(m.group(1)))
    cur = ot.get_volume()
    if cur is None:
        return "This OS keeps its volume mixer out of reach, sir."
    if "up" in u:
        ot.set_volume(min(100, cur + 15))
        return "Louder."
    if "down" in u or "quieter" in u or "lower" in u:
        ot.set_volume(max(0, cur - 15))
        return "Softer."
    return f"Volume is {cur} percent."


# ------------------------------------------------------------------ apps
_APPS = ("safari|chrome|firefox|brave|opera|edge|arc|spotify|music|podcasts|tv|movies|"
         "terminal|iterm|vscode|visual studio code|cursor|sublime|xcode|finder|"
         "notes|notion|mail|messages|whatsapp|telegram|signal|line|messenger|wechat|"
         "instagram|tiktok|youtube|netflix|calendar|maps|photos|reminders|clock|"
         "preview|slack|discord|telegram|zoom|teams|webex|obs|figma|sketch|"
         "intellij|pycharm|android studio|docker|postman|tableplus|dbeaver|navicat|"
         "bitwarden|1password|obsidian|keepassxc|blender|gimp|imovie|garageband|keynote|"
         "pages|numbers|excel|word|powerpoint|outlook|onenote|vlc|iina|mpv|steam|"
         "app store|system settings|system preferences|activity monitor|task manager|"
         "mission control|launchpad|shortcuts|freeform|stocks|news|weather|home|"
         "ebook converters|calibre|transmission|qbittorrent|filezilla|wireshark")


@command("sys.open_app", "Open or quit an application by name.",
         patterns=[rf"\b(?:open|launch|start|quit|close)\s+(?:the\s+)?(?:app\s+)?{_APPS}\b"],
         examples=["open safari", "launch spotify", "start visual studio code",
                   "open system settings", "quit spotify", "close finder windows",
                   "open terminal", "launch xcode", "quit slack"],
         priority=10)
def open_app(utterance: str) -> str:
    text = utterance.lower()
    # Prefer a known app name anywhere in the phrase — polite trailing words
    # ("…for me, Jarvis?") no longer defeat extraction the way an end-anchored
    # regex did. Falls back to the words right after the verb for unknown apps.
    known = re.search(rf"\b({_APPS})\b", text)
    name = known.group(1) if known else None
    if not name:
        m = re.search(r"(?:open|launch|start|quit|close|kill)\s+"
                      r"(?:the\s+|an?\s+|app\s+)?(.*)", text)
        if m:
            fill = {"for", "me", "please", "now", "app", "application", "and",
                    "to", "up", "window", "windows", "again", "instantly", "jarvis"}
            words = []
            for w in re.findall(r"[a-z0-9][\w.-]*", m.group(1)):
                if w in fill:
                    break
                words.append(w)
            name = " ".join(words) or None
    if not name:
        return "Which application, sir?"
    if re.search(r"\b(?:quit|close|kill)\b", text) and not re.search(r"\b(?:open|launch|start)\b", text):
        return ot.quit_app(name)
    return ot.open_app(name)


# ------------------------------------------------------------------ screenshots
@command("sys.screenshot", "Capture the screen to Desktop/Pictures.",
         patterns=[r"screenshot|screen ?shot|capture (?:the )?screen"],
         examples=["take a screenshot", "capture the screen", "screen shot everything"])
def screenshot(utterance: str) -> str:
    return ot.screenshot()


# ------------------------------------------------------------------ power
@command("sys.power", "Manage sleep: stay awake, sleep, or lock.",
         patterns=[r"caffeinate|\bawake\b|(?:dont|don't|do not) sleep\s*$"
                   r"|to sleep|sleep the (?:mac|system|computer)"
                   r"|lock (?:the )?(?:screen|mac|computer)|wake up"],
         examples=["keep the mac awake for an hour", "dont let the mac sleep",
                   "put the system to sleep", "lock the screen", "stop staying awake"])
def power(utterance: str) -> str:
    if "lock" in utterance:
        return ot.lock_screen()
    if "stay awake" in utterance or "caffeinate" in utterance or "don't sleep" in utterance \
            or "dont sleep" in utterance:
        m = re.search(r"(\d+)\s*(?:minute|hour|min|h)", utterance)
        secs = int(m.group(1)) * 60 if m else 3600
        return ot.stay_awake(secs)
    if "stop" in utterance or "cancel" in utterance:
        return ot.cancel_stay_awake()
    if "sleep" in utterance:
        return ot.sleep_machine()
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
        b = ot.battery()
        if b["pct"] is None:
            return "This machine has no battery, sir."
        return f"Battery at {b['pct']} percent, {b['state']}."
    if "ip" in u:
        return f"Local IP is {ot.local_ip()}."
    if "uptime" in u:
        secs = ot.uptime_secs()
        days, rem = divmod(secs, 86400)
        hours, minutes = divmod(rem, 3600)
        return f"Uptime {days} days, {hours} hours, {minutes} minutes."
    if "disk" in u:
        d = ot.disk()
        return f"Disk: {d.get('used', '?')} used of {d.get('total', '?')}, {d.get('free', '?')} available ({d.get('pct', '?')} used)."
    load = ot.loadavg()
    free_mb = ot.mem().get("free_mb", 0)
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
    before = ot.mem().get("free_mb", 0)
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
    for clip in glob.glob(os.path.join(tempfile.gettempdir(), "jarvis_tts_*", "*.mp3")):
        try:
            if time.time() - os.path.getmtime(clip) > 3600:
                os.remove(clip)
                removed += 1
        except OSError:
            pass
    if removed:
        did.append(f"purged {removed} stale voice clips")
    # 3 — report the real hogs
    hogs = [f"{name} {pct}%" for name, pct in ot.top_mem_processes(4)]
    after = ot.mem().get("free_mb", 0)
    gained = max(0, after - before)
    if not did:
        did.append("no idle models to unload")
    hint = " Say 'find files untouched for 6 months' if you want a stale-file scan (nothing deleted without approval)." \
        if "unused files" in utterance else ""
    return (f"Freed {gained} MB — memory now at {after} MB free. Did: {', '.join(did)}. "
            f"Top memory users: {', '.join(hogs)}, sir.{hint}")


# ------------------------------------------------------------------ device identity
@command("sys.device", "Full machine identity: OS version, model, chip, memory, serial.",
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
    return (f"{p.get('name', 'Machine')}: {p.get('os', '')} {p.get('os_version', '')} "
            f"{('(build ' + p['build'] + ')') if p.get('build') else ''}, "
            f"{p.get('model', '')} with {p.get('chip', 'unknown CPU')}, "
            f"{p.get('cores', '?')} cores, {p.get('memory_gb', '?')} GB RAM. "
            f"Serial {p.get('serial', '—')}. All recorded, sir.")


# ------------------------------------------------------------------ do not disturb
@command("sys.dnd", "Toggle Focus / Do Not Disturb.",
         patterns=[r"do not disturb|focus mode"],
         examples=["turn on do not disturb", "enable focus", "disable do not disturb"])
def dnd(utterance: str) -> str:
    on = any(k in utterance for k in ("on", "enable", "turn on", "start"))
    if not _MAC:
        where = ("Windows calls it Focus Assist — flip it in the notification tray"
                 if ot.IS_WIN else "Linux focus modes vary by desktop environment")
        return f"{where}, sir. Automatic toggling isn't exposed on {ot.SYSTEM}."
    verb = "enable" if on else "disable"
    r = subprocess.run([
        "osascript", "-e",
        f'tell application "Control Center" to click menu item "{verb} Focus"'],
        capture_output=True, text=True)
    if r.returncode == 0:
        return f"Do Not Disturb {verb}d."
    return "Open Control Center manually, sir — Focus toggles need Accessibility permission."
