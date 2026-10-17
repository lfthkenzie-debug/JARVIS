"""Web scraping — MechanicalSoup for interactive/form pages, Scrapy for
structured multi-page crawls. Results saved to Downloads."""
from __future__ import annotations

import csv
import re
import subprocess
from datetime import datetime
from pathlib import Path

from jarvis.config import HOME, SPIDER_DIR, log
from jarvis.core.registry import command

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _url_from(utterance: str) -> str | None:
    m = re.search(r"https?://[\w./%=?&#+-]+", utterance)
    if m:
        return m.group(0)
    m = re.search(r"(?:scrape|grab|fetch|extract)(?:\s+\w+)*\s+([\w-]+\.(?:com|org|io|net|dev|ai|co\.uk))",
                  utterance)
    return f"https://{m.group(1)}" if m else None


# ------------------------------------------------------------------ quick scrape (MechanicalSoup)
@command("scrape.page", "Scrape titles/links from a page with MechanicalSoup.",
         patterns=[r"scrape|extract (?:links|titles|data) from|grab (?:the )?(?:page|site)"],
         examples=["scrape example com", "extract links from news.ycombinator.com",
                   "grab the titles from this page", "scrape https://quotes.toscrape.com"],
         priority=8)
def scrape_page(utterance: str) -> str:
    url = _url_from(utterance)
    if not url:
        return "Which URL, sir?"
    want_links = "link" in utterance
    want_quotes = "quote" in utterance
    try:
        import mechanicalsoup as ms
        browser = ms.Browser(raise_on_status=False, useragent=UA)
        page = browser.get(url)
        if page.status_code >= 400:
            return f"Site returned HTTP {page.status_code}."
        soup = page.soup
        title = soup.title.string.strip() if soup.title and soup.title.string else url
        if want_quotes:
            texts = [q.get_text(strip=True) for q in soup.select(".text, .quote .text")][:5]
            if texts:
                return f"{len(texts)} quotes captured. First: {texts[0]}"
        if want_links:
            links = [(a.get_text(strip=True)[:40], a["href"]) for a in soup.find_all("a")
                     if a.get("href") and a.get_text(strip=True)]
            out = HOME / "Downloads" / f"jarvis_links_{datetime.now():%H%M%S}.csv"
            with out.open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["text", "href"])
                w.writerows(links[:500])
            return f"Extracted {len(links)} links from {title}; CSV saved to Downloads."
        headings = [h.get_text(" ", strip=True)[:60] for h in soup.select("h1, h2")][:6]
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text()) > 40]
        summary = paras[0][:180] if paras else (headings[0] if headings else "no text found")
        return f"{title}. Top sections: {', '.join(headings[:3]) or 'none'}. Lead: {summary}"
    except ImportError:
        return "MechanicalSoup not installed. Run ./setup.sh."
    except Exception as exc:
        return f"Scrape failed: {exc}"


# ------------------------------------------------------------------ login-aware scrape
@command("scrape.form", "Fill a web form and retrieve results (MechanicalSoup forms).",
         patterns=[r"log ?in to|fill (?:the )?form|submit (?:the )?search"],
         examples=["login to site and scrape data", "fill the search form on example com",
                   "submit search query on wikipedia"])
def scrape_form(utterance: str) -> str:
    url = _url_from(utterance)
    if not url:
        return "I need a URL to drive the form."
    query = re.search(r"(?:query|search for|form with)\s+[\"]?([\w ]+)", utterance)
    try:
        import mechanicalsoup as ms
        browser = ms.Browser(useragent=UA)
        page = browser.get(url)
        forms = list(page.soup.find_all("form"))
        if not forms:
            return "No forms detected on that page."
        form = browser.get_form(url) or forms[0]
        if query:
            for el in (form.select("input[type=text]") + form.select("input:not([type])")):
                el["value"] = query.group(1).strip()
                break
            result = browser.submit_selected()
            titles = [a.get_text(strip=True) for a in result.soup.find_all("a")][:8]
            return f"Submitted search for {query.group(1).strip()}. Results: {'; '.join(titles[:5])}"
        return f"Detected {len(forms)} form(s) on {url}; name the fields to fill."
    except Exception as exc:
        return f"Form automation failed: {exc}"


# ------------------------------------------------------------------ deep crawl (Scrapy)
@command("scrape.crawl", "Generate and run a Scrapy spider for a multi-page crawl.",
         patterns=[r"crawl|deep ?scrape|spider"],
         examples=["crawl books to scrape dot com", "run a scrapy spider on example com",
                   "deep crawl the documentation site"])
def crawl(utterance: str) -> str:
    url = _url_from(utterance)
    if not url:
        return "What site should I crawl?"
    try:
        import scrapy  # type: ignore
    except ImportError:
        return "Scrapy not installed — run ./setup.sh to add it."
    domain = re.sub(r"\W+", "_", url.split("//")[-1].split("/")[0])
    spider_path = SPIDER_DIR / f"jarvis_spider_{domain}.py"
    limit = re.search(r"(\d+)\s*(?:pages|items)", utterance)
    cap = int(limit.group(1)) if limit else 30
    spider_path.write_text(f'''# Auto-generated JARVIS spider — {url}
import scrapy, csv, pathlib

class JarvisSpider(scrapy.Spider):
    name = "jarvis_{domain}"
    start_urls = ["{url}"]
    custom_settings = {{"CLOSE_SPIDER_AFTER": {cap}, "ROBOTSTXT_OBEY": True,
                       "DOWNLOAD_DELAY": 0.5, "LOG_LEVEL": "WARNING"}}

    def parse(self, response):
        yield {{"url": response.url,
                "title": response.css("title::text").get() or "",
                "h1": response.css("h1::text").get() or ""}}
        for href in response.css("a::attr(href)").getall()[:40]:
            if len(response.request.url.split("//")[-1]) < 200:
                yield response.follow(href, callback=self.parse,
                                      errback=lambda _: None)
''')
    out_json = SPIDER_DIR / f"crawl_{domain}.json"
    r = subprocess.run(
        ["python3", "-m", "scrapy", "crawl", f"jarvis_{domain}",
         "-s", f"FEEDS={out_json}", "-s", "FEED_EXPORT_ENCODING=utf-8",
         "--set", f"MAX_REQUESTS={cap}", "--set", f"CLOSE_SPIDER_COUNT={cap}"],
        cwd=SPIDER_DIR, capture_output=True, text=True, timeout=180)
    if out_json.exists():
        return f"Crawl finished for {url}; up to {cap} items saved to jarvis_data/crawl_{domain}.json."
    if r.returncode != 0:
        return f"Scrapy reported: {r.stderr.strip()[-200:]}"
    return f"Crawl launched for {url}; check jarvis_data for output."
