"""AntennaPod 등 표준 팟캐스트 앱용 RSS 2.0(iTunes) 피드와 정적 페이지를 생성합니다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import html
import json
from pathlib import Path
import shutil
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from curator import DailyBriefing


KST = timezone(timedelta(hours=9))
DEFAULT_PODCAST_TITLE = "글로벌 모닝 브리핑"
DEFAULT_PODCAST_DESCRIPTION = (
    "매일 아침 7시(KST), The Guardian 전문과 NYT 주요 기사 중 "
    "빅테크·AI·글로벌 경제·기업·인프라·건강 핵심 뉴스 10~12건을 엄선해 들려드리는 개인 모닝 브리핑입니다."
)
DEFAULT_PODCAST_AUTHOR = "Global Morning Briefing"
MAX_EPISODES = 30


def _format_itunes_duration(seconds: int) -> str:
    """초 단위 길이를 HH:MM:SS 형식으로 변환합니다."""
    total = max(1, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def build_episode_show_notes_html(
    briefing: DailyBriefing | None,
    usage_footer_html: str | None = None,
) -> str:
    """AntennaPod 에피소드 설명란에 표시할 읽기용 HTML 쇼노트를 생성합니다."""
    footer = f"\n{usage_footer_html}" if usage_footer_html else ""
    if briefing is None:
        return f"<p>오늘의 글로벌 모닝 브리핑 오디오 에피소드입니다.</p>{footer}"

    parts: list[str] = [
        f"<p><strong>{html.escape(briefing.morning_intro_kr)}</strong></p>",
        "<hr/>",
    ]
    for idx, art in enumerate(briefing.articles, start=1):
        cat = html.escape(art.category_kr)
        title_kr = html.escape(art.title_kr)
        title_en = html.escape(art.title_en)
        source = html.escape(art.source)
        url = html.escape(art.url, quote=True)
        bullets_html = "".join(
            f"<li>{html.escape(b)}</li>" for b in art.summary_bullets
        )
        parts.append(
            f"<p><strong>{idx}. [{cat}] {title_kr}</strong><br/>"
            f"<em>{title_en}</em> ({source})</p>"
            f"<ul>{bullets_html}</ul>"
            f"<p><a href=\"{url}\">원문 기사 보기</a></p>"
        )
    if usage_footer_html:
        parts.append(usage_footer_html)
    return "\n".join(parts)


def build_episode_summary_plain(briefing: DailyBriefing | None, episode_title: str) -> str:
    """짧은 플레인 텍스트 요약(itunes:subtitle/summary용)을 생성합니다."""
    if briefing is None:
        return episode_title
    headlines = " / ".join(art.title_kr for art in briefing.articles[:4])
    return f"{briefing.morning_intro_kr} 주요 소식: {headlines}"


def _cdata(text: str) -> str:
    """XML CDATA 섹션 안전 래핑."""
    safe = text.replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{safe}]]>"


def _render_rss_xml(
    episodes: list[dict[str, Any]],
    base_url: str,
    podcast_title: str = DEFAULT_PODCAST_TITLE,
    podcast_description: str = DEFAULT_PODCAST_DESCRIPTION,
    podcast_author: str = DEFAULT_PODCAST_AUTHOR,
) -> str:
    """에피소드 메타데이터 목록으로 표준 RSS 2.0 + iTunes XML을 생성합니다."""
    clean_base = base_url.rstrip("/")
    feed_url = f"{clean_base}/feed.xml"
    last_build = format_datetime(datetime.now(KST))

    items_xml: list[str] = []
    for ep in episodes:
        mp3_url = f"{clean_base}/episodes/{ep['mp3_filename']}"
        title = xml_escape(ep["title"])
        guid = xml_escape(ep["guid"])
        pub_date = xml_escape(ep["pub_date_rfc2822"])
        duration = xml_escape(_format_itunes_duration(ep["duration_seconds"]))
        size_bytes = int(ep["file_size_bytes"])
        summary_plain = xml_escape(ep.get("summary_plain") or ep["title"])
        desc_html = ep.get("description_html") or f"<p>{title}</p>"

        items_xml.append(
            "    <item>\n"
            f"      <title>{title}</title>\n"
            f"      <guid isPermaLink=\"false\">{guid}</guid>\n"
            f"      <pubDate>{pub_date}</pubDate>\n"
            f"      <description>{_cdata(desc_html)}</description>\n"
            f"      <itunes:summary>{summary_plain}</itunes:summary>\n"
            f"      <itunes:duration>{duration}</itunes:duration>\n"
            f"      <itunes:explicit>false</itunes:explicit>\n"
            f"      <enclosure url=\"{xml_escape(mp3_url)}\" length=\"{size_bytes}\" type=\"audio/mpeg\"/>\n"
            "    </item>"
        )

    items_block = "\n".join(items_xml)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<rss version=\"2.0\"\n"
        "     xmlns:itunes=\"http://www.itunes.com/dtds/podcast-1.0.dtd\"\n"
        "     xmlns:atom=\"http://www.w3.org/2005/Atom\">\n"
        "  <channel>\n"
        f"    <title>{xml_escape(podcast_title)}</title>\n"
        f"    <link>{xml_escape(clean_base)}/</link>\n"
        f"    <atom:link href=\"{xml_escape(feed_url)}\" rel=\"self\" type=\"application/rss+xml\"/>\n"
        "    <language>ko-kr</language>\n"
        f"    <description>{xml_escape(podcast_description)}</description>\n"
        f"    <lastBuildDate>{xml_escape(last_build)}</lastBuildDate>\n"
        f"    <itunes:author>{xml_escape(podcast_author)}</itunes:author>\n"
        f"    <itunes:summary>{xml_escape(podcast_description)}</itunes:summary>\n"
        "    <itunes:explicit>false</itunes:explicit>\n"
        "    <itunes:category text=\"News\">\n"
        "      <itunes:category text=\"Daily News\"/>\n"
        "    </itunes:category>\n"
        f"{items_block}\n"
        "  </channel>\n"
        "</rss>\n"
    )


def _render_index_html(
    episodes: list[dict[str, Any]],
    base_url: str,
    podcast_title: str = DEFAULT_PODCAST_TITLE,
    podcast_description: str = DEFAULT_PODCAST_DESCRIPTION,
) -> str:
    """브라우저에서 RSS 주소 확인 및 바로 듣기를 지원하는 간단한 인덱스 페이지."""
    clean_base = base_url.rstrip("/")
    feed_url = f"{clean_base}/feed.xml"

    cards: list[str] = []
    for ep in episodes[:15]:
        mp3_rel = f"episodes/{html.escape(ep['mp3_filename'], quote=True)}"
        dur = _format_itunes_duration(ep["duration_seconds"])
        cards.append(
            "<article class='card'>"
            f"<h2>{html.escape(ep['title'])}</h2>"
            f"<p class='meta'>{html.escape(ep['pub_date_rfc2822'])} · {dur}</p>"
            f"<audio controls preload='none' src='{mp3_rel}'></audio>"
            f"<details><summary>기사 요약 및 원문 링크 보기</summary>"
            f"<div class='notes'>{ep.get('description_html', '')}</div>"
            "</details>"
            "</article>"
        )

    cards_html = "\n".join(cards) if cards else "<p>아직 등록된 에피소드가 없습니다.</p>"
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html.escape(podcast_title)}</title>
  <link rel="alternate" type="application/rss+xml" title="{html.escape(podcast_title)}" href="feed.xml" />
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 760px; margin: 2rem auto; padding: 0 1rem; color: #1f2937; line-height: 1.6; }}
    h1 {{ margin-bottom: 0.25rem; }}
    .rss-box {{ background: #f3f4f6; padding: 0.9rem 1rem; border-radius: 8px; margin: 1rem 0 2rem; word-break: break-all; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 10px; padding: 1.1rem 1.25rem; margin-bottom: 1rem; }}
    .card h2 {{ margin: 0 0 0.25rem; font-size: 1.1rem; }}
    .meta {{ color: #6b7280; font-size: 0.85rem; margin: 0 0 0.75rem; }}
    audio {{ width: 100%; margin-bottom: 0.6rem; }}
    details summary {{ cursor: pointer; color: #2563eb; font-size: 0.92rem; }}
    .notes {{ margin-top: 0.75rem; font-size: 0.92rem; }}
  </style>
</head>
<body>
  <h1>🎧 {html.escape(podcast_title)}</h1>
  <p>{html.escape(podcast_description)}</p>
  <div class="rss-box">
    <strong>AntennaPod RSS 구독 주소:</strong><br/>
    <a href="{html.escape(feed_url, quote=True)}">{html.escape(feed_url)}</a>
  </div>
  {cards_html}
</body>
</html>
"""


def publish_podcast_episode(
    pages_dir: Path,
    base_url: str,
    mp3_path: Path,
    episode_title: str,
    duration_seconds: int,
    date_slug: str,
    briefing: DailyBriefing | None = None,
    usage_footer_html: str | None = None,
    max_episodes: int = MAX_EPISODES,
) -> Path:
    """새 에피소드 MP3를 pages_dir에 복사하고 feed.xml 및 index.html을 갱신합니다."""
    if not mp3_path.exists():
        raise FileNotFoundError(f"배포할 MP3 파일이 없습니다: {mp3_path}")

    episodes_dir = pages_dir / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)

    target_mp3 = episodes_dir / mp3_path.name
    if mp3_path.resolve() != target_mp3.resolve():
        shutil.copy2(mp3_path, target_mp3)

    metadata_path = episodes_dir / "metadata.json"
    episodes: list[dict[str, Any]] = []
    if metadata_path.exists():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                episodes = loaded
        except (OSError, json.JSONDecodeError):
            episodes = []

    now_kst = datetime.now(KST)
    guid = f"global-briefing-{date_slug}"
    new_entry: dict[str, Any] = {
        "guid": guid,
        "date_slug": date_slug,
        "title": episode_title,
        "pub_date_rfc2822": format_datetime(now_kst),
        "published_iso": now_kst.isoformat(),
        "mp3_filename": target_mp3.name,
        "file_size_bytes": target_mp3.stat().st_size,
        "duration_seconds": int(duration_seconds),
        "summary_plain": build_episode_summary_plain(briefing, episode_title),
        "description_html": build_episode_show_notes_html(
            briefing,
            usage_footer_html=usage_footer_html,
        ),
    }

    # 같은 날짜 에피소드가 이미 있으면 교체 (중복 방지)
    episodes = [ep for ep in episodes if ep.get("guid") != guid]
    episodes.insert(0, new_entry)
    episodes.sort(key=lambda ep: ep.get("published_iso", ""), reverse=True)

    # 최근 max_episodes개만 유지하고 오래된 MP3 정리
    if len(episodes) > max_episodes:
        removed = episodes[max_episodes:]
        episodes = episodes[:max_episodes]
        for old_ep in removed:
            old_name = old_ep.get("mp3_filename")
            if old_name:
                (episodes_dir / old_name).unlink(missing_ok=True)

    metadata_path.write_text(
        json.dumps(episodes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    feed_xml = _render_rss_xml(episodes=episodes, base_url=base_url)
    feed_path = pages_dir / "feed.xml"
    feed_path.write_text(feed_xml, encoding="utf-8")

    index_html = _render_index_html(episodes=episodes, base_url=base_url)
    (pages_dir / "index.html").write_text(index_html, encoding="utf-8")

    return feed_path
