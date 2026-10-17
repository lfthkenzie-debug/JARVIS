"""Web commands — search, open sites, weather, Wikipedia digests, time & date."""
from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime

from jarvis.config import HOME, log, reveal
from jarvis.core import net
from jarvis.core.registry import command


def _get(url: str, timeout: int = 10) -> str:
    return net.get(url, timeout=timeout)


@command("web.search", "Web search — DuckDuckGo instant answers, opened in browser.",
         patterns=[r"\bsearch (?:the )?web\b|\blook up\b|\bgoogle\b|\bfind information about\b"],
         examples=["search the web for latest python release", "google quantum computing news",
                   "look up the capital of Australia", "find information about gradient boosting"])
def web_search(utterance: str) -> str:
    q = re.sub(r"^.*?(?:search (?:the )?web for|google|look up|find information about)\s+", "",
               utterance).strip()
    if not q:
        return "What should I search for?"
    try:
        data = json.loads(_get("https://api.duckduckgo.com/?" + urllib.parse.urlencode(
            {"q": q, "format": "json", "no_html": 1, "skip_disambig": 1})))
        answer = data.get("AbstractText") or data.get("Answer") or ""
        if answer:
            topic = data.get("Heading", q)
            return f"{topic}: {str(answer)[:300]}"
    except Exception as exc:
        log(f"DDG API: {exc}")
    reveal("https://duckduckgo.com/html/?q=" + urllib.parse.quote_plus(q))
    return f"Opening search results for {q}."


@command("web.open", "Open any URL in the default browser.",
         patterns=[r"https?://|\bopen (?:the )?(?:site|website|url|page)\b"],
         examples=["open github.com", "go to https://news.ycombinator.com",
                   "open the python documentation site"],
         priority=20)
def open_url(utterance: str) -> str:
    m = re.search(r"https?://[\w./%=?&#+-]+", utterance)
    if m:
        url = m.group(0)
    else:
        m = re.search(r"(?:open|go to)\s+(?:the\s+)?(?:site\s+|website\s+|page\s+)?([\w-]+\.[a-z]{2,6}[^\s]*)",
                      utterance)
        if not m:
            return "Which URL?"
        url = "https://" + m.group(1).strip().rstrip("?.")
    reveal(url)
    return f"Opened {url}."


@command("web.wiki", "Wikipedia digest for any topic.",
         patterns=[r"wikipedia|who (?:is|was)|what (?:is|was) (?:the )?\w+"],
         examples=["who is ada lovelace", "wikipedia quantum entanglement",
                   "what is the roman empire", "tell me about alan turing"])
def wiki(utterance: str) -> str:
    q = re.sub(r"^.*?(?:search wikipedia for|wikipedia|who (?:is|was)|what (?:is|was) (?:the )?|tell me about)\s+", "",
               utterance).strip()
    if not q or len(q) < 2:
        return "Name a topic, sir."
    try:
        url = ("https://en.wikipedia.org/api/rest_v1/page/summary/" +
               urllib.parse.quote(q.replace(" ", "_")))
        data = json.loads(_get(url))
        extract = data.get("extract", "")
        if extract:
            return f"{data.get('title', q)}: {extract[:320]}"
    except Exception as exc:
        log(f"Wikipedia: {exc}")
    reveal("https://en.wikipedia.org/wiki/Special:Search?search=" + urllib.parse.quote_plus(q))
    return f"Opening the Wikipedia page for {q}."


@command("web.weather", "Weather report for your location.",
         patterns=[r"\bweather\b|temperature outside|forecast (?:today|tomorrow)"
                   r"|\b(?:will it|is it|going to)\b[^.]{0,15}\b(?:rain|snow|pour)\b|\bumbrella\b"],
         examples=["what's the weather", "weather tokyo", "will it rain tomorrow",
                   "how hot is it outside", "temperature new york"])
def weather(utterance: str) -> str:
    m = re.search(r"weather(?:\s+(?:in|for|at))?\s+([\w ]+)$", utterance)
    place = m.group(1).strip() if m else ""
    try:
        out = _get(f"https://wttr.in/{urllib.parse.quote(place)}?format=j1", timeout=8)
        data = json.loads(out)
        cur = data["current_condition"][0]
        area = data.get("nearest_area", [{}])[0]
        city = area.get("areaName", [{}])[0].get("value", place or "your area")
        desc = cur["weatherDesc"][0]["value"]
        tomorrow = data["weather"][1]
        return (f"{city}: {desc}, {cur['temp_C']}°C feels {cur['FeelsLikeC']}°C, "
                f"humidity {cur['humidity']}%. Tomorrow {tomorrow['mintempC']}–{tomorrow['maxtempC']}°C.")
    except Exception as exc:
        log(f"wttr.in: {exc}")
        return "Weather service unreachable right now."


@command("sys.time", "Current time, date, and countdowns.",
         patterns=[r"\btime\b|\bdate\b|what day|\bwhats\b.{0,10}today|today.{0,10}\bdate\b"],
         examples=["what time is it", "what is the date", "what day is it",
                   "how many days until christmas"])
def time_cmd(utterance: str) -> str:
    now = datetime.now()
    if "until" in utterance or "till" in utterance:
        m = re.search(r"until\s+(\w+)", utterance)
        if m:
            target_name = m.group(1).lower()
            targets = {"christmas": (12, 25), "new year": (1, 1), "halloween": (10, 31),
                       "your birthday": None}
            t = targets.get(target_name)
            if t:
                nxt = datetime(now.year, t[0], t[1])
                if nxt < now:
                    nxt = datetime(now.year + 1, t[0], t[1])
                return f"{(nxt - now).days} days until {target_name}."
        return "Name the event and I'll count down."
    if "date" in utterance or "day" in utterance:
        return f"Today is {now:%A, %d %B %Y}."
    return f"The time is {now:%H:%M}."
