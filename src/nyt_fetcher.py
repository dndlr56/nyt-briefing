"""New York Times 공식 RSS 피드에서 최근 주요 기사를 수집합니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET

import requests


NYT_RSS_FEEDS: list[tuple[str, str]] = [
    ("HomePage", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"),
    ("Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("Economy", "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml"),
    ("Technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
    ("World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("US", "https://rss.nytimes.com/services/xml/rss/nyt/US.xml"),
    ("Science", "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml"),
    ("Health", "https://rss.nytimes.com/services/xml/rss/nyt/Health.xml"),
]


@dataclass
class Article:
    section: str
    title: str
    description: str
    url: str
    published_at: datetime


def _parse_pubdate(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def fetch_recent_articles(
    hours: int = 36,
    max_per_feed: int = 12,
    timeout: int = 15,
) -> list[Article]:
    """각 NYT RSS 피드에서 최근 `hours`시간 이내 기사를 수집하고 중복을 제거합니다."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    seen_urls: set[str] = set()
    articles: list[Article] = []

    headers = {
        "User-Agent": "NYT-Morning-Briefing-Bot/1.0 (+https://github.com)",
    }

    for section, feed_url in NYT_RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as exc:
            print(f"[WARN] Failed to fetch {section} feed: {exc}")
            continue

        count = 0
        for item in root.findall("./channel/item"):
            if count >= max_per_feed:
                break

            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            pub_dt = _parse_pubdate(item.findtext("pubDate"))

            if not title or not link:
                continue

            # 쿼리 파라미터 제거 후 중복 확인
            canonical_url = link.split("?")[0]
            if canonical_url in seen_urls:
                continue

            if pub_dt and pub_dt < cutoff:
                continue

            seen_urls.add(canonical_url)
            articles.append(
                Article(
                    section=section,
                    title=title,
                    description=description,
                    url=canonical_url,
                    published_at=pub_dt or datetime.now(timezone.utc),
                )
            )
            count += 1

    # 최신 기사 순으로 정렬
    articles.sort(key=lambda a: a.published_at, reverse=True)
    return articles
