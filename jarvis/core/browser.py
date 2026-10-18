"""Headless text browser — fetch a page, read it, list what's clickable, and
follow links deeper. This is the layer that lets JARVIS *explore* a URL rather
than only firing it at the visual browser: he pulls the HTML, strips it down to
readable text and links, holds the page in a small session so 'follow link 3'
or 'go back' work, and hands the prose to the LLM to summarise.

stdlib urllib + BeautifulSoup only — no browser engine, no JS. Good for articles,
docs, wikis, listings; useless for SPAs that render everything client-side, and
we say so honestly rather than inventing a summary of an empty <div>."""
from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
from typing import Optional

from bs4 import BeautifulSoup

from jarvis.config import log
from jarvis.core import net

MAX_TEXT = 6000          # characters of body prose kept for the summary prompt
MAX_LINKS = 60           # clickable links captured per page
FETCH_TIMEOUT = 12

# Session: the page JARVIS last read, so follow-up commands need no re-typing URL.
_page: dict = {"url": "", "final_url": "", "title": "", "description": "",
               "text": "", "links": [], "fetched_at": 0.0}
_history: list[str] = []   # stack of prior urls for go_back()


def _strip_js_css(html: str) -> str:
    return re.sub(r"<(script|style|noscript|svg)\b.*?</\1>", " ",
                  html or "", flags=re.I | re.S)


def fetch(url: str) -> tuple[str, str]:
    """Return (final_url, html) after following redirects. Raises on HTTP error."""
    if not re.match(r"^https?://", url):
        url = "https://" + url
    req = urllib.request.Request(url, headers=net.UA)
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT, context=net._context()) as r:
        return r.geturl(), r.read().decode("utf-8", "ignore")


def parse(html: str, base_url: str) -> dict:
    """Pull readable text + resolved, de-duplicated links out of a page."""
    soup = BeautifulSoup(_strip_js_css(html), "html.parser")
    for tag in soup(["nav", "footer", "header", "aside", "form", "iframe"]):
        tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else ""
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else base_url
    desc_tag = soup.find("meta", attrs={"name": "description"}) or \
        soup.find("meta", attrs={"property": "og:description"})
    description = (desc_tag.get("content", "") if desc_tag else "").strip()

    parts: list[str] = []
    for el in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        t = el.get_text(" ", strip=True)
        if len(t) > 2:
            parts.append(t)
    text = re.sub(r"\s{2,}", " ", "\n".join(parts)).strip()[:MAX_TEXT]

    links: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        label = a.get_text(" ", strip=True)[:60]
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        absu = urllib.parse.urljoin(base_url, href)[:300]
        if absu in seen:
            continue
        seen.add(absu)
        links.append({"text": label, "url": absu})
        if len(links) >= MAX_LINKS:
            break

    # short human label per link: fall back to the domain/path when anchor text is empty
    for i, lnk in enumerate(links, 1):
        if not lnk["text"]:
            path = urllib.parse.urlsplit(lnk["url"]).path.strip("/") or "index"
            lnk["text"] = (path.split("/")[-1][:40]) or f"link {i}"
    return {"title": title[:160], "description": description[:300],
            "text": text, "links": links}


def load(url: str, keep_history: bool = True) -> dict:
    """Fetch + parse + store into the session. Returns the full page record
    (parsed fields plus the original and redirect-resolved URLs)."""
    final, html = fetch(url)
    data = parse(html, final)
    if keep_history and _page["url"]:
        _history.append(_page["url"])
        del _history[:-20]
    _page.update(url=url, final_url=final, fetched_at=time.time(), **data)
    return current()


def current() -> dict:
    return dict(_page)


def has_page() -> bool:
    return bool(_page.get("final_url")) and (time.time() - _page.get("fetched_at", 0)) < 900


def go_back() -> Optional[str]:
    """Pop the history stack and reload the previous page. Returns its url or None."""
    while _history:
        url = _history.pop()
        try:
            load(url, keep_history=False)
            return url
        except Exception as exc:
            log(f"browser back {url}: {exc}")
            continue
    return None


def find_link(token: str) -> Optional[dict]:
    """Resolve a link reference: a 1-based number ('3', 'link 3', 'the third
    one') or a substring of the anchor text/url. Returns {'n', 'text', 'url'}."""
    links = _page.get("links", [])
    if not links:
        return None
    token = (token or "").strip().lower()
    m = re.search(r"(\d+)", token)
    if m and (token == m.group(1) or "link" in token or "one" in token or
              token.startswith("page") or token.startswith("#")):
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(links):
            lnk = links[idx]
            return {"n": idx + 1, "text": lnk["text"], "url": lnk["url"]}
        return None
    # ordinal words
    ordinals = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
                "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
    for word, n in ordinals.items():
        if re.search(rf"\b{word}\b", token):
            if 0 <= n - 1 < len(links):
                lnk = links[n - 1]
                return {"n": n, "text": lnk["text"], "url": lnk["url"]}
    # substring match against anchor text, then url
    if token:
        for i, lnk in enumerate(links):
            if token in lnk["text"].lower():
                return {"n": i + 1, "text": lnk["text"], "url": lnk["url"]}
        for i, lnk in enumerate(links):
            if token in lnk["url"].lower():
                return {"n": i + 1, "text": lnk["text"], "url": lnk["url"]}
    return None


def summarize(text: str, title: str, url: str) -> str:
    """Hand page prose to the LLM for a short persona-flavoured digest."""
    body = (text or "").strip()
    if len(body) < 40:
        return ("Not much readable text there, sir — looks like a page that builds "
                "itself in the browser with JavaScript, which I can't see. Open it "
                "visually and I'll read whatever lands in the tab.")
    try:
        from jarvis.skills.chat import _llm
        prompt = (
            "Summarise this web page for me in two or three crisp sentences, "
            "then name the single most interesting takeaway. Be direct, no preamble.\n"
            f"Title: {title}\nURL: {url}\nContent:\n{body[:MAX_TEXT]}"
        )
        out = _llm.ask(prompt, history=False)
        if out and out.strip():
            return out.strip()[:700]
    except Exception as exc:
        log(f"browser summarize: {exc}")
    return f"{title}: {body[:280]}"


def list_links(n: int = 12) -> list[dict]:
    return _page.get("links", [])[:n]


# --------------------------------------------------------------- "together" mode
_REMARK_REFUSAL = re.compile(
    r"can'?t (?:provide|help|assist|answer)|unable to|explicit content|"
    r"i (?:don'?t|do not) have access|as an ai|not (?:able|comfortable)", re.I)


def _plain(text: str, limit: int = 500) -> str:
    """Squeeze fetched prose to one clean line for a prompt."""
    text = re.sub(r"https?://\S+|www\.\S+", "", text or "")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def remark(page: dict, first: bool = True) -> str:
    """A short, over-the-shoulder observation about a page — the 'browsing
    together' voice, chattier and briefer than the formal `summarize` digest.
    Returns '' when the page has nothing readable (a JS shell), so the watcher
    stays silent rather than babbling about an empty div."""
    body = _plain(page.get("text", ""), 1400)
    if len(body) < 60:
        return ""
    angle = ("Give a good-natured first impression." if first else
             "I'm still reading it — offer a fresh aside, a detail or a question, "
             "not a repeat of the opening summary.")
    prompt = (
        "React like a mate glancing over my shoulder as I browse the web. This is "
        "just me reading a normal page — nothing sensitive. One or two sentences, "
        f"witty, honest and specific, with your own taste. {angle} "
        "Don't open with 'Oh' or 'Well', don't start every line with 'Sir', and "
        "give an actual observation about the content, not a vague non-answer.\n"
        f"Page: {page.get('title', '')}\nURL: {page.get('final_url', '')}\n"
        f"Content: {body}")
    try:
        from jarvis.skills.chat import _llm
        out = _llm.ask(prompt, history=False)
        if out and not _REMARK_REFUSAL.search(out):
            return out.strip()[:320]
    except Exception as exc:
        log(f"browser remark: {exc}")
    return ""
