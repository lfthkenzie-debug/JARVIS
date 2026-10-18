"""YouTube skills — open, search-and-play, trending, personal picks, reactions,
watch history, and OAuth uploads. Reads need an API key (jarvis_data/youtube_api_key.txt
or JARVIS_YT_KEY); uploads additionally need an OAuth client secret."""
from __future__ import annotations

import re

from jarvis.core import youtube as yt
from jarvis.core.registry import command

_NOKEY = ("I need a YouTube API key for that, sir — paste one into "
          "jarvis_data/youtube_api_key.txt or set JARVIS_YT_KEY. "
          "Console → 'YouTube Data API v3' → create credentials.")


def _strip_query(u: str, *prefixes: str) -> str:
    """Remove command words from either end, leaving the subject."""
    q = u.lower()
    for p in prefixes:
        q = re.sub(p, "", q)
    q = re.sub(r"\bjarvis\b|\bplease\b|\bfor me\b", "", q)
    return q.strip(" ?.,!'\"").strip()


@command("yt.open", "Open YouTube in the browser.",
         patterns=[r"\b(?:open|launch|start|go to|show)\b[^.]{0,15}\byoutube\b"
                   r"|\byoutube\b[^.]{0,10}\b(?:home|up)\b"],
         examples=["open youtube", "launch youtube", "go to youtube",
                   "show me the youtube homepage", "youtube home"],
         priority=9)
def yt_open(utterance: str) -> str:
    if re.search(r"\bdone\b|close|quit|stop", utterance):
        return "YouTube stays where you left it, sir."
    from jarvis.config import reveal
    reveal("https://www.youtube.com")
    return "YouTube, sir. The rabbit hole is open."


@command("yt.play", "Search YouTube and play the top match (or a given URL).",
         patterns=[r"\bplay\b.{0,60}\byoutube\b|\byoutube\b.{0,20}\bplay\b"
                   r"|\b(?:put on|queue up|search youtube(?: for)?)\b"],
         examples=["play lofi beats on youtube", "youtube play never give up",
                   "put on some jazz on youtube", "search youtube for deep work"],
         priority=8)
def yt_play(utterance: str) -> str:
    if not yt.have_key():
        return _NOKEY
    url_vid = yt.video_id(utterance)
    if url_vid:
        yt.open_video(url_vid)
        return "On your screen, sir."
    quoted = re.search(r"['\"\u2018\u201c]([^'\"\u2019\u201d]{3,})['\"\u2019\u201d]", utterance)
    if quoted:
        hits = yt.search(quoted.group(1), max_results=1)
        if not hits:
            return f"Nothing on YouTube under '{quoted.group(1)}', sir."
        yt.open_video(hits[0]["id"])
        return f"Playing '{hits[0]['title']}' by {hits[0]['channel']}, sir."
    q = _strip_query(utterance,
                     r"^.*?\bplay\b|\bon youtube\b", r"^.*?\bput on\b|\bon youtube\b",
                     r"^.*?\bqueue up\b|\bon youtube\b",
                     r"^.*?\bsearch youtube for\b", r"^.*?\byoutube\b")
    q = re.sub(r"^(?:the video|video|some)\s+", "", q)
    if not q:
        return "Play what, sir?"
    try:
        hits = yt.search(q, max_results=3)
    except Exception as exc:
        return f"YouTube API misbehaved, sir ({exc})."
    if not hits:
        return f"Nothing worth playing under '{q}', sir."
    top = hits[0]
    yt.open_video(top["id"])
    others = " Three more in the results." if len(hits) > 1 else ""
    return f"Playing '{top['title']}' by {top['channel']}.{others}"


@command("yt.trending", "What's trending on YouTube right now.",
         patterns=[r"youtube trending|trending (?:on )?youtube"
                   r"|what'?s trending|\bpopular videos?\b|top videos? today"],
         examples=["what's trending on youtube", "youtube trending",
                   "show me popular videos today", "top videos on youtube"],
         priority=9)
def yt_trending(utterance: str) -> str:
    if not yt.have_key():
        return _NOKEY
    try:
        vid = yt.trending(max_results=5)
    except Exception as exc:
        return f"Trending feed refused me, sir ({exc})."
    if not vid:
        return "Trending is empty — a first, sir."
    lines = "; ".join(f"{i + 1}. {v['title']} ({v['channel']}, "
                      f"{yt._fmt_n(v['views'])} views)" for i, v in enumerate(vid))
    return f"Trending in Indonesia: {lines}."


@command("yt.pick", "JARVIS chooses a video he himself wants to watch and puts it on.",
         patterns=[r"(?:watch|choose|pick|pick out) (?:me )?(?:a|some|any) video"
                   r"|you (?:want to |wanna )?watch something"
                   r"|surprise me (?:with )?(?:a|some) video|\bsuggest a video\b"
                   r"|\bput something on\b|\bdive into any video\b"],
         examples=["pick a video for us to watch", "you want to watch something",
                   "surprise me with a video", "suggest a video", "put something on"],
         priority=8)
def yt_pick(utterance: str) -> str:
    mood = _strip_query(utterance, r"^.*?\b(?:about|for|like|in the mood for)\b")
    return yt.pick_for_me(mood if len(mood) > 2 else "")


@command("yt.react", "JARVIS reacts to a video — the last one you watched, a URL, or a topic.",
         patterns=[r"react to (?:that|the|my|this)(?: last)? video"
                   r"|what do you think of (?:this|that|the) video"
                   r"|(?:watched|saw|viewed) (?:that|this|a) video"
                   r"|react to my (?:watching|last)|your opinion on (?:this|that) video"],
         examples=["react to that video", "what do you think of this video?",
                   "I just watched that video, react", "your opinion on that video"],
         priority=8)
def yt_react(utterance: str) -> str:
    vid = yt.video_id(utterance)
    if not vid:
        m = re.search(r"https?://\S+", utterance)
        if m:
            vid = yt.video_id(m.group(0))
    if vid:
        return yt.react_to_video(vid)
    last = yt.last_watched()
    if last and last.get("vid"):
        return f"On '{last['title']}': " + yt.react_to_video(last["vid"])
    topic = _strip_query(utterance, r"^.*?\bvideo\b")
    if topic:
        return yt.react(topic)
    return ("I don't know which video you mean, sir — give me a link, "
            "or watch something via 'play ... on youtube' first.")


@command("yt.history", "Your JARVIS-logged YouTube watch history and stats.",
         patterns=[r"(?:youtube )?watch (?:history|stats|log)"
                   r"|what (?:did i|have i) watch|videos? (?:i (?:watched|have watched)"
                   r"|have i watched)|how many videos"],
         examples=["what videos did I watch", "show my watch history",
                   "youtube watch stats", "what have I been watching"],
         priority=9)
def yt_history(utterance: str) -> str:
    h = yt.watch_history(8)
    if not h:
        return yt.watch_stats()
    seen, lines = set(), []
    for x in h:
        if x["vid"] in seen:
            continue
        seen.add(x["vid"])
        lines.append(f"{x['title'] or x['vid']} ({x['channel'] or 'unknown'})")
    return f"Last watched: {'; '.join(lines[:5])}. {yt.watch_stats()}"


@command("yt.upload", "Upload a video file to your YouTube channel (OAuth, commanded only).",
         patterns=[r"upload (?:a |the |this |my )?video|upload (?:it |this )?to youtube"
                   r"|\bpublish (?:this|that|the|my) video|put (?:this|that) video on youtube"],
         examples=["upload this video to youtube", "publish my video",
                   "upload the file ~/Movies/demo.mp4", "put this video on youtube"],
         priority=8)
def yt_upload(utterance: str) -> str:
    from pathlib import Path
    from jarvis.config import HOME
    m = re.search(r"[~]?/[\w./-]+\.(?:mp4|mov|mkv|avi|webm|m4v)", utterance) \
        or re.search(r"[\w][\w ./_-]*\.(?:mp4|mov|mkv|avi|webm|m4v)", utterance) \
        or re.search(r"['\"]([^'\"]+)['\"]", utterance)
    if not m:
        return ("Which file, sir? Name it — e.g. \"upload ~/Movies/demo.mp4 to youtube\".")
    raw = m.group(0).strip("\"' ")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        for root in (HOME / "Movies", HOME / "Downloads"):
            cand = root / raw
            if cand.is_file():
                path = cand
                break
    title = ""
    tm = re.search(r"(?:titled?|named?|called)\s+['\"]?([^'\"\.]+)", utterance, re.I)
    if tm:
        title = tm.group(1).strip()
    priv = "unlisted" if re.search(r"\bunlisted\b", utterance) else \
        "public" if re.search(r"\bpublicly?\b|make it public", utterance) else "private"
    return yt.upload(str(path), title=title, privacy=priv)


@command("yt.auth", "Run the Google OAuth consent so JARVIS can upload videos.",
         patterns=[r"(?:authorise|authorize|auth)\b.{0,20}youtube|youtube (?:oauth|auth)"
                   r"|connect (?:my )?youtube|link (?:my )?youtube"],
         examples=["authorise youtube", "connect my youtube", "grant you youtube access",
                   "youtube oauth"],
         priority=8)
def yt_auth(utterance: str) -> str:
    return yt.authorize()


@command("yt.watch_on", "Watch the YouTube you browse in your browser and react to it live.",
         patterns=[r"react to (?:my|the) (?:youtube|videos? i'?m watching|what i'?m watching)",
                   r"(?:watch|follow|monitor|track) (?:my (?:youtube|viewing|watching)|what i'?m watching)",
                   r"comment on (?:my|the) videos?|see what i'?m (?:watching|viewing)",
                   r"know (?:what|when) i'?m (?:watching|putting on)", r"react in real time"],
         examples=["react to my youtube in real time", "watch what I'm watching on youtube",
                   "comment on my videos", "see what I'm watching", "follow my viewing"],
         priority=8)
def yt_watch_on(utterance: str) -> str:
    from jarvis.core import activity
    if not activity.running():
        activity.start()
    activity.enable_yt(True)
    return ("Settling in beside you, sir — whatever's on that tab is now on my screen "
            "too. I'll keep a running commentary going as it plays and hold my tongue "
            "between the good bits. Say 'stop reacting to my videos' when you want the "
            "quiet back.")


@command("yt.watch_off", "Stop JARVIS reacting to the videos you browse (timeline keeps running).",
         patterns=[r"stop react(?:ing)? to (?:my|the) (?:youtube|videos?)",
                   r"don'?t (?:react|comment) (?:to|on) my (?:videos?|youtube)",
                   r"stop comment(?:ing) on my videos|(?:hush|be quiet about) my (?:videos?|youtube)",
                   r"stop (?:watching|following) my (?:youtube|viewing)"],
         examples=["stop reacting to my youtube", "don't comment on my videos",
                   "stop following my viewing", "hush about my videos"],
         priority=8)
def yt_watch_off(utterance: str) -> str:
    from jarvis.core import activity
    activity.enable_yt(False)
    return ("Silent on your viewing, sir — the timeline still runs unless you "
            "say 'stop monitoring' altogether.")
