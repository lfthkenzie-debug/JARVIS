"""OS adapter — lets one JARVIS speak macOS, Windows and Linux natively.

Every OS-specific trick in the codebase funnels through this single file.
Each helper degrades gracefully and returns something a skill can relay
verbatim, so an unsupported feature reads as a witty refusal, not a crash.
No third-party dependencies — pure stdlib + the tools each OS ships."""
from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SYSTEM = {"darwin": "macos", "win32": "windows", "cygwin": "windows",
          "msys": "windows"}.get(sys.platform, "linux")
IS_MAC, IS_WIN, IS_LINUX = SYSTEM == "macos", SYSTEM == "windows", SYSTEM == "linux"
HOME = Path.home()


# ------------------------------------------------------------------ runners
def out(cmd: list[str], timeout: int = 30) -> str:
    """Run a command, return stdout (stripped); '' on any failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def code(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except OSError as e:
        return subprocess.CompletedProcess(cmd, returncode=127, stdout="", stderr=str(e))
    except subprocess.TimeoutExpired:
        # a hung child must degrade like any other failure, not crash the skill
        return subprocess.CompletedProcess(cmd, returncode=124, stdout="",
                                           stderr=f"timed out after {timeout}s")


def ps(script: str, timeout: int = 40) -> str:
    """PowerShell one-liner (Windows only)."""
    return out(["powershell", "-NoProfile", "-Command", script], timeout)


def spawn(cmd: list[str]):
    """Fire-and-forget background process; returns Popen or None."""
    try:
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return None


def first_tool(*names: str) -> str | None:
    for n in names:
        if shutil.which(n):
            return n
    return None


def playback_available() -> bool:
    """Can this machine play an mp3 from the command line?"""
    if IS_MAC:
        return shutil.which("afplay") is not None
    if IS_WIN:
        return True
    return first_tool("mpv", "ffplay", "cvlc", "paplay", "aplay") is not None


def native_tts_available() -> bool:
    """Does the OS ship a text-to-speech command?"""
    if IS_MAC:
        return shutil.which("say") is not None
    if IS_WIN:
        return shutil.which("powershell") is not None
    return first_tool("espeak-ng", "espeak", "spd-say", "festival") is not None


# ------------------------------------------------------------------ open / media
_EXEC_EXT = {".bat", ".cmd", ".com", ".exe", ".scr", ".lnk", ".pif", ".msi",
             ".app", ".desktop", ".sh", ".bash", ".zsh", ".command", ".workflow"}


def open_target(target) -> bool:
    """Open a file/folder/http(s)-URL with the OS default handler.

    Hardened gate: everything JARVIS reveals funnels through here, so the
    input is validated before any OS dispatch — no executables, no odd URI
    schemes, no control characters, and paths are resolved (symlink-safe)."""
    t = str(target or "").strip()
    if not t or "\n" in t or "\x00" in t:
        return False
    if t.startswith(("http://", "https://")):
        pass                                          # plain web URLs are fine
    elif "://" in t:
        return False                                  # file:, smb:, jar:, data: …
    else:
        p = Path(t).expanduser()
        if not p.exists():
            return False
        p = p.resolve()                               # collapse symlinks/..
        if p.is_file() and (p.suffix.lower() in _EXEC_EXT or os.access(p, os.X_OK)):
            return False                              # never launch binaries
        t = str(p)
    if IS_MAC:
        return code(["open", t]).returncode == 0
    if IS_WIN:
        try:
            os.startfile(t)  # type: ignore[attr-defined]
            return True
        except OSError:
            return code(["cmd", "/c", "start", "", t]).returncode == 0
    if shutil.which("xdg-open"):
        return code(["xdg-open", t]).returncode == 0
    if shutil.which("gio"):
        return code(["gio", "open", t]).returncode == 0
    return False


def play_media(path: str):
    """Play an audio file in the background; returns Popen or None."""
    if IS_MAC:
        return spawn(["afplay", path]) if shutil.which("afplay") else None
    if IS_WIN:
        player = first_tool("ffplay", "mpv")
        if player:
            flag = ["-nodisp", "-autoexit", "-loglevel", "quiet"] if player == "ffplay" else ["--no-video"]
            return spawn([player, *flag, path])
        return spawn(["cmd", "/c", "start", "", "/MIN", path])
    player = first_tool("mpv", "ffplay", "cvlc", "paplay", "aplay")
    if not player:
        return None
    flag = {"mpv": ["--no-video"],
            "ffplay": ["-nodisp", "-autoexit", "-loglevel", "quiet"],
            "cvlc": ["-q", "--play-and-exit"]}.get(player, [])
    return spawn([player, *flag, path])


def speak_native(text: str, rate: int = 188, voice: str = "Daniel"):
    """Last-resort TTS using whatever the OS ships. Returns Popen or None."""
    safe = text.replace("'", "").replace('"', "")[:400]
    if IS_MAC:
        return spawn(["say", "-r", str(rate), "-v", voice, safe])
    if IS_WIN:
        script = ("Add-Type -AssemblyName System.Speech; "
                  "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"$s.Rate=2; $s.Speak('{safe}')")
        return spawn(["powershell", "-NoProfile", "-Command", script])
    tool = first_tool("espeak-ng", "espeak", "spd-say", "festival")
    if not tool:
        return None
    if tool == "festival":
        try:
            p = subprocess.Popen([tool, "--tts"], stdin=subprocess.PIPE,
                                 text=True, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            p.stdin.write(safe)
            p.stdin.close()
            return p
        except OSError:
            return None
    cmd = {"espeak-ng": ["-v", "en-gb", "-s", str(rate)],
           "espeak": ["-v", "en-gb", "-s", str(rate)],
           "spd-say": ["-l", "en-GB"]}.get(tool, [])
    return spawn([tool, *cmd, safe])


# ------------------------------------------------------------------ audio mixer
def get_volume() -> int | None:
    if IS_MAC:
        v = out(["osascript", "-e", "output volume of (get volume settings)"])
        return int(v) if v.isdigit() else None
    if IS_WIN:
        # Windows ships no volume CLI — NirCmd (nircmd.com) unlocks this if installed.
        n = out(["nircmd", "getsoundvolume", "master"], timeout=8)
        m = re.search(r"\d+", n)
        return min(100, int(m.group()) * 100 // 65535) if m else None
    if first_tool("pactl"):
        t = out(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        m = re.search(r"(\d+)%", t)
        return int(m.group(1)) if m else None
    t = out(["amixer", "get", "Master"])
    m = re.search(r"\[(\d+)%\]", t)
    return int(m.group(1)) if m else None


def set_volume(pct: int) -> str:
    pct = max(0, min(100, pct))
    if IS_MAC:
        ok = code(["osascript", "-e", f"set volume output volume {pct}"]).returncode == 0
    elif IS_WIN:
        ok = code(["nircmd", "setsoundvolume", str(pct * 65535 // 100)]).returncode == 0
        if not ok:
            return ("Windows has no built-in volume CLI, sir — install NirCmd "
                    "(nircmd.com) and I'll gladly obey.")
    elif first_tool("pactl"):
        ok = code(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"]).returncode == 0
    elif first_tool("amixer"):
        ok = code(["amixer", "sset", "Master", f"{pct}%"]).returncode == 0
    else:
        return "No mixer tool found (need pactl or amixer), sir."
    return f"Volume {pct} percent." if ok else "The machine refused, sir."


def mute(on: bool) -> str:
    if IS_MAC:
        ok = code(["osascript", "-e", f"set volume output muted {'true' if on else 'false'}"]).returncode == 0
    elif IS_WIN:
        ok = code(["nircmd", "mutesound" if on else "unmutesound"]).returncode == 0
    elif first_tool("pactl"):
        ok = code(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if on else "0"]).returncode == 0
    elif first_tool("amixer"):
        ok = code(["amixer", "sset", "Master", "mute" if on else "unmute"]).returncode == 0
    else:
        return "No mixer tool found on this machine, sir."
    return ("Muted." if on else "Unmuted.") if ok else "The machine refused, sir."


# ------------------------------------------------------------------ apps
_APP_ALIASES_WIN = {"notes": "notepad", "terminal": "wt", "command prompt": "cmd",
                    "explorer": "explorer", "settings": "start ms-settings:",
                    "calculator": "calc", "task manager": "taskmgr"}
_APP_ALIASES_LINUX = {"terminal": "gnome-terminal", "files": "nautilus",
                      "calculator": "gnome-calculator", "text editor": "gedit"}
# Spoken name -> real macOS bundle display name for the awkward ones a fuzzy
# search can't bridge (e.g. "vs code" is nowhere inside "Visual Studio Code").
_APP_ALIASES_MAC = {
    "vs code": "Visual Studio Code", "vsc": "Visual Studio Code",
    "code": "Visual Studio Code", "sublime": "Sublime Text",
    "chrome": "Google Chrome", "activity": "Activity Monitor",
    "task manager": "Activity Monitor", "system settings": "System Settings",
    "system preferences": "System Settings", "preferences": "System Settings",
    "app store": "App Store", "activity monitor": "Activity Monitor",
    "illustrator": "Adobe Illustrator", "photoshop": "Adobe Photoshop",
    "acrobat": "Adobe Acrobat Reader",
}


def _clean_app_token(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (name or "").lower()).strip()


def _mac_app_score(query: str, disp: str) -> int:
    q, d = _clean_app_token(query), _clean_app_token(disp)
    if not q or not d:
        return 9
    if q == d:
        return 0
    if d.startswith(q):                 # spoken name is the head of the real one
        return 1
    if q in d:                          # "photoshop" inside "adobe photoshop 2024"
        return 2
    # every spoken word present (ignoring 'adobe','2024','pro' filler)
    if all(w in d for w in q.split()):
        return 2
    return 9


def _mac_find_app(name: str) -> str | None:
    """Resolve a spoken app name to a real installed .app path.
    Strategies: alias map -> /Applications scan -> Spotlight (mdfind, which also
    sees nested/renamed bundles like Adobe's) -> typo-tolerant closest match.
    Returns a path or None; does not launch."""
    q = _APP_ALIASES_MAC.get(_clean_app_token(name), name.strip())
    seen: dict[str, str] = {}          # cleaned display name -> path (for fuzzy tier)
    # scan the standard Applications folders
    best, bs = None, 9
    for root in ("/Applications", "/System/Applications", str(HOME / "Applications"),
                 "/Applications/Utilities", "/System/Applications/Utilities"):
        try:
            for d in Path(root).glob("*.app"):
                disp = d.name[:-4]
                s = _mac_app_score(q, disp)
                if s < bs:
                    bs, best = s, str(d)
                seen.setdefault(_clean_app_token(disp), str(d))
        except OSError:
            continue
    if best and bs <= 2:
        return best
    # Spotlight: matches display name substring, incl. apps nested in vendor folders
    if shutil.which("mdfind"):
        n = _clean_app_token(q)
        if n:
            cands: list[str] = []
            for term in [n] + n.split():
                hit = out(["mdfind",
                           "kMDItemContentType == 'com.apple.application-bundle' && "
                           f"kMDItemDisplayName == '*{term}*'cd"])
                cands += [l.strip() for l in hit.splitlines() if l.strip().endswith(".app")]
                if cands:
                    break
            for c in cands:
                seen.setdefault(_clean_app_token(Path(c).name[:-4]), c)
            scored = sorted({c for c in cands}, key=lambda p: (_mac_app_score(q, Path(p).name[:-4]), len(p)))
            if scored and _mac_app_score(q, Path(scored[0]).name[:-4]) <= 2:
                return scored[0]
    # typo-tolerant last resort: closest installed app name (e.g. "calender"->"Calendar").
    # cutoff kept high so a short system app never swallows a longer distinct query
    # ("photoshop" must NOT match "photos").
    qc = _clean_app_token(q)
    if qc and seen:
        close = difflib.get_close_matches(qc, list(seen), n=1, cutoff=0.86)
        if close:
            return seen[close[0]]
    return None


def find_app_path(name: str) -> str | None:
    """Best-effort check: is `name` a launchable installed application? Returns a
    path/command or None. The router uses this so 'open <any installed app>' runs the
    real action instead of being misrouted to chat (which would only pretend)."""
    if not _clean_app_token(name):
        return None
    if IS_MAC:
        return _mac_find_app(name)
    if IS_LINUX:
        key = _clean_app_token(name)
        real = _APP_ALIASES_LINUX.get(key, key)
        return first_tool(real, real.replace(" ", "-"))
    return None


def open_app(name: str) -> str:
    key = name.lower().strip()
    if IS_MAC:
        # `open -a` (LaunchServices) is the only reliable way to LAUNCH a GUI app
        # from a background/detached process; osascript "activate" silently no-ops
        # (yet returns rc 0) when JARVIS runs outside an interactive Terminal session.
        q = _APP_ALIASES_MAC.get(_clean_app_token(name), name.strip())
        if code(["open", "-a", q]).returncode == 0:
            return f"Opening {q}."
        path = _mac_find_app(name)
        if path and code(["open", path]).returncode == 0:
            return f"Opening {Path(path).name[:-4]}."
        return f"I couldn't find “{name}” installed, sir. Give me its exact name, or install it first."
    exe = _APP_ALIASES_WIN.get(key, key) if IS_WIN else _APP_ALIASES_LINUX.get(key, key)
    if IS_WIN:
        if code(["cmd", "/c", "start", "", exe]).returncode == 0:
            return f"Opening {name}."
    else:
        real = first_tool(exe, key.replace(" ", "-"))
        if real:
            spawn([real])
            return f"Opening {name}."
        if first_tool("gtk-launch") and code(["gtk-launch", key.replace(" ", "")]).returncode == 0:
            return f"Opening {name}."
    return f"I couldn't find {name} on this {SYSTEM}, sir."


def _mac_proc_running(exe_prefix: str) -> bool:
    """True if any live process's executable path starts with exe_prefix
    (the '<Bundle>.app/Contents/MacOS/' directory of the resolved bundle)."""
    return any(line.strip().startswith(exe_prefix)
               for line in out(["ps", "-axww", "-o", "comm="]).splitlines())


def _mac_wait_exit(exe_prefix: str, secs: float = 4.0) -> bool:
    """Poll until the app's process disappears. True if it exited."""
    deadline = time.time() + secs
    while time.time() < deadline:
        if not _mac_proc_running(exe_prefix):
            return True
        time.sleep(0.4)
    return not _mac_proc_running(exe_prefix)


def quit_app(name: str) -> str:
    key = name.lower().strip()
    if IS_MAC:
        # A blind `osascript … to quit` is a trap: if the app ISN'T running
        # AppleScript launches it just to send the message and can hang for 30s+
        # (same for a pending Automation-permission prompt). So: resolve the real
        # bundle first, check it's actually running, give AppleScript a SHORT fuse,
        # then fall back to SIGTERM pinned to the exact bundle path.
        path = _mac_find_app(name)
        disp = Path(path).name[:-4] if path else name.strip().title()
        if path:
            exe_prefix = str(Path(path) / "Contents" / "MacOS") + "/"
            if not _mac_proc_running(exe_prefix):
                return f"{disp} isn't running, sir."
            code(["osascript", "-e", f'tell application "{disp}" to quit'], timeout=8)
            if _mac_wait_exit(exe_prefix):
                return f"Quit {disp}."
            code(["pkill", "-f", re.escape(exe_prefix)], timeout=6)   # SIGTERM, path-pinned
            if _mac_wait_exit(exe_prefix):
                return f"Quit {disp}."
            return f"{disp} refuses to close, sir — it may be waiting on a dialog."
        # no identifiable bundle: exact-name SIGTERM, never osascript's launch-trap
        r = code(["pkill", "-i", "-x", _clean_app_token(name) or key], timeout=6)
        return f"Quit {disp}." if r.returncode == 0 \
            else f"{disp} wasn't running, or I couldn't reach it."
    pats = {**_APP_ALIASES_WIN, **_APP_ALIASES_LINUX}.get(key, key)
    if IS_WIN:
        r = ps(f"Stop-Process -Name {pats} -Force -ErrorAction SilentlyContinue;"
               f"if ($?) {{ 'ok' }} else {{ 'fail' }}")
        return f"Quit {name}." if "ok" in r else f"{name} wasn't running, or I couldn't reach it."
    r = code(["pkill", "-fi", pats])
    return f"Quit {name}." if r.returncode == 0 \
        else f"{name} wasn't running, or I couldn't reach it."


# ------------------------------------------------------------------ power & screen
def lock_screen() -> str:
    if IS_MAC:
        ok = code(["osascript", "-e",
                    'tell application "System Events" to key code 12 using {control down, command down}']).returncode == 0
    elif IS_WIN:
        ok = code(["rundll32.exe", "user32.dll,LockWorkStation"], timeout=8).returncode == 0
    else:
        ok = any(code(c).returncode == 0 for c in
                 (["loginctl", "lock-session"],
                  ["dbus-send", "--session", "--type=call", "org.freedesktop.ScreenSaver",
                   "--type=method_call", "/ScreenSaver", "org.freedesktop.ScreenSaver.Lock"],
                  ["xdg-screensaver", "lock"]))
    return "Screen locked." if ok else "This machine won't let me lock it, sir."


def sleep_machine() -> str:
    if IS_MAC:
        ok = code(["osascript", "-e", 'tell application "System Events" to sleep']).returncode == 0
    elif IS_WIN:
        ok = code(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], timeout=8).returncode == 0
    else:
        ok = code(["systemctl", "suspend"]).returncode == 0
    return "Going to sleep. Goodbye." if ok else "Sleep command refused, sir."


AWAKE_PIDFILE = Path(os.getenv("TEMP", "/tmp")) / "jarvis_awake.pid"


def stay_awake(secs: int) -> str:
    if IS_MAC:
        p = spawn(["caffeinate", "-dis", str(secs)])
    elif IS_WIN:
        script = ("Add-Type -MemberDefinition '[DllImport(\"kernel32.dll\")] "
                  "public static extern uint SetThreadExecutionState(uint e);' "
                  "-Name PW -Namespace J; [J.PW]::SetThreadExecutionState(0x80000003); "
                  f"Start-Sleep -Seconds {secs}; "
                  "[J.PW]::SetThreadExecutionState(0x80000000)")
        p = spawn(["powershell", "-NoProfile", "-Command", script])
    else:
        p = spawn(["systemd-inhibit", "--what=idle:sleep", "--mode=block",
                   "--who=JARVIS", "--why=staying awake", "sleep", str(secs)])
    if not p:
        return "No supported way to hold this machine awake, sir."
    AWAKE_PIDFILE.write_text(str(p.pid))
    return f"I'll keep this machine awake for {secs // 60} minute(s)."


def cancel_stay_awake() -> str:
    pid = AWAKE_PIDFILE.read_text().strip() if AWAKE_PIDFILE.exists() else ""
    if pid:
        if IS_WIN:
            code(["taskkill", "/PID", pid, "/T", "/F"])
        else:
            code(["kill", pid])
        AWAKE_PIDFILE.unlink(missing_ok=True)
        return "Normal sleep behaviour restored."
    if IS_MAC:
        code(["pkill", "-f", "caffeinate"])
    elif IS_LINUX:
        code(["pkill", "-f", "systemd-inhibit"])
    return "Normal sleep behaviour restored."


def screenshot() -> str:
    stamp = int(time.time())
    if IS_MAC:
        path = HOME / f"Desktop/JARVIS_shot_{stamp}.png"
        ok = code(["screencapture", "-x", str(path)]).returncode == 0
    elif IS_WIN:
        path = HOME / f"Desktop/JARVIS_shot_{stamp}.png"
        script = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                  "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
                  "$bmp=New-Object Drawing.Bitmap $b.Width,$b.Height;"
                  "$g=[Drawing.Graphics]::FromImage($bmp);"
                  "$g.CopyFromScreen($b.Location,[Drawing.Point]::Empty,$b.Size);"
                  f"$bmp.Save('{path}'); 'ok'")
        ok = "ok" in ps(script)
    else:
        path = HOME / f"Pictures/JARVIS_shot_{stamp}.png"
        ok = any(code(c).returncode == 0 for c in
                 (["gnome-screenshot", "-f", str(path)], ["spectacle", "-b", "-n", "-o", str(path)],
                  ["scrot", str(path)], ["import", "-window", "root", str(path)]))
    return f"Screenshot saved: {path}" if ok else "Screenshot tools unavailable on this machine, sir."


# ------------------------------------------------------------------ activity probes
_MAC_TITLE_OK = True    # flips False after a denied/timed-out title probe (TCC trap)


def frontmost_app() -> tuple[str, str]:
    """(app name, window title) of whatever the user is looking at right now.
    macOS app name comes from lsappinfo (LaunchServices) — no Automation prompt.
    Window titles need System Events: ONE short-fused probe; if it is denied or
    hangs we drop titles silently for the rest of the session (never repeats the
    30-second osascript trap that broke 'quit calendar')."""
    global _MAC_TITLE_OK
    if IS_MAC:
        asn = out(["lsappinfo", "front"], timeout=3)
        name = ""
        if asn and asn != "NULL":
            raw = out(["lsappinfo", "info", "-only", "name", asn], timeout=3)
            m = re.search(r'"(?:LSDisplayName|AXCaption|name)"="([^"]+)"', raw) \
                or re.search(r'="([^"]+)"', raw)
            name = m.group(1) if m else ""
        title = ""
        if _MAC_TITLE_OK and name:
            r = code(["osascript", "-e",
                      'tell application "System Events" to get name of window 1 of '
                      'first application process whose frontmost is true'], timeout=4)
            if r.returncode == 0:
                title = (r.stdout or "").strip()[:160]
            else:
                _MAC_TITLE_OK = False      # denied or unscriptable app — stop asking
        return name, title
    if IS_WIN:
        script = ("Add-Type -Namespace W -Name F -MemberDefinition '"
                  "[DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();"
                  "[DllImport(\"user32.dll\")] public static extern uint "
                  "GetWindowThreadProcessId(IntPtr h, out uint pid);';"
                  "$h=[W.F]::GetForegroundWindow();$p=0;[void][W.F]::GetWindowThreadProcessId($h,[ref]$p);"
                  "$g=Get-Process -Id $p -ErrorAction SilentlyContinue;"
                  "if($g){$g.ProcessName+'|'+$g.MainWindowTitle}else{'|'}")
        line = ps(script, timeout=10)
        app, _, title = line.partition("|")
        return app.strip(), title.strip()[:160]
    if first_tool("xdotool"):
        title = out(["xdotool", "getactivewindow", "getwindowname"], timeout=3)
        pid = out(["xdotool", "getactivewindow", "getwindowpid"], timeout=3)
        app = out(["ps", "-p", pid, "-o", "comm="], timeout=3) if pid.strip().isdigit() else ""
        return Path(app).name if app else "", title
    return "", ""


# ------------------------------------------------------------------ browser active-tab
_MAC_URL_OK = True
_MAC_URL_TIMEOUTS = 0   # a slow (unanswered) permission prompt isn't a denial — but cap it
_MAC_BROWSER_SCRIPTS = {
    "safari": 'tell application "Safari" to return (URL of front document) & "|" & (name of front document)',
    "google chrome": 'tell application "Google Chrome" to return (URL of active tab of front window) & "|" & (title of active tab of front window)',
    "chromium": 'tell application "Chromium" to return (URL of active tab of front window) & "|" & (title of active tab of front window)',
    "microsoft edge": 'tell application "Microsoft Edge" to return (URL of active tab of front window) & "|" & (title of active tab of front window)',
    "brave browser": 'tell application "Brave Browser" to return (URL of active tab of front window) & "|" & (title of active tab of front window)',
    "vivaldi": 'tell application "Vivaldi" to return (URL of active tab of front window) & "|" & (title of active tab of front window)',
}
_MAC_BROWSER_ALIASES = {"chrome": "google chrome", "edge": "microsoft edge",
                        "brave": "brave browser"}
_MAC_BROWSER_WORDS = set(_MAC_BROWSER_SCRIPTS) | {"firefox", "arc", "opera"} | set(_MAC_BROWSER_ALIASES)


def is_browser(app: str) -> bool:
    return bool(app) and app.strip().lower() in _MAC_BROWSER_WORDS


def active_tab(app: str) -> tuple[str, str]:
    """(url, title) of the frontmost browser tab — macOS, via AppleScript.
    Uses the Automation TCC (separate from Accessibility), so it works even when
    the System Events title probe returns empty. One short-fused probe per call;
    a real 'not authorised' answer or repeated hangs disable it for the session
    so we never fall into an osascript trap."""
    global _MAC_URL_OK, _MAC_URL_TIMEOUTS
    if not (IS_MAC and _MAC_URL_OK) or not app:
        return "", ""
    key = app.strip().lower()
    key = _MAC_BROWSER_ALIASES.get(key, key)
    script = _MAC_BROWSER_SCRIPTS.get(key)
    if not script:                     # firefox/arc/opera: no clean URL dictionary
        return "", ""
    r = code(["osascript", "-e", script], timeout=8)
    err = (r.stderr or "").lower()
    if r.returncode != 0:
        if "not authorized" in err or "-1743" in err or "may not be allowed" in err:
            _MAC_URL_OK = False        # user denied Automation — stop asking, silently
        elif r.returncode == 124:
            _MAC_URL_TIMEOUTS += 1
            if _MAC_URL_TIMEOUTS > 3:
                _MAC_URL_OK = False
        return "", ""                  # no window/doc etc is transient; retry next sample
    raw = (r.stdout or "").strip()
    if not raw:
        return "", ""
    url, _, title = raw.partition("|")
    return url.strip()[:300], title.strip()[:160]


def grab_screen_image() -> str | None:
    """Silent full-screen capture into a temp PNG; returns the path or None.
    For machine vision — unlike screenshot(), nothing is kept on the Desktop."""
    path = str(Path(tempfile.gettempdir()) / f"jarvis_vision_{int(time.time())}.png")
    if IS_MAC:
        ok = code(["screencapture", "-x", path], timeout=10).returncode == 0
    elif IS_WIN:
        script = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                  "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
                  "$bmp=New-Object Drawing.Bitmap $b.Width,$b.Height;"
                  "$g=[Drawing.Graphics]::FromImage($bmp);"
                  "$g.CopyFromScreen($b.Location,[Drawing.Point]::Empty,$b.Size);"
                  f"$bmp.Save('{path}'); 'ok'")
        ok = "ok" in ps(script, timeout=15)
    else:
        ok = any(code(c, timeout=10).returncode == 0 for c in
                 (["gnome-screenshot", "-f", path], ["spectacle", "-b", "-n", "-o", path],
                  ["scrot", path], ["import", "-window", "root", path]))
    return path if ok and Path(path).exists() else None


def set_brightness(pct: int) -> str:
    if IS_MAC:
        ok = code(["osascript", "-e",
                    'tell application "System Events" to set brightness of '
                    f'display 1 to {pct / 100}']).returncode == 0
        return (f"Brightness {pct} percent." if ok
                else "Brightness needs Accessibility permission on this Mac, sir.")
    if IS_WIN:
        r = ps(f"(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)"
               f".WmiSetBrightness(1,{pct}); 'ok'")
        return f"Brightness {pct} percent." if "ok" in r and "Exception" not in r \
            else "No adjustable brightness here, sir."
    bl = sorted((Path("/sys/class/backlight")).glob("*")) if Path("/sys/class/backlight").exists() else []
    if bl:
        mx = bl[0] / "max_brightness"
        try:
            cur = int((bl[0] / "brightness").read_text())
            top = int(mx.read_text())
            (bl[0] / "brightness").write_text(str(int(top * pct / 100)))
            return f"Brightness {pct} percent."
        except (OSError, ValueError):
            pass
    return "Adjusting brightness on this Linux box needs root or xrandr, sir."


# ------------------------------------------------------------------ vitals
def loadavg() -> tuple[float, float, float]:
    """1/5/15-min load; on Windows these are instantaneous CPU %% samples."""
    if IS_WIN:
        r = ps("[Math]::Round((Get-CimInstance Win32_Processor | "
               "Measure-Object -Property LoadPercentage -Average).Average,1)")
        m = re.search(r"[\d.]+", r)
        v = float(m.group()) if m else 0.0
        return v, v, v
    try:
        return os.getloadavg()
    except OSError:
        return 0.0, 0.0, 0.0


def cpu_pct() -> tuple[float, list[float]]:
    """(busy percent, load triple) in a form any OS can answer."""
    l1, l5, l15 = loadavg()
    if IS_WIN:
        return l1, [round(l1, 1), round(l5, 1), round(l15, 1)]
    cores = os.cpu_count() or 1
    return round(min(100.0, l1 / cores * 100), 1), [round(l1, 2), round(l5, 2), round(l15, 2)]


def hostname() -> str:
    if IS_WIN:
        return out(["cmd", "/c", "hostname"]) or "PC"
    return os.uname().nodename


def local_ip() -> str:
    if IS_MAC:
        return out(["ipconfig", "getifaddr", "en0"]) or "—"
    if IS_WIN:
        r = ps("(Get-NetIPAddress -AddressFamily IPv4 | "
               "Where-Object { $_.PrefixOrigin -ne 'Loopback' } | "
               "Select-Object -First 1).IPAddress")
        return r.splitlines()[0] if r else "—"
    r = out(["hostname", "-I"])
    if r:
        return r.split()[0]
    r = out(["sh", "-c", "ip route get 1.1.1.1 2>/dev/null | grep -oE 'src [0-9.]+' | cut -d' ' -f2"])
    return r or "—"


def uptime_secs() -> int:
    if IS_MAC:
        m = re.search(r"sec = (\d+)", out(["sysctl", "-n", "kern.boottime"]))
        return max(0, int(time.time()) - int(m.group(1))) if m else 0
    if IS_WIN:
        r = ps("[int]((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalSeconds")
        return int(r) if r.strip().isdigit() else 0
    try:
        return int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError):
        return 0


def battery() -> dict:
    if IS_MAC:
        raw = out(["pmset", "-g", "batt"])
        m = re.search(r"(\d+)%", raw)
        if not m:
            return {"pct": None, "state": "n/a"}
        low = raw.lower()
        return {"pct": int(m.group(1)),
                "state": ("charging" if "charging" in low
                          else "charged" if "charged" in low else "discharging")}
    if IS_WIN:
        r = ps("$b=Get-CimInstance Win32_Battery; if($b){ '{0}|{1}' -f $b.EstimatedBatteryRemaining, "
               "$(if($b.BatteryStatus -eq 2){'charging'}else{'discharging'}) }")
        if "|" in r:
            p, st = r.split("|", 1)
            return {"pct": int(p) if p.strip().isdigit() else None, "state": st.strip()}
        return {"pct": None, "state": "n/a"}
    base = Path("/sys/class/power_supply")
    for d in base.glob("BAT*") if base.exists() else []:
        try:
            pct = int((d / "capacity").read_text().strip())
            st = (d / "status").read_text().strip().lower() if (d / "status").exists() else "discharging"
            return {"pct": pct, "state": st if st in ("charging", "full") else "discharging"}
        except (OSError, ValueError):
            pass
    return {"pct": None, "state": "n/a"}


def disk(path: str = "/") -> dict:
    drive = path if IS_WIN else "/"
    try:
        u = shutil.disk_usage(drive)
        return {"total": f"{u.total / 1024**3:.0f}G", "used": f"{u.used / 1024**3:.0f}G",
                "free": f"{u.free / 1024**3:.0f}G", "pct": f"{u.used * 100 // u.total}%",
                "total_gb": round(u.total / 1024**3, 1), "free_gb": round(u.free / 1024**3, 1)}
    except OSError:
        return {}


def mem() -> dict:
    """pct used + total_gb + free_mb, from whatever the OS exposes."""
    if IS_MAC or IS_LINUX:
        if IS_LINUX and Path("/proc/meminfo").exists():
            kv = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                m = re.match(r"(\w+):\s+(\d+)", line)
                if m:
                    kv[m.group(1)] = int(m.group(2))
            total = kv.get("MemTotal", 0)
            avail = kv.get("MemAvailable", 0)
            used_pct = (total - avail) * 100 / total if total else 0
            return {"pct": round(used_pct, 1), "total_gb": round(total / 1024**2, 1),
                    "free_mb": avail // 1024}
        vs = out(["vm_stat"])
        pg = re.search(r"page size of (\d+)", vs)
        psize = int(pg.group(1)) if pg else 16384
        pages = lambda lab: (int(re.search(lab + r":\s+(\d+)", vs).group(1))
                             if re.search(lab + r":\s+(\d+)", vs) else 0)
        total = int(out(["sysctl", "-n", "hw.memsize"]) or 0)
        used = (pages("Pages active") + pages("Pages wired down")
                + pages("Pages occupied by compressor")) * psize
        free_mb = (pages("Pages free") + pages("Pages speculative")) * psize // 1048576
        return {"pct": round(max(0.0, min(100.0, used / total * 100)) if total else 0, 1),
                "total_gb": round(total / 1024**3, 1), "free_mb": free_mb}
    r = ps("$os=Get-CimInstance Win32_OperatingSystem; "
           "'{0}|{1}' -f $os.TotalVisibleMemorySize, $os.FreePhysicalMemory")
    if "|" in r:
        tot_kb, free_kb = (x.strip() for x in r.split("|", 1))
        tot, free = int(tot_kb) * 1024, int(free_kb) * 1024
        return {"pct": round((tot - free) * 100 / tot, 1),
                "total_gb": round(tot / 1024**3, 1), "free_mb": free // 1048576}
    return {"pct": 0, "total_gb": 0, "free_mb": 0}


def top_mem_processes(n: int = 5) -> list[tuple[str, int]]:
    if IS_WIN:
        r = ps("$t=(Get-CimInstance Win32_OperatingSystem).TotalVisibleMemorySize*1kb; "
               f"Get-Process | Sort WS -Desc | Select -First {n} | "
               "ForEach { '{0}:{1:N0}' -f $_.ProcessName, ($_.WS*100/$t) }")
        return [(x.split(":")[0], int(float(x.split(":")[1]))) for x in r.splitlines() if ":" in x]
    cmd = ["ps", "aux", "--sort=-%mem"] if IS_LINUX else ["ps", "aux", "-m"]
    rows = out(cmd).splitlines()[1:1 + n]
    hogs = []
    for line in rows:
        c = line.split()
        if len(c) > 10:
            hogs.append((Path(c[10]).name, round(float(c[3]))))
    return hogs


# ------------------------------------------------------------------ device identity
def device_profile() -> dict:
    """Full machine fingerprint for the current OS — collected once, remembered forever."""
    p: dict = {}
    if IS_MAC:
        p = {"name": out(["scutil", "--get", "ComputerName"]),
             "os": out(["sw_vers", "-productName"]),
             "os_version": out(["sw_vers", "-productVersion"]),
             "build": out(["sw_vers", "-buildVersion"]),
             "model": out(["sysctl", "-n", "hw.model"]),
             "chip": out(["sysctl", "-n", "machdep.cpu.brand_string"])}
        try:
            ior = out(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
            m = re.search(r'"IOPlatformSerialNumber"\s*=\s*"([^"]+)"', ior)
            p["serial"] = m.group(1) if m else "—"
        except OSError:
            p["serial"] = "—"
    elif IS_WIN:
        r = ps("$cs=Get-CimInstance Win32_ComputerSystem; $os=Get-CimInstance Win32_OperatingSystem; "
               "$b=Get-CimInstance Win32_Bios; $cpu=Get-CimInstance Win32_Processor | Select -First 1; "
               "'{0}|{1}|{2}|{3}|{4}|{5}|{6}|{7}' -f $cs.Name,$os.Caption,$os.Version,$os.BuildNumber, "
               "$cs.Manufacturer,$cs.Model,$cpu.Name,$b.SerialNumber")
        f = (r.split("|") + [""] * 8)[:8] if r else [""] * 8
        p = {"name": f[0].strip(), "os": f[1].strip(), "os_version": f[2].strip(),
             "build": f[3].strip(), "model": f"{f[4].strip()} {f[5].strip()}".strip(),
             "chip": f[6].strip(), "serial": f[7].strip() or "—"}
    else:
        rel = {}
        for part in Path("/etc/os-release").read_text().splitlines() if Path("/etc/os-release").exists() else []:
            m = re.match(r'(\w+)=["]?([^"]*)["]?', part)
            if m:
                rel[m.group(1)] = m.group(2)
        chip = ""
        if Path("/proc/cpuinfo").exists():
            m = re.search(r"model name\s*:\s*(.+)", Path("/proc/cpuinfo").read_text())
            chip = m.group(1).strip() if m else ""
        def dmi(k):
            try:
                return Path(f"/sys/class/dmi/id/{k}").read_text().strip()
            except OSError:
                return ""
        p = {"name": hostname(), "os": rel.get("NAME", "Linux"),
             "os_version": rel.get("VERSION_ID", "") or out(["uname", "-r"]),
             "build": out(["uname", "-r"]),
             "model": dmi("product_name") or dmi("sys_vendor"),
             "chip": chip, "serial": dmi("product_serial") or "—"}
    p["cores"] = os.cpu_count() or 0
    p["memory_gb"] = mem().get("total_gb", 0)
    return p
