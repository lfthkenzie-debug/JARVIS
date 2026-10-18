/* JARVIS holographic interface — front-end controller */
"use strict";

const $ = (id) => document.getElementById(id);
const API = (path, opts) => fetch(path, opts).then((r) => r.json());
const POST = (path, body) =>
  API(path, { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body) });

const STATE = { current: "boot", audio: null, typing: false };
const ROOT_LABELS = { downloads: "DOWNLOADS", documents: "DOCUMENTS", desktop: "DESKTOP",
                      pictures: "PICTURES", movies: "MOVIES", music: "MUSIC",
                      projects: "PROJECTS", home: "HOME", disk: "MACINTOSH HD" };
const localIso = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
                         return d.toISOString().slice(0, 19); };
const PRIVACY = {
  on: localStorage.getItem("jarvis_privacy") === "1" ||
      new URLSearchParams(location.search).get("privacy") === "1" };

function setState(s) {
  STATE.current = s;
  document.body.className = "state-" + s;
  $("status-text").textContent =
    { boot: "BOOTING", idle: "STANDBY", listening: "LISTENING",
      thinking: "PROCESSING", speaking: "VOCALIZING" }[s] || s.toUpperCase();
}

/* ---------------------------------------------------------------- clock */
function tickClock() {
  const d = new Date();
  $("clock-time").textContent = d.toLocaleTimeString("en-GB");
  $("clock-date").textContent = d.toLocaleDateString("en-GB",
    { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
}
setInterval(tickClock, 1000); tickClock();

/* ---------------------------------------------------------------- chat */
function addMsg(kind, text, skill, dim) {
  const div = document.createElement("div");
  div.className = "msg " + kind + (dim ? " dim" : "");
  if (kind === "jarvis" && skill) {
    const tag = document.createElement("span");
    tag.className = "skill-tag"; tag.textContent = skill;
    div.appendChild(tag);
  }
  const body = document.createElement("span");
  body.textContent = text;
  div.appendChild(body);
  $("chat").appendChild(div);
  $("chat").scrollTop = $("chat").scrollHeight;
  return div;
}

function typewriter(div, text, speed = 12) {
  STATE.typing = true;
  const target = div.querySelector("span:last-child");
  target.textContent = "";
  div.classList.add("caret");
  let i = 0;
  (function step() {
    if (i >= text.length) { div.classList.remove("caret"); STATE.typing = false; return; }
    const chunk = text.slice(i, Math.min(text.length, i + 2 + Math.random() * 3));
    i += chunk.length;
    target.textContent += chunk;
    $("chat").scrollTop = $("chat").scrollHeight;
    setTimeout(step, speed);
  })();
}

function speakFallback(text) {
  if (!("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text);
  const v = speechSynthesis.getVoices().find(
    (x) => /en[-_]GB/i.test(x.lang) && /Daniel|Arthur|George/i.test(x.name))
    || speechSynthesis.getVoices().find((x) => /en[-_]GB/i.test(x.lang));
  if (v) u.voice = v;
  u.rate = 1.08; u.pitch = 0.92;
  speechSynthesis.cancel(); speechSynthesis.speak(u);
}

async function send(text) {
  text = (text || "").trim();
  if (!text || STATE.current === "thinking") return;
  addMsg("user", text);
  setState("thinking");
  try {
    const res = await POST("/api/command", { text, voice: $("voice-cb").checked });
    setState("speaking");
    const div = addMsg("jarvis", "", res.skill, false);
    typewriter(div, res.reply || "…");
    if (res.audio) {
      const audio = new Audio("data:audio/mpeg;base64," + res.audio);
      STATE.audio = audio;
      audio.onended = () => { if (STATE.audio === audio) STATE.audio = null; setState("idle"); };
      audio.onerror = () => { speakFallback(res.reply); };
      audio.play().catch(() => speakFallback(res.reply));
    } else if ($("voice-cb").checked) {
      speakFallback(res.reply);
    }
    if (!STATE.audio) setTimeout(() => { if (STATE.current === "speaking") setState("idle"); }, 1400);
    pollSystem();                       // commands may move disks/memory
  } catch (err) {
    addMsg("jarvis", "Communication link failed: " + err.message, "system");
    setState("idle");
  }
}

$("cmd-form").addEventListener("submit", (e) => {
  e.preventDefault();
  send($("cmd-input").value);
  $("cmd-input").value = "";
});

/* clear the screen — JARVIS's memory vault stays untouched */
$("chat-clear").addEventListener("click", () => {
  $("chat").innerHTML = "";
  localStorage.setItem("jarvis_chat_cleared", localIso());
  addMsg("jarvis",
         "Screen cleared, sir. My memory vault still holds every word — ask me what you said earlier any time.",
         "memory", true);
});

/* ---------------------------------------------------------------- mic (Web Speech) */
let recog = null;
$("mic-btn").addEventListener("click", () => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { addMsg("jarvis", "Voice input needs Chrome or Safari, sir. Keyboard remains fully operational.", "system"); return; }
  if (recog) { recog.stop(); return; }
  recog = new SR();
  recog.lang = "en-GB"; recog.interimResults = true; recog.continuous = false;
  recog.onstart = () => { $("mic-btn").classList.add("rec"); setState("listening"); };
  recog.onresult = (e) => {
    const t = [...e.results].map((r) => r[0].transcript).join("");
    $("cmd-input").value = t;
    if (e.results[e.results.length - 1].isFinal) recog.stop();
  };
  recog.onerror = recog.onend = () => {
    $("mic-btn").classList.remove("rec"); recog = null;
    const v = $("cmd-input").value.trim();
    if (v) { $("cmd-input").value = ""; send(v); } else setState("idle");
  };
  recog.start();
});

/* ---------------------------------------------------------------- system vitals */
function setBar(id, valId, pct, txt, invert) {
  const el = $(id); el.style.width = Math.min(100, Math.max(0, pct)) + "%";
  $(valId).textContent = txt;
  el.className = "";
  if (invert === "bat") {
    if (pct <= 20) el.className = "low"; else if (pct > 0) el.className = "ok";
  } else if (pct > 85) el.className = "hot";
}

async function pollSystem() {
  try {
    const s = await API("/api/system" + (PRIVACY.on ? "?privacy=1" : ""));
    if (s.privacy) {
      for (const [b, v] of [["cpu-bar", "cpu-val"], ["mem-bar", "mem-val"],
                             ["disk-bar", "disk-val"], ["bat-bar", "bat-val"]]) {
        $(b).style.width = "0%"; $(b).className = ""; $(v).textContent = "•••";
      }
      for (const id of ["sys-uptime", "sys-load", "sys-host", "sys-ip",
                        "dev-os", "dev-model", "dev-chip"]) $(id).textContent = "•••";
    } else {
      setBar("cpu-bar", "cpu-val", s.cpu.pct, s.cpu.pct.toFixed(0) + "%");
      setBar("mem-bar", "mem-val", s.mem.pct, s.mem.pct.toFixed(0) + "% · " + s.mem.total_gb + " GB");
      const dpct = parseInt(s.disk.pct) || 0;
      setBar("disk-bar", "disk-val", dpct, s.disk.free + " free");
      if (s.battery.pct != null)
        setBar("bat-bar", "bat-val", s.battery.pct, s.battery.pct + "% · " + s.battery.state, "bat");
      else setBar("bat-bar", "bat-val", 100, "AC POWER", "bat");
      $("sys-uptime").textContent = s.uptime;
      $("sys-load").textContent = s.cpu.load.join(" / ") + " (" + s.cores + " cores)";
      $("sys-host").textContent = s.host;
      $("sys-ip").textContent = s.ip;
      if (s.device) {
        $("dev-os").textContent = (s.device.os || "macOS") + " " + (s.device.os_version || "");
        $("dev-model").textContent = s.device.model || "—";
        $("dev-chip").textContent = (s.device.chip || "—") + " · " + (s.device.memory_gb || "?") + "GB";
      }
    }
    $("sys-skills").textContent = $("skill-list").children.length || "—";
    $("sys-inter").textContent = s.jarvis.interactions;
    buildRoots(s.roots);
  } catch (e) { /* server restarting */ }
}
setInterval(pollSystem, 3000);

/* ---------------------------------------------------------------- privacy mode */
function applyPrivacy() {
  $("privacy-btn").classList.toggle("on", PRIVACY.on);
  $("file-list").innerHTML = "";
  FILE_PATH = "";
  $("file-crumbs").textContent = PRIVACY.on
    ? "hidden — privacy mode on" : "select a folder above";
  pollSystem();
}
$("privacy-btn").addEventListener("click", () => {
  PRIVACY.on = !PRIVACY.on;
  localStorage.setItem("jarvis_privacy", PRIVACY.on ? "1" : "0");
  applyPrivacy();
});

/* ---------------------------------------------------------------- files */
let FILE_ROOT = "downloads", FILE_PATH = "";
const built = { roots: false };

function buildRoots(roots) {
  if (built.roots) return; built.roots = true;
  const wrap = $("root-chips");
  roots.forEach((r) => {
    const b = document.createElement("button");
    b.className = "chip" + (r === FILE_ROOT ? " active" : "");
    b.textContent = ROOT_LABELS[r] || r.toUpperCase();
    b.onclick = () => { FILE_ROOT = r; FILE_PATH = "";
      wrap.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
      b.classList.add("active"); loadFiles(); };
    wrap.appendChild(b);
  });
}

function fmtSize(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
  while (n >= 1024 && i < 4) { n /= 1024; i++; }
  return n.toFixed(n >= 100 || i === 0 ? 0 : 1) + u[i];
}

async function loadFiles(q) {
  if (PRIVACY.on && !q) {
    $("file-list").innerHTML = "";
    $("file-crumbs").textContent = "hidden — privacy mode on";
    return;
  }
  const url = q ? "/api/files?q=" + encodeURIComponent(q)
                : `/api/files?root=${FILE_ROOT}&path=${encodeURIComponent(FILE_PATH)}`;
  const d = await API(url);
  const list = $("file-list"); list.innerHTML = "";
  $("file-crumbs").textContent = q ? `SEARCH: ${q}` : d.path || "";
  if (d.error) { $("file-crumbs").textContent = d.error; return; }
  if (!q && !d.at_home) {
    const up = document.createElement("li");
    up.className = "dir"; up.innerHTML = `<span class="fi">↩</span><span class="fn">..</span>`;
    up.onclick = () => { FILE_PATH = FILE_PATH.split("/").slice(0, -1).join("/"); loadFiles(); };
    list.appendChild(up);
  }
  d.entries.forEach((en) => {
    const li = document.createElement("li");
    li.className = en.dir ? "dir" : "";
    const rel = q ? en.path || en.name : (d.at_home ? en.name : FILE_PATH + "/" + en.name);
    li.innerHTML = `<span class="fi">${en.dir ? "▸" : "·"}</span>` +
      `<span class="fn"></span><span class="fs">${en.dir ? "" : fmtSize(en.size)}</span>`;
    li.querySelector(".fn").textContent = en.name;
    li.onclick = () => {
      if (en.dir && !q) { FILE_PATH = rel; loadFiles(); }
      else POST("/api/files/open", { root: q ? "home" : FILE_ROOT, path: en.path || rel });
    };
    list.appendChild(li);
  });
}

$("file-search").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("file-q").value.trim();
  if (q) loadFiles(q); else loadFiles();
});

/* ---------------------------------------------------------------- tasks */
async function loadTasks() {
  const d = await API("/api/tasks");
  const list = $("task-list"); list.innerHTML = "";
  (d.tasks || []).forEach((t) => {
    const li = document.createElement("li");
    if (t.done) li.className = "done";
    li.innerHTML = `<span class="tk"></span><span class="tt"></span><span class="tx">✕</span>`;
    li.querySelector(".tt").textContent = t.title;
    li.querySelector(".tk").onclick = () => POST("/api/tasks", { action: "toggle", id: t.id }).then(loadTasks);
    li.querySelector(".tt").onclick = li.querySelector(".tk").onclick;
    li.querySelector(".tx").onclick = () => POST("/api/tasks", { action: "delete", id: t.id }).then(loadTasks);
    list.appendChild(li);
  });
}
$("task-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const title = $("task-input").value.trim();
  if (!title) return;
  $("task-input").value = "";
  POST("/api/tasks", { action: "add", title }).then(loadTasks);
});
$("tasks-clear").onclick = () => POST("/api/tasks", { action: "clear_done" }).then(loadTasks);

/* ---------------------------------------------------------------- quick actions */
const ACTIONS = [
  ["WEATHER", "what's the weather"], ["SYSTEM STATUS", "system status"],
  ["SCREENSHOT", "take a screenshot"], ["SUMMARIZE DOWNLOADS", "summarize my downloads"],
  ["TIME CHECK", "what time is it"], ["ML STACK", "check the ml stack"],
  ["STAY AWAKE 1H", "keep the mac awake for an hour"], ["OPEN SAFARI", "open safari"],
  ["WIKI: TESLA", "who is nikola tesla"], ["VOLUME 30", "set volume to thirty"],
];
ACTIONS.forEach(([label, phrase]) => {
  const b = document.createElement("button");
  b.className = "act"; b.textContent = label;
  b.onclick = () => send(phrase);
  $("action-grid").appendChild(b);
});

/* ---------------------------------------------------------------- skills */
async function loadSkills() {
  const d = await API("/api/skills");
  const list = $("skill-list");
  d.skills.forEach((s) => {
    const li = document.createElement("li");
    li.innerHTML = `<b>${s.name}</b><span></span>`;
    li.querySelector("span").textContent = s.help;
    if (s.examples && s.examples[0]) li.title = "try: " + s.examples[0];
    li.onclick = () => { $("cmd-input").value = (s.examples && s.examples[0]) || ""; $("cmd-input").focus(); };
    list.appendChild(li);
  });
}

/* ---------------------------------------------------------------- canvases */
function initParticles() {
  const cv = $("particles"), cx = cv.getContext("2d");
  let W, H, pts = [];
  const reset = () => {
    W = cv.width = innerWidth; H = cv.height = innerHeight;
    pts = Array.from({ length: 64 }, () => ({
      x: Math.random() * W, y: Math.random() * H,
      vx: (Math.random() - .5) * .25, vy: (Math.random() - .5) * .25,
      r: Math.random() * 1.6 + .4 }));
  };
  reset(); addEventListener("resize", reset);
  (function draw() {
    cx.clearRect(0, 0, W, H);
    cx.fillStyle = "rgba(120,220,255,.5)";
    pts.forEach((p) => {
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0 || p.x > W) p.vx *= -1;
      if (p.y < 0 || p.y > H) p.vy *= -1;
      cx.beginPath(); cx.arc(p.x, p.y, p.r, 0, 7); cx.fill();
    });
    for (let i = 0; i < pts.length; i++)
      for (let j = i + 1; j < pts.length; j++) {
        const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y, d = dx * dx + dy * dy;
        if (d < 12000) {
          cx.strokeStyle = `rgba(77,232,255,${(1 - d / 12000) * .16})`;
          cx.lineWidth = .6;
          cx.beginPath(); cx.moveTo(pts[i].x, pts[i].y); cx.lineTo(pts[j].x, pts[j].y); cx.stroke();
        }
      }
    requestAnimationFrame(draw);
  })();
}

/* energised arc-reactor core: rotating plasma + orbiting energy sparks */
function initCoreFX() {
  const cv = $("core-fx"); if (!cv) return;
  const cx = cv.getContext("2d");
  let W, H, cxp, cyp, R;
  const size = () => {
    const r = cv.getBoundingClientRect(), dpr = devicePixelRatio || 1;
    W = cv.width = Math.max(60, r.width) * dpr; H = cv.height = Math.max(60, r.height) * dpr;
    cxp = W / 2; cyp = H / 2; R = Math.min(W, H) / 2;
  };
  size(); addEventListener("resize", size);
  const sparks = Array.from({ length: 54 }, () => ({
    a: Math.random() * 6.28, r: 0.34 + Math.random() * 0.6,
    s: (0.008 + Math.random() * 0.05) * (Math.random() < 0.5 ? -1 : 1),
    sz: 0.5 + Math.random() * 1.9, w: Math.random() * 6.28 }));
  let t = 0;
  (function draw() {
    t += 1;
    const boost = STATE.current === "thinking" ? 2.0 : STATE.current === "speaking" ? 1.55
                : STATE.current === "listening" ? 1.3 : 1.0;
    cx.clearRect(0, 0, W, H);
    const dpr = devicePixelRatio || 1;
    // plasma arcs
    cx.save(); cx.translate(cxp, cyp); cx.lineCap = "round";
    for (let i = 0; i < 3; i++) {
      const rad = R * (0.58 + i * 0.13);
      cx.strokeStyle = `rgba(77,232,255,${(0.10 + 0.09 * i) * boost})`;
      cx.lineWidth = (1.1 + i * 0.6) * dpr;
      const start = t * 0.02 * (i % 2 ? -1 : 1) + i * 2;
      cx.beginPath(); cx.arc(0, 0, rad, start, start + 1.5 + 0.4 * boost); cx.stroke();
    }
    // corona ring
    cx.strokeStyle = `rgba(138,244,255,${0.05 * boost})`; cx.lineWidth = R * 0.14;
    cx.beginPath(); cx.arc(0, 0, R * 0.5, 0, 6.28); cx.stroke();
    cx.restore();
    // orbiting energy sparks
    cx.shadowColor = "rgba(77,232,255,.9)";
    sparks.forEach((k) => {
      k.a += k.s * boost; k.w += 0.05;
      const rr = R * k.r * (1 + 0.04 * Math.sin(t * 0.05 + k.a));
      const x = cxp + Math.cos(k.a) * rr, y = cyp + Math.sin(k.a) * rr;
      const al = (0.35 + 0.55 * Math.abs(Math.sin(k.w))) * Math.min(1.4, boost);
      cx.fillStyle = `rgba(150,240,255,${al})`;
      cx.shadowBlur = 6; cx.beginPath(); cx.arc(x, y, k.sz * dpr, 0, 6.28); cx.fill();
    });
    cx.shadowBlur = 0;
    requestAnimationFrame(draw);
  })();
}

function initWave() {
  const cv = $("wave"), cx = cv.getContext("2d");
  let W, H;
  const reset = () => { W = cv.width = cv.clientWidth * devicePixelRatio;
                        H = cv.height = cv.clientHeight * devicePixelRatio; };
  reset(); addEventListener("resize", reset);
  let t = 0;
  (function draw() {
    t += .05; cx.clearRect(0, 0, W, H);
    const n = 56, mid = H / 2;
    for (let i = 0; i < n; i++) {
      const x = (i + .5) * W / n;
      let amp;
      if (STATE.current === "speaking")
        amp = (Math.sin(t * 3 + i * .6) + Math.sin(t * 7 + i)) * .25 + (Math.random() * .5);
      else if (STATE.current === "thinking" || STATE.current === "listening")
        amp = Math.sin(t * 2 + i * .35) * .3 + .35;
      else amp = Math.sin(t + i * .25) * .16 + .2;
      const h = Math.abs(amp) * mid * 1.6 + 2;
      const a = STATE.current === "speaking" ? .9 : .45;
      cx.strokeStyle = `rgba(77,232,255,${a})`;
      cx.lineWidth = 2 * devicePixelRatio;
      cx.shadowColor = "rgba(77,232,255,.8)"; cx.shadowBlur = 6;
      cx.beginPath(); cx.moveTo(x, mid - h); cx.lineTo(x, mid + h); cx.stroke();
    }
    requestAnimationFrame(draw);
  })();
}

/* ---------------------------------------------------------------- weather uplink */
function wxIcon(code, desc) {
  const d = (desc || "").toLowerCase(), C = {
    "113": "☀️", "116": "⛅", "119": "☁️", "122": "☁️", "143": "🌫️",
    "176": "🌦️", "200": "⛈️", "266": "🌧️", "302": "🌧️", "308": "🌧️",
    "353": "🌦️", "386": "⛈️", "389": "⛈️", "395": "❄️" };
  if (C[code]) return C[code];
  if (/thunder|storm/.test(d)) return "⛈️";
  if (/snow|sleet|blizzard|hail|ice/.test(d)) return "❄️";
  if (/rain|drizzle|shower/.test(d)) return "🌧️";
  if (/fog|mist|haze|smoke/.test(d)) return "🌫️";
  if (/cloud|overcast/.test(d)) return "☁️";
  if (/sun|clear|fair/.test(d)) return "☀️";
  return "🌡️";
}

function renderWeather(d) {
  const grid = $("wx-grid"); if (!grid) return;
  grid.innerHTML = "";
  (d.places || []).forEach((p) => {
    const card = document.createElement("div");
    card.className = "wx-card" + (p.ok ? "" : " bad");
    card.innerHTML = `<div class="wx-city"></div><div class="wx-sub"></div>` +
      `<div class="wx-main"><span class="wx-icon"></span>` +
      `<div><div class="wx-temp"></div><div class="wx-desc"></div></div></div>` +
      `<div class="wx-stats"></div>`;
    card.querySelector(".wx-city").textContent = p.city;
    card.querySelector(".wx-sub").textContent = [p.region, p.country].filter(Boolean).join(", ");
    card.querySelector(".wx-icon").textContent = p.ok ? wxIcon(p.code, p.desc) : "📡";
    card.querySelector(".wx-temp").innerHTML = p.ok ? `${p.temp}<small>°C</small>` : "--";
    card.querySelector(".wx-desc").textContent = p.ok ? p.desc : "signal lost";
    const stats = card.querySelector(".wx-stats");
    if (p.ok) for (const [k, v] of [["feels", p.feels + "°"], ["humid", p.humidity + "%"],
        ["wind", `${p.wind} km/h ${p.wind_dir || ""}`], ["uv", p.uv],
        ["lo/hi", `${p.min}°/${p.max}°`]]) {
      const s = document.createElement("span");
      s.innerHTML = `${k} <b></b>`; s.querySelector("b").textContent = v; stats.appendChild(s);
    }
    grid.appendChild(card);
  });
  if (d.tz) $("wx-tz").textContent = d.tz;
  $("wx-updated").textContent = "updated " + new Date().toLocaleTimeString("en-GB",
    { hour: "2-digit", minute: "2-digit" });
}

async function loadWeather() {
  try { renderWeather(await API("/api/weather")); }
  catch (e) { const g = $("wx-grid"); if (g) g.innerHTML =
    `<div class="wx-load">atmospheric uplink offline — retrying…</div>`; }
}
setInterval(loadWeather, 5 * 60 * 1000);

/* --------------------------------------------------- activity monitor (local) */
let actRunning = false;
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("en-GB",
  { hour: "2-digit", minute: "2-digit" });

async function loadActivity() {
  try {
    const a = await API("/api/activity");
    actRunning = !!a.running;
    const btn = $("watch-btn");
    btn.textContent = actRunning ? "HOLD" : "WATCH";
    btn.classList.toggle("on", actRunning);
    $("act-dot").className = "act-dot" + (actRunning ? " live" : "");
    $("act-app").textContent = actRunning ? (a.current.app || "…") : "off";
    $("act-title").textContent = actRunning && !PRIVACY.on ? (a.current.title || "") : "";
    const bars = $("act-bars"); bars.innerHTML = "";
    if ((a.today || []).length) {
      const max = Math.max(...a.today.map((t) => t.mins), 1);
      a.today.slice(0, 5).forEach((t) => {
        const row = document.createElement("div");
        row.className = "act-bar";
        row.innerHTML = `<span></span><i></i><b>${t.mins >= 60 ? (t.mins / 60).toFixed(1) + "h" : Math.round(t.mins) + "m"}</b>`;
        row.querySelector("span").textContent = t.app;
        row.querySelector("i").style.width = Math.max(4, 100 * t.mins / max) + "%";
        bars.appendChild(row);
      });
    } else bars.innerHTML = `<div class="act-none">no scenes logged yet</div>`;
    const feed = $("act-feed"); feed.innerHTML = "";
    (a.feed || []).slice(0, 5).forEach((f) => {
      const li = document.createElement("li");
      li.innerHTML = `<em>${hhmm(f.ts)}</em><b></b><span></span>`;
      li.querySelector("b").textContent = f.app;
      li.querySelector("span").textContent = PRIVACY.on ? "" : (f.title || "");
      feed.appendChild(li);
    });
    $("act-sum").textContent = actRunning
      ? `since ${a.since} · ${Math.round(a.total_mins)}m accounted · eyes:${a.vision}`
      : "say 'start monitoring', sir";
    const last = (a.reactions || [])[0];
    $("act-react").textContent = last ? "“" + last.text + "”" : "";
  } catch (e) { /* server restarting */ }
}
$("watch-btn").addEventListener("click", async () => {
  try {
    const res = await POST("/api/activity", { action: actRunning ? "stop" : "start" });
    addMsg("jarvis", res.reply, "activity");
    loadActivity();
  } catch (e) { addMsg("jarvis", "Monitor link failed: " + e.message, "system"); }
});
setInterval(loadActivity, 10000);

/* ------------------------------------- hands-free AUTO (continuous, no wake word) */
let hands = null, handsOn = false;
function handsStop() {
  handsOn = false;
  if (hands) { try { hands.stop(); } catch (e) {} hands = null; }
  $("hands-btn").classList.remove("on", "live");
  if (STATE.current === "listening") setState("idle");
}
function handsStart() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { addMsg("jarvis", "Hands-free needs Chrome or Safari, sir.", "system"); return; }
  if (handsOn) return;
  handsOn = true; $("hands-btn").classList.add("on");
  (function listen() {
    if (!handsOn) return;
    hands = new SR();
    hands.lang = "en-GB"; hands.interimResults = false; hands.continuous = true;
    hands.onstart = () => $("hands-btn").classList.add("live");
    hands.onresult = (e) => {
      let final = "";
      for (let i = e.resultIndex; i < e.results.length; i++)
        if (e.results[i].isFinal) final += e.results[i][0].transcript;
      final = final.trim();
      if (!final) return;
      if (STATE.current === "thinking" || STATE.current === "speaking") return; // don't hear ourselves
      send(final);
    };
    hands.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        handsStop(); addMsg("jarvis", "Microphone permission denied, sir.", "system");
      }
    };
    hands.onend = () => { $("hands-btn").classList.remove("live");
      if (handsOn) setTimeout(listen, 400); };   // auto-restart keeps it truly continuous
    try { hands.start(); } catch (e) {}
  })();
}
$("hands-btn").addEventListener("click", () => handsOn ? handsStop() : handsStart());

/* ---------------------------------------------------------------- boot */
(async function boot() {
  initParticles(); initWave(); initCoreFX();
  setState("boot");
  $("privacy-btn").classList.toggle("on", PRIVACY.on);
  $("file-crumbs").textContent = PRIVACY.on ? "hidden — privacy mode on" : "select a folder above";
  await Promise.all([loadSkills(), loadTasks(), pollSystem(), loadWeather(), loadActivity()]);
  const cleared = localStorage.getItem("jarvis_chat_cleared");
  const hist = await API("/api/history?n=20").catch(() => ({ items: [] }));
  hist.items.filter((h) => !cleared || h.ts >= cleared).slice(-4).forEach((h) => {
    addMsg("user", h.heard, "", true);
    addMsg("jarvis", h.reply, "", true);
  });
  const d = addMsg("jarvis", "", "system");
  typewriter(d, "All systems online. How may I assist you, sir?");
  const flash = $("boot-flash");
  flash.classList.add("gone");
  setTimeout(() => flash.remove(), 900);
  setState("idle");
  $("cmd-input").focus();
})();
