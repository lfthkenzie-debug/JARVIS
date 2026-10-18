"""Browser exploration skills — the difference between *firing* a URL at the
visual browser (web.open) and JARVIS actually *reading* the web for you: fetch a
page, digest its prose, enumerate what's clickable, follow a link deeper, and
report back in persona. He can also read the tab you're already looking at."""
from __future__ import annotations

import re

from jarvis.config import log
from jarvis.core import browser, ostools
from jarvis.core.registry import command


_URL_RE = re.compile(r"(https?://[\w./%=?&#+~-]+|www\.[\w./%=?&#+-]+|"
                     r"[\w-]+\.[a-z]{2,6}(?:/[\w./%=?&#+-]*)?)", re.I)


def _url_from(text: str) -> str | None:
    m = _URL_RE.search(text or "")
    if not m:
        return None
    url = m.group(1).rstrip(".,;:!?)")
    if url.startswith("www.") or "://" not in url:
        # don't invent a scheme for bare domain-looking words that aren't dotted paths
        if "." not in url.split("/")[0]:
            return None
        url = "https://" + url
    return url


def _link_line(i: int, lnk: dict) -> str:
    return f"  {i}. {lnk['text'][:48]}  —  {lnk['url'][:70]}"


@command("browser.explore", "Read and summarise a web page, then list what's clickable.",
         patterns=[r"\b(?:explore|browse)\b",
                   r"\b(?:read|summari[sz]e|digest|check|go ?through|what(?:'s| is)? on)\b[^.]{0,40}"
                   r"\b(?:http|www\.|[\w-]+\.[a-z]{2,6}|page|link|url|site|website|article)\b"],
         examples=["explore https://news.ycombinator.com", "browse example.com",
                   "read this page", "summarize that article", "what's on this page",
                   "check the link https://python.org", "go through this website"],
         # negative priority: web.search's \bgoogle\b and web.wiki's 'wikipedia' are
         # priority 0 and would otherwise hijack 'explore google.com' in step 1.
         priority=-5)
def browser_explore(utterance: str) -> str:
    url = _url_from(utterance)
    try:
        if url:
            data = browser.load(url)
            where = data["final_url"]
        elif browser.has_page():
            data = browser.current()
            where = data["final_url"]
        else:
            return ("Give me a link to read, sir — or say 'read my browser' and I'll "
                    "take a look at whatever's in the front tab.")
    except Exception as exc:
        log(f"browser explore: {exc}")
        return f"I couldn't reach that page — {str(exc)[:120]}"
    digest = browser.summarize(data["text"], data["title"], where)
    n = len(data["links"])
    tail = (f" There are {n} links here — say 'links' to list them, or "
            f"'follow link 2' to go deeper." if n else
            " Nothing worth clicking on that page.")
    return f"【{data['title']}】 {digest}{tail}"


@command("browser.current", "Read the web page currently open in your browser tab.",
         patterns=[r"read (?:my|the) (?:browser|tab|page)\b",
                   r"what(?:'s| am i| is)? ?(?:looking at|on my screen right now|page am i on)",
                   r"\bthis tab\b|current tab|what (?:page|site|url) (?:am i|i'?m on)",
                   r"whats? (?:this|the current) page"],
         examples=["read my browser", "what am I looking at", "read this tab",
                   "what page am I on", "what's this page"],
         priority=6)
def browser_current(utterance: str) -> str:
    app, _title = ostools.frontmost_app()
    url, tab_title = ostools.active_tab(app)
    if not url:
        return ("I can't see a browser tab right now, sir — bring Safari or Chrome to "
                "the front (and grant the one-time Automation prompt) and try again.")
    if not re.search(r"https?://", url):
        return f"That tab doesn't look like a web page: {url[:80]}"
    try:
        data = browser.load(url)
    except Exception as exc:
        log(f"browser current: {exc}")
        return f"The tab is {tab_title or url} but I couldn't read it — {str(exc)[:100]}"
    digest = browser.summarize(data["text"], data["title"], data["final_url"])
    n = len(data["links"])
    tail = f" {n} links here — say 'links'." if n else ""
    return f"You're on 【{data['title']}】. {digest}{tail}"


@command("browser.links", "List the clickable links on the page JARVIS last read.",
         patterns=[r"\b(?:links?|hrefs?|options)\b",
                   r"(?:what|show|list|enumerate).{0,18}\b(?:can i (?:click|go)|go next|next)\b",
                   r"what(?:'s| is) clickable"],
         examples=["what links are here", "show me the links", "what can I click",
                   "list the links", "what are my options"],
         priority=6)
def browser_links(utterance: str) -> str:
    if not browser.has_page():
        return "I haven't read a page yet — explore one first and I'll list its links."
    page = browser.current()
    links = browser.list_links(15)
    if not links:
        return f"No links to follow on {page['title']}."
    rows = "\n".join(_link_line(i, lnk) for i, lnk in enumerate(links, 1))
    more = (f"\n  … {len(page['links']) - len(links)} more." if len(page["links"]) > len(links) else "")
    return f"Links on 【{page['title']}】:\n{rows}{more}\nSay 'follow link N' to open one."


@command("browser.follow", "Follow a numbered link (or one matched by text) from the page.",
         patterns=[r"\b(?:follow|open|click|visit|go to|try)\b[^.]{0,16}\b(?:link|result|item|number|page)\b ?\d*",
                   r"\b(?:the )?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b"
                   r"[^.]{0,12}\b(?:link|result|one|item)\b",
                   r"\blink (?:number )?\d+\b", r"^(?:link |#)?\d{1,2}$"],
         examples=["follow link 3", "open the second link", "click link number 1",
                   "go to result 5", "the third one", "link 2"],
         priority=6)
def browser_follow(utterance: str) -> str:
    if not browser.has_page():
        return "There's no page open to follow a link from — explore one first."
    token = re.sub(r"https?://\S+", "", utterance)
    link = browser.find_link(token)
    if not link:
        avail = browser.list_links(10)
        listing = "\n".join(_link_line(i, l) for i, l in enumerate(avail, 1))
        return f"I couldn't match that to a link on this page. Top links:\n{listing}"
    try:
        data = browser.load(link["url"])
    except Exception as exc:
        log(f"browser follow: {exc}")
        return f"Link {link['n']} ({link['text']}) wouldn't load — {str(exc)[:100]}"
    digest = browser.summarize(data["text"], data["title"], data["final_url"])
    n = len(data["links"])
    tail = f" {n} new links — 'links' to list, 'go back' to return." if n else " Say 'go back' to return."
    return f"Followed #{link['n']} → 【{data['title']}】. {digest}{tail}"


@command("browser.back", "Go back to the previously read page.",
         patterns=[r"\bgo ?back\b|\bprevious (?:page|link|one)\b|\bup a level\b",
                   r"\bbrowser back\b|\breturn to (?:the )?(?:previous|last) (?:page|link)"],
         examples=["go back", "previous page", "go back to the last link", "browser back"],
         priority=6)
def browser_back(utterance: str) -> str:
    url = browser.go_back()
    if not url:
        return "There's nowhere further back to go, sir — I've only just started reading."
    data = browser.current()
    digest = browser.summarize(data["text"], data["title"], data["final_url"])
    return f"Back to 【{data['title']}】. {digest}"


@command("browser.watch_on", "Browse the web alongside you — read whatever tab you linger on and remark on it live.",
         patterns=[r"(?:browse|surf|read|explore|go ?through) (?:the )?(?:web|internet|sites?|pages?|links?|tabs?)"
                   r"(?: with me| alongside me| together| buddy| copilot)\b",
                   r"(?:browse|surf|read|watch) (?:the web|my browsing|my tabs?|what i'?m reading) (?:with|alongside) me",
                   r"react to (?:the|my) (?:pages?|sites?|websites?|links?|browsing|tabs?|what i'?m (?:reading|browsing|looking at|on))",
                   r"comment on (?:the|my) (?:pages?|sites?|websites?|browsing|what i'?m reading)",
                   r"read over my shoulder|(?:browse|web|reading) (?:together|copilot|buddy)"],
         examples=["browse the web with me", "react to the pages I'm reading",
                   "comment on the sites I browse", "read over my shoulder",
                   "browse together", "surf the web with me"],
         # priority -6: beat browser.explore (-5) so 'browse the web with me' toggles
         # together-mode instead of being read as a plain 'browse <url>' request.
         priority=-6)
def browser_watch_on(utterance: str) -> str:
    from jarvis.core import activity
    if not activity.running():
        activity.start()
    activity.enable_web(True)
    return ("Pulling up a chair beside you, sir — point your browser at a page and "
            "hold it a moment and I'll read it over your shoulder and say what I think. "
            "I keep it brief — a first impression, maybe one aside — and I won't "
            "narrate every tab. Say 'stop browsing with me' for the quiet.")


@command("browser.watch_off", "Stop JARVIS remarking on the pages you browse (timeline keeps running).",
         patterns=[r"stop (?:browsing|reading) (?:with me|alongside me|together|over my shoulder)",
                   r"stop (?:reacting|commenting) (?:to|on) (?:the|my) (?:pages?|sites?|websites?|links?|browsing)",
                   r"don'?t (?:react|comment) (?:to|on) (?:the|my) (?:pages?|sites?|websites?|links?|browsing|reading|what i'?m (?:reading|browsing))",
                   r"(?:hush|be quiet|quiet) (?:about|on) my (?:pages?|browsing|reading)",
                   r"stop reading over my shoulder"],
         examples=["stop browsing with me", "don't react to the pages I'm reading",
                   "stop commenting on my sites", "hush about my browsing",
                   "stop reading over my shoulder"],
         priority=-6)
def browser_watch_off(utterance: str) -> str:
    from jarvis.core import activity
    activity.enable_web(False)
    return ("Quiet on the browsing, sir — the timeline still runs unless you say "
            "'stop monitoring' altogether.")
