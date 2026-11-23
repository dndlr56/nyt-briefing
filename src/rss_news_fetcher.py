"""BBC, CNBC, NPR 공식 RSS에서 최근 글로벌 뉴스 후보를 수집하고 본문 전문을 추출합니다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import html
import re
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import requests

from guardian_fetcher import ArticleCandidate


HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/xml, application/rss+xml, text/html;q=0.9, */*;q=0.8",
}

MULTI_OUTLET_FEEDS: list[tuple[str, str, str]] = [
    # (source_name, section_label, feed_url)
    ("BBC", "World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC", "Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("BBC", "Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("CNBC", "Economy", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("CNBC", "Finance", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("CNBC", "Technology", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"),
    ("NPR", "World", "https://feeds.npr.org/1004/rss.xml"),
    ("NPR", "Business", "https://feeds.npr.org/1006/rss.xml"),
    ("NPR", "Technology", "https://feeds.npr.org/1019/rss.xml"),
]

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(raw: str | None) -> str:
    if not raw:
        return ""
    stripped = _HTML_TAG_RE.sub(" ", raw)
    return _WS_RE.sub(" ", html.unescape(stripped)).strip()


def _parse_pubdate(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _normalize_url(url: str) -> str:
    return (url or "").split("?")[0].strip().rstrip("/")


def fetch_multi_outlet_candidates(
    hours: int = 24,
    max_per_outlet: dict[str, int] | None = None,
    timeout: int = 12,
) -> list[ArticleCandidate]:
    """BBC, CNBC, NPR 공식 RSS에서 최근 hours 이내 기사 후보를 수집합니다."""
    limits = max_per_outlet or {"BBC": 20, "CNBC": 18, "NPR": 15}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    by_source: dict[str, list[ArticleCandidate]] = {k: [] for k in limits}
    seen_urls: set[str] = set()
    counters: dict[str, int] = {k: 0 for k in limits}

    for source_name, section_label, feed_url in MULTI_OUTLET_FEEDS:
        if len(by_source.get(source_name, [])) >= limits.get(source_name, 15):
            continue
        try:
            resp = requests.get(feed_url, headers=HTTP_HEADERS, timeout=timeout)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as exc:
            print(f"      [WARN] {source_name} ({section_label}) RSS fetch failed: {exc}")
            continue

        for item in root.findall(".//item"):
            title = _clean_text(item.findtext("title"))
            link = (item.findtext("link") or "").strip()
            desc = _clean_text(item.findtext("description"))
            pub_dt = _parse_pubdate(item.findtext("pubDate"))
            if not title or not link or pub_dt is None or pub_dt < cutoff:
                continue

            norm = _normalize_url(link)
            if norm in seen_urls:
                continue
            seen_urls.add(norm)

            counters[source_name] = counters.get(source_name, 0) + 1
            prefix = source_name.lower()
            by_source.setdefault(source_name, []).append(
                ArticleCandidate(
                    article_id=f"{prefix}:{counters[source_name]}",
                    section=section_label,
                    title=title,
                    trail_text=desc,
                    url=link,
                    published_at=pub_dt,
                    wordcount=max(300, len(desc.split()) * 8),
                    source=source_name,
                )
            )

    combined: list[ArticleCandidate] = []
    for source_name, limit in limits.items():
        items = sorted(by_source.get(source_name, []), key=lambda c: c.published_at, reverse=True)
        combined.extend(items[:limit])
    return combined


def fetch_html_article_body(
    url: str,
    max_body_chars: int = 8000,
    timeout: int = 15,
) -> str:
    """BBC, CNBC, NPR 기사 페이지에서 본문 HTML 텍스트를 추출합니다."""
    if not url:
        return ""
    html_text = ""
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=timeout)
        resp.raise_for_status()
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        html_text = resp.text
    except Exception:
        try:
            from curl_cffi import requests as cffi_requests  # type: ignore[import-not-found]

            c_resp = cffi_requests.get(url, impersonate="chrome", headers=HTTP_HEADERS, timeout=timeout)
            if 200 <= c_resp.status_code < 300:
                html_text = c_resp.text
        except Exception:
            return ""

    if not html_text:
        return ""

    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"]):
        tag.decompose()

    container = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.body
        or soup
    )
    paras = [
        _WS_RE.sub(" ", p.get_text(" ", strip=True)).strip()
        for p in container.find_all("p")
    ]
    paras = [p for p in paras if len(p) >= 25]
    body_text = "\n\n".join(paras).strip()
    if len(body_text) > max_body_chars:
        body_text = body_text[:max_body_chars].rsplit(" ", 1)[0] + "..."
    return body_text
