from datetime import datetime, timezone, date

from lane import news


def _group():
    now = datetime.now(timezone.utc)
    return [{"title": "Sheikh Hamdan visits hospital after accident", "publisher": "gulfnews.com", "when": now,
             "url": "https://gulfnews.com/a"},
            {"title": "Dubai Crown Prince at bedside", "publisher": "khaleejtimes.com", "when": now,
             "url": "https://khaleejtimes.com/b"}]


def test_best_needs_score_and_two_readable_trusted_articles(monkeypatch):
    g = _group()
    monkeypatch.setattr("tools.reporter.collect", lambda: ([(g, {"gulfnews.com", "khaleejtimes.com"}, True, False, 8.0)], [], 5, 2))
    monkeypatch.setattr(news, "article_text", lambda url: "Sheikh Hamdan visited the hospital. " * 20)
    cur = {}
    item = news.best(cur, 6, date.today())
    assert item and item.score == 8.0 and len(item.sources) == 2
    news.mark_done(cur, item, date.today())
    assert news.best(cur, 6, date.today()) is None            # not covered twice


def test_low_score_or_unreadable_is_ignored(monkeypatch):
    g = _group()
    monkeypatch.setattr("tools.reporter.collect", lambda: ([(g, {"a", "b"}, True, False, 5.0)], [], 5, 2))
    monkeypatch.setattr(news, "article_text", lambda url: "x" * 400 + " Hamdan")
    assert news.best({}, 6, date.today()) is None
    monkeypatch.setattr("tools.reporter.collect", lambda: ([(g, {"a", "b"}, True, False, 9.0)], [], 5, 2))
    monkeypatch.setattr(news, "article_text", lambda url: "")
    assert news.best({}, 6, date.today()) is None


def test_untrusted_sites_are_never_opened():
    assert news.article_text("https://evil.example.com/story") == ""
