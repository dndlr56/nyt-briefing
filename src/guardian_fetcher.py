"""The Guardian Open Platform API에서 무료 전문(Full Text) 기사를 2단계로 수집합니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import html
import os
import re

import requests


GUARDIAN_API_BASE = "https://content.guardianapis.com"

# 독자 관심사(경제·테크 50%, 글로벌 시사 30%, 과학·기후·건강 20%)에 맞춘 섹션 목록
GUARDIAN_SECTIONS: list[tuple[str, str]] = [
    ("business", "Business"),
    ("technology", "Technology"),
    ("world", "World"),
    ("science", "Science"),
    ("environment", "Environment"),
]

_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class ArticleCandidate:
    article_id: str
    section: str
    title: str
    trail_text: str
    url: str
    published_at: datetime
    wordcount: int


@dataclass
class FullArticle:
    article_id: str
    section: str
    category_kr: str
    title: str
    trail_text: str
    byline: str
    body_text: str
    url: str
    published_at: datetime
    wordcount: int


def _clean_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = _HTML_TAG_RE.sub("", raw)
    return html.unescape(text).strip()


def _parse_iso_datetime(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return datetime.now(timezone.utc)


def _get_api_key(api_key: str | None = None) -> str:
    key = api_key or os.getenv("GUARDIAN_API_KEY")
    if not key:
        raise ValueError(
            "GUARDIAN_API_KEY 환경변수가 필요합니다. "
            "https://open-platform.theguardian.com/access/ 에서 무료 API 키를 발급받아 등록해 주세요."
        )
    return key


def fetch_guardian_candidates(
    hours: int = 36,
    page_size: int = 15,
    min_wordcount: int = 250,
    api_key: str | None = None,
    timeout: int = 15,
) -> list[ArticleCandidate]:
    """1단계: 제목·요약(trailText)·분량만 가볍게 수집해 큐레이션 후보 목록을 만듭니다."""
    key = _get_api_key(api_key)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    from_date = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    seen_ids: set[str] = set()
    candidates: list[ArticleCandidate] = []

    for section_id, section_label in GUARDIAN_SECTIONS:
        params = {
            "api-key": key,
            "section": section_id,
            "type": "article",
            "from-date": from_date,
            "order-by": "newest",
            "page-size": page_size,
            "show-fields": "headline,trailText,wordcount",
        }
        try:
            resp = requests.get(
                f"{GUARDIAN_API_BASE}/search",
                params=params,
                timeout=timeout,
            )
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"Guardian API 인증 실패 ({resp.status_code}): "
                    "GUARDIAN_API_KEY 값을 다시 확인해 주세요."
                )
            resp.raise_for_status()
            payload = resp.json()
        except RuntimeError:
            raise
        except Exception as exc:
            print(f"[WARN] Failed to fetch Guardian section '{section_id}': {exc}")
            continue

        results = payload.get("response", {}).get("results", [])
        for item in results:
            article_id = item.get("id", "").strip()
            if not article_id or article_id in seen_ids:
                continue

            fields = item.get("fields") or {}
            title = _clean_html(fields.get("headline") or item.get("webTitle"))
            trail_text = _clean_html(fields.get("trailText"))
            url = (item.get("webUrl") or "").strip()
            pub_dt = _parse_iso_datetime(item.get("webPublicationDate"))

            try:
                wordcount = int(fields.get("wordcount") or 0)
            except (TypeError, ValueError):
                wordcount = 0

            if not title or not url:
                continue
            if wordcount and wordcount < min_wordcount:
                continue

            seen_ids.add(article_id)
            candidates.append(
                ArticleCandidate(
                    article_id=article_id,
                    section=section_label,
                    title=title,
                    trail_text=trail_text,
                    url=url,
                    published_at=pub_dt,
                    wordcount=wordcount,
                )
            )

    candidates.sort(key=lambda c: c.published_at, reverse=True)
    return candidates


def fetch_article_full_texts(
    selected_items: list[tuple[ArticleCandidate, str]],
    max_body_chars: int = 10000,
    api_key: str | None = None,
    timeout: int = 20,
) -> list[FullArticle]:
    """2단계: 선별된 5~6개 기사의 전문(bodyText)만 개별 조회합니다."""
    key = _get_api_key(api_key)
    full_articles: list[FullArticle] = []

    for candidate, category_kr in selected_items:
        params = {
            "api-key": key,
            "show-fields": "headline,trailText,byline,bodyText,wordcount",
        }
        try:
            resp = requests.get(
                f"{GUARDIAN_API_BASE}/{candidate.article_id}",
                params=params,
                timeout=timeout,
            )
            resp.raise_for_status()
            content = resp.json().get("response", {}).get("content", {})
        except Exception as exc:
            print(
                f"[WARN] Failed to fetch full text for '{candidate.article_id}': {exc}"
            )
            continue

        fields = content.get("fields") or {}
        body_text = (fields.get("bodyText") or "").strip()
        if not body_text:
            body_text = candidate.trail_text

        if len(body_text) > max_body_chars:
            body_text = body_text[:max_body_chars].rsplit(" ", 1)[0] + "..."

        full_articles.append(
            FullArticle(
                article_id=candidate.article_id,
                section=candidate.section,
                category_kr=category_kr,
                title=_clean_html(fields.get("headline")) or candidate.title,
                trail_text=_clean_html(fields.get("trailText")) or candidate.trail_text,
                byline=_clean_html(fields.get("byline")),
                body_text=body_text,
                url=content.get("webUrl") or candidate.url,
                published_at=candidate.published_at,
                wordcount=candidate.wordcount,
            )
        )

    return full_articles
