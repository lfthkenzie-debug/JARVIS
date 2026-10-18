#!/bin/bash
# JARVIS — share the installer with remote testers, straight from THIS laptop.
#
#   ./share.sh          build a clean bundle, open a public tunnel, print tester links
#   ./share.sh stop     tear the tunnel + file server down
#   ./share.sh status   show the current public URL and whether it's alive
#
# SECURITY: this serves ONLY static files (landing page, installers, the code
# bundle) over a tunnel. It never exposes the live control API (/api/*), so a
# remote tester can only DOWNLOAD — JARVIS then runs on their own machine and
# controls their own OS. Nothing on this laptop can be driven by them.
#
# AGENTIC: wrapped in `caffeinate` so the Mac stays awake and the link keeps
# serving until you run `./share.sh stop` (keep the lid open / on power).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
RUN="$ROOT/.share"
SHARE="$ROOT/share"
PID_HTTP="$RUN/http.pid"
PID_TUN="$RUN/tunnel.pid"
URL_FILE="$RUN/url"
LOG="$RUN/serve.log"
PORT="${JARVIS_SHARE_PORT:-8795}"
mkdir -p "$RUN"

_alive() { [[ -f "$1" ]] && kill -0 "$(cat "$1")" 2>/dev/null; }

stop_down() {
  for pf in "$PID_TUN" "$PID_HTTP"; do
    if [[ -f "$pf" ]] && kill -0 "$(cat "$pf")" 2>/dev/null; then
      kill "$(cat "$pf")" 2>/dev/null || true
    fi
    rm -f "$pf"
  done
  pkill -f "http.server $PORT" 2>/dev/null || true
  pkill -f "cloudflared tunnel --url http://127.0.0.1:$PORT" 2>/dev/null || true
  rm -f "$URL_FILE"
}

build_bundle() {
  echo "▸ Building a clean installer bundle (excludes venv, user data, caches, secrets)…"
  command -v rsync >/dev/null 2>&1 || { echo "✗ rsync not found — cannot stage the bundle."; exit 1; }
  mkdir -p "$SHARE"
  local STAGE; STAGE="$(mktemp -d)"
  mkdir -p "$STAGE/JARVIS"
  rsync -a \
    --exclude '.venv' --exclude 'venv' --exclude 'env' --exclude '.direnv' \
    --exclude 'jarvis_data' --exclude '.git' --exclude 'share' --exclude '.share' \
    --exclude '__pycache__' --exclude '*.pyc' --exclude '*.pyo' \
    --exclude '.DS_Store' --exclude '.qoder' --exclude '*.sqlite3' --exclude '*.log' \
    --exclude '.env' --exclude '*.key' --exclude '*.pem' --exclude 'node_modules' \
    "$ROOT/" "$STAGE/JARVIS/"
  rm -f "$SHARE/JARVIS.zip"
  ( cd "$STAGE" && zip -qr "$SHARE/JARVIS.zip" JARVIS )
  rm -rf "$STAGE"
  cp "$ROOT/install.sh" "$SHARE/install.sh"
  [[ -f "$ROOT/install.ps1" ]] && cp "$ROOT/install.ps1" "$SHARE/install.ps1"
  local size; size="$(du -h "$SHARE/JARVIS.zip" | cut -f1)"
  echo "▸ Bundle ready: share/JARVIS.zip ($size)"
}

write_index() {
  local url="$1"
  cat > "$SHARE/index.html" <<HTML
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JARVIS — Installer (live from host)</title>
<style>
:root{--cy:#4de8ff;--bg:#02060d;--fg:#cfefff;--mut:#6fa8c0}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1200px 600px at 50% -10%,#062334,var(--bg));
color:var(--fg);font-family:'Segoe UI',system-ui,sans-serif;padding:40px 20px}
.wrap{max-width:760px;margin:0 auto}h1{color:var(--cy);letter-spacing:2px;margin:0 0 4px;font-weight:700}
.sub{color:var(--mut);margin:0 0 24px}.note{background:#08283a;border:1px solid #0f4a66;border-radius:12px;
padding:14px 16px;margin:18px 0;color:#a9d8ec;font-size:14px;line-height:1.5}
h2{color:var(--cy);font-size:15px;letter-spacing:1px;text-transform:uppercase;margin:26px 0 8px}
pre{background:#010a12;border:1px solid #0f4a66;border-radius:10px;padding:12px 14px;overflow-x:auto;
color:#bff0ff;font-size:13px;white-space:pre;margin:0}
a.btn{display:inline-block;margin-top:10px;color:#02060d;background:var(--cy);border-radius:8px;
padding:8px 14px;font-weight:700;text-decoration:none}
footer{color:var(--mut);font-size:12px;margin-top:28px;text-align:center}
</style></head><body><div class="wrap">
<h1>J.A.R.V.I.S</h1><p class="sub">Installer mirror — served live from the host laptop.</p>
<div class="note">Your machine stays yours: download the installer, then JARVIS runs <b>locally</b> and controls <b>your</b> OS. This page only hands out files.</div>

<h2>macOS / Linux — one line</h2>
<pre>curl -fsSL $url/install.sh | JARVIS_ZIP="$url/JARVIS.zip" bash</pre>

<h2>Windows (PowerShell)</h2>
<pre>powershell -c "\$env:JARVIS_ZIP='$url/JARVIS.zip'; irm $url/install.ps1 | iex"</pre>

<a class="btn" href="$url/JARVIS.zip">Download JARVIS.zip</a>
<footer>This is a temporary mirror. For the permanent public link, use the GitHub Pages site.</footer>
</div></body></html>
HTML
}

do_start() {
  # Keep the Mac awake for the whole session (agentic: never sleeps until stopped).
  if [[ "$(uname)" == Darwin && "${JARVIS_SHARE_CAFFEINATED:-}" != 1 ]] && command -v caffeinate >/dev/null; then
    export JARVIS_SHARE_CAFFEINATED=1
    exec caffeinate -is "$0" start
  fi
  if _alive "$PID_TUN" && [[ -f "$URL_FILE" ]]; then
    echo "▸ Already sharing: $(cat "$URL_FILE")   (run './share.sh stop' first to refresh)"
    return
  fi
  command -v cloudflared >/dev/null 2>&1 || { echo "✗ cloudflared not found. brew install cloudflared"; exit 1; }
  stop_down
  : > "$LOG"
  build_bundle
  echo "▸ Serving files on 127.0.0.1:$PORT (local only — control API is NOT exposed)…"
  python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$SHARE" >>"$LOG" 2>&1 &
  echo $! > "$PID_HTTP"
  sleep 1
  echo "▸ Opening Cloudflare quick tunnel (no account needed)…"
  cloudflared tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate >>"$LOG" 2>&1 &
  echo $! > "$PID_TUN"
  local url=""
  for _ in $(seq 1 30); do
    url="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1)"
    [[ -n "$url" ]] && break
    sleep 1
  done
  if [[ -z "$url" ]]; then
    echo "✗ Could not obtain a tunnel URL. Last log lines:"; tail -20 "$LOG"; stop_down; exit 1
  fi
  echo "$url" > "$URL_FILE"
  write_index "$url"
  trap 'echo; echo "▸ Stopping…"; stop_down; exit 0' INT TERM
  echo
  echo "╔══════════════════════════════════════════════════════════════╗"
  echo "║  JARVIS installer is LIVE for remote testers                 ║"
  echo "╚══════════════════════════════════════════════════════════════╝"
  echo "   Landing page :  $url"
  echo "   macOS/Linux  :  curl -fsSL $url/install.sh | JARVIS_ZIP=\"$url/JARVIS.zip\" bash"
  echo "   Windows      :  powershell -c \"\$env:JARVIS_ZIP='$url/JARVIS.zip'; irm $url/install.ps1 | iex\""
  echo "   Bundle       :  $url/JARVIS.zip"
  echo
  echo "   Log: $LOG   ·   Stop with: ./share.sh stop   (or Ctrl-C)"
  echo "   Keeping the Mac awake via caffeinate — keep it on power, lid open."
  echo
  wait "$(cat "$PID_TUN")"
}

do_stop() { stop_down; echo "▸ Tunnel + file server stopped. Testers can no longer reach the laptop."; }

do_status() {
  if _alive "$PID_TUN" && [[ -f "$URL_FILE" ]]; then
    echo "▸ LIVE → $(cat "$URL_FILE")"
  else
    echo "▸ Not sharing."
  fi
}

case "${1:-start}" in
  start)  do_start ;;
  stop)   do_stop ;;
  status) do_status ;;
  *) echo "usage: ./share.sh [start|stop|status]"; exit 1 ;;
esac
