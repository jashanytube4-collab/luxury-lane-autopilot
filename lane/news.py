"""Fresh news for stories: the reporter's scored stories, plus the text of the articles from a short list of trusted
news sites (nothing else is ever opened). The script is then written from that text by the free local model.

A story is only used when it scores above `news.min_score` and at least two trusted articles could be read, so the
script rests on facts confirmed by several publishers.
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

import requests

log = logging.getLogger("lane.news")

TRUSTED = {"gulfnews.com", "khaleejtimes.com", "thenationalnews.com", "arabnews.com", "gulftoday.ae", "emirates247.com",
           "bbc.com", "bbc.co.uk", "reuters.com", "apnews.com", "ndtv.com", "timesofindia.indiatimes.com",
           "hindustantimes.com", "indianexpress.com", "thehindu.com", "economictimes.indiatimes.com",
           "gulfbusiness.com", "arabianbusiness.com", "wam.ae", "dubaimediaoffice.gov.ae", "aljazeera.com",
           "cnn.com", "theguardian.com", "news18.com", "indiatoday.in", "firstpost.com", "mid-day.com"}


@dataclass
class NewsStory:
    key: str
    label: str
    score: float
    sources: list[dict]            # [{"title", "date", "text"}] — the article texts for the writer
    publishers: list[str]
    items: list[dict] = field(default_factory=list)   # [{"title", "url", "date"}] — enough to read the articles again


def _trusted(host: str) -> bool:
    host = host.lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in TRUSTED)


def article_text(url: str) -> str:
    """The readable text of one article on a trusted site; '' for anything else. Only the text of <p> tags is kept and
    nothing on the page is ever run or followed."""
    if not _trusted(urlparse(url).netloc):
        return ""
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"}, stream=True)
        if not _trusted(urlparse(r.url).netloc) or "html" not in r.headers.get("content-type", ""):
            return ""
        body = r.raw.read(1_500_000, decode_content=True).decode(r.encoding or "utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        log.info("article not readable (%s): %s", url[:80], e)
        return ""
    body = re.sub(r"(?is)<(script|style|noscript|aside|nav|footer|header)\b.*?</\1>", " ", body)
    paras = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", p))).strip()
             for p in re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", body)]
    paras = [p for p in paras if len(p) >= 60 and not re.search(
        r"cookie|subscribe|sign up|newsletter|advertis|seasoned journalists|all rights reserved", p, re.I)]
    return " ".join(paras)[:4500]


def story_key(title: str) -> str:
    return " ".join(sorted(w for w in re.findall(r"[a-z]{4,}", title.lower()))[:6])


def best(cursors: dict, min_score: float, today: date) -> NewsStory | None:
    """The highest-scoring fresh story not covered yet, with the text of its trusted articles; None if there is none."""
    from tools.reporter import collect
    try:
        good, _, _, _ = collect()
    except Exception as e:  # noqa: BLE001 — the news feed must never stop a run
        log.warning("reporter failed: %s", e)
        return None
    done = cursors.setdefault("news_done", {})
    for group, pubs, _, _, score in good:
        if score <= min_score:
            break
        key = story_key(group[0]["title"])
        if key in done:
            continue
        sources, items = [], []
        for it in group:
            if len(sources) >= 5:
                break
            text = article_text(it.get("url", ""))
            if len(text) >= 300 and re.search(r"hamdan|fazza|maktoum", text, re.I):
                sources.append({"title": it["title"], "date": it["when"].date().isoformat(), "text": text})
                items.append({"title": it["title"], "url": it["url"], "date": sources[-1]["date"]})
        publishers = sorted({urlparse(it.get("url", "")).netloc.removeprefix("www.") for it in group if it.get("url")})
        if len(sources) >= 2:
            return NewsStory(key, group[0]["title"], score, sources, publishers, items)
        log.info("news story %r skipped: only %d trusted articles readable", group[0]["title"][:60], len(sources))
    return None


def mark_done(cursors: dict, story: NewsStory, today: date) -> None:
    cursors.setdefault("news_done", {})[story.key] = today.isoformat()


def rebuild(d: dict) -> NewsStory | None:
    """A NewsStory saved in a day plan (key, label, score, items) with its article texts read again."""
    sources = []
    for it in d.get("items", []):
        text = article_text(it["url"])
        if len(text) >= 300:
            sources.append({"title": it["title"], "date": it["date"], "text": text})
    if len(sources) < 2:
        return None
    pubs = sorted({urlparse(it["url"]).netloc.removeprefix("www.") for it in d["items"]})
    return NewsStory(d["key"], d["label"], d["score"], sources, pubs, d["items"])
