"""AntennaPod 등 표준 팟캐스트 앱용 RSS 2.0(iTunes) 피드와 정적 페이지를 생성합니다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import html
import json
import os
from pathlib import Path
import shutil
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from curator import DailyBriefing


KST = timezone(timedelta(hours=9))
DEFAULT_PODCAST_TITLE = "글로벌 모닝 브리핑"
DEFAULT_PODCAST_DESCRIPTION = (
    "매일 아침 7시(KST), The Guardian, BBC, CNBC, NPR 전문에서 "
    "빅테크, AI, 글로벌 경제, 기업, 인프라, 건강 핵심 뉴스 10~12건을 엄선해 전하는 일간 오디오 브리핑입니다."
)
DEFAULT_PODCAST_AUTHOR = "Global Morning Briefing"
DEFAULT_COVER_PATH = Path(__file__).resolve().parent.parent / "assets" / "cover.jpg"
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
    has_cover: bool = True,
    owner_email: str | None = None,
) -> str:
    """에피소드 메타데이터 목록으로 표준 RSS 2.0 + iTunes XML을 생성합니다."""
    clean_base = base_url.rstrip("/")
    feed_url = f"{clean_base}/feed.xml"
    cover_url = f"{clean_base}/cover.jpg"
    last_build = format_datetime(datetime.now(KST))

    resolved_email = (owner_email or os.getenv("PODCAST_OWNER_EMAIL") or "").strip()
    owner_block = ""
    if resolved_email:
        owner_block = (
            "    <itunes:owner>\n"
            f"      <itunes:name>{xml_escape(podcast_author)}</itunes:name>\n"
            f"      <itunes:email>{xml_escape(resolved_email)}</itunes:email>\n"
            "    </itunes:owner>\n"
        )

    cover_block = ""
    if has_cover:
        cover_block = (
            f"    <itunes:image href=\"{xml_escape(cover_url)}\"/>\n"
            "    <image>\n"
            f"      <url>{xml_escape(cover_url)}</url>\n"
            f"      <title>{xml_escape(podcast_title)}</title>\n"
            f"      <link>{xml_escape(clean_base)}/</link>\n"
            "    </image>\n"
        )

    items_xml: list[str] = []
    for ep in episodes:
        mp3_filename = (ep.get("mp3_filename") or "").strip()
        if not mp3_filename:
            continue
        mp3_rel = (ep.get("mp3_rel_path") or f"episodes/{mp3_filename}").lstrip("/")
        mp3_url = f"{clean_base}/{mp3_rel}"
        title = xml_escape(ep["title"])
        guid = xml_escape(ep["guid"])
        pub_date = xml_escape(ep["pub_date_rfc2822"])
        duration = xml_escape(_format_itunes_duration(ep.get("duration_seconds", 1)))
        size_bytes = int(ep.get("file_size_bytes", 0))
        summary_plain = xml_escape(ep.get("summary_plain") or ep["title"])
        desc_html = ep.get("description_html") or f"<p>{title}</p>"
        link_url = (ep.get("link_url") or "").strip()
        link_line = f"      <link>{xml_escape(link_url)}</link>\n" if link_url else ""

        items_xml.append(
            "    <item>\n"
            f"      <title>{title}</title>\n"
            f"{link_line}"
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
        f"{owner_block}"
        f"    <itunes:summary>{xml_escape(podcast_description)}</itunes:summary>\n"
        "    <itunes:explicit>false</itunes:explicit>\n"
        f"{cover_block}"
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
    """글로벌 모닝 브리핑 에디토리얼 웹 아카이브 페이지."""
    from deep_dive.web_publisher import (
        SHARED_EDITORIAL_CSS,
        SHARED_FONTS_HEAD,
        SHARED_UI_SCRIPT,
        THEME_INIT_SCRIPT,
    )

    esc = html.escape
    clean_base = base_url.rstrip("/")
    feed_url = f"{clean_base}/feed.xml"

    cards: list[str] = []
    for idx, ep in enumerate(episodes[:20]):
        mp3_rel = f"episodes/{esc(ep['mp3_filename'], quote=True)}"
        dur = _format_itunes_duration(ep.get("duration_seconds", 0))
        raw_ds = (ep.get("date_slug") or "").strip()
        if len(raw_ds) == 8 and raw_ds.isdigit():
            date_str = f"{raw_ds[:4]}.{raw_ds[4:6]}.{raw_ds[6:]}"
        else:
            date_str = raw_ds.replace("-", ".") or ep.get("pub_date_rfc2822", "")[:16]
        featured_cls = " featured" if idx == 0 else ""
        latest_tag = "<span class='latest-tag'>오늘의 브리핑</span>" if idx == 0 else ""
        open_attr = " open" if idx == 0 else ""
        summary_plain = ep.get("summary_plain", "")
        search_blob = f"{ep.get('title', '')} {summary_plain}".lower()

        cards.append(
            f"<article class='card{featured_cls}' data-search='{esc(search_blob, quote=True)}'>"
            f"<div class='card-top'>{latest_tag}<span class='meta-date'>{esc(date_str)}</span><span class='meta-info'>재생 시간 {esc(dur)}</span></div>"
            f"<h2>{esc(ep['title'])}</h2>"
            "<div class='audio-deck'>"
            "<div class='audio-deck-top'>"
            "<span class='audio-title'>1인 앵커 해설 오디오</span>"
            "<div class='speed-group' role='group' aria-label='재생 배속'>"
            "<button type='button' class='speed-btn active' data-speed='1'>1.0x</button>"
            "<button type='button' class='speed-btn' data-speed='1.25'>1.25x</button>"
            "<button type='button' class='speed-btn' data-speed='1.5'>1.5x</button>"
            "<button type='button' class='speed-btn' data-speed='1.75'>1.75x</button>"
            "</div></div>"
            f"<audio controls preload='none' src='{mp3_rel}'></audio>"
            "</div>"
            f"<details class='notes-drawer'{open_attr}>"
            "<summary>선별 기사 요약 및 원문 링크</summary>"
            f"<div class='notes'>{ep.get('description_html', '')}</div>"
            "</details>"
            "</article>"
        )

    cards_html = "\n".join(cards) if cards else "<p class='empty-state'>아직 등록된 모닝 브리핑이 없습니다.</p>"
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{esc(podcast_title)}</title>
  <link rel="alternate" type="application/rss+xml" title="{esc(podcast_title)}" href="feed.xml" />
  {SHARED_FONTS_HEAD}
  {THEME_INIT_SCRIPT}
  <style>
    {SHARED_EDITORIAL_CSS}
    .page-wrap {{ max-width: 900px; margin: 0 auto; padding: 2.25rem 1.25rem 4.5rem; }}
    .hero {{ margin-bottom: 1.65rem; padding-bottom: 1.3rem; border-bottom: 1px solid var(--border); }}
    .hero h1 {{ margin: 0 0 0.45rem; font-size: clamp(1.6rem, 3vw, 2.05rem); letter-spacing: -0.025em; }}
    .hero-desc {{ margin: 0 0 0.95rem; color: var(--ink-secondary); font-size: 0.96rem; max-width: 66ch; }}
    .feed-bar {{ display: flex; flex-wrap: wrap; gap: 0.5rem 0.9rem; font-size: 0.84rem; color: var(--ink-muted); }}
    .search-input {{
      width: 100%; margin-bottom: 1.35rem; padding: 0.62rem 0.9rem;
      border: 1px solid var(--border); border-radius: 8px;
      background: var(--surface-sheet); color: var(--ink-primary);
      font-family: inherit; font-size: 0.92rem;
    }}
    .card {{
      background: var(--surface-sheet); border: 1px solid var(--border);
      border-radius: 11px; padding: 1.35rem 1.5rem; margin-bottom: 1rem;
    }}
    .card.featured {{ border-color: var(--accent); border-width: 1.5px; }}
    .card.hidden {{ display: none; }}
    .card-top {{ display: flex; flex-wrap: wrap; align-items: center; gap: 0.55rem; margin-bottom: 0.45rem; font-size: 0.82rem; color: var(--ink-muted); }}
    .latest-tag {{
      background: var(--accent); color: #FFFFFF; font-size: 0.73rem;
      font-weight: 700; padding: 0.16rem 0.5rem; border-radius: 4px;
    }}
    :root[data-color-mode="dark"] .latest-tag {{ color: #0B1018; }}
    .meta-date {{ font-variant-numeric: tabular-nums; font-weight: 600; color: var(--ink-secondary); }}
    .card h2 {{ margin: 0 0 0.85rem; font-size: 1.18rem; letter-spacing: -0.015em; }}
    .audio-deck {{
      background: var(--surface-subtle); border: 1px solid var(--border);
      border-radius: 9px; padding: 0.75rem 0.95rem; margin-bottom: 0.85rem;
    }}
    .audio-deck-top {{ display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }}
    .audio-title {{ font-size: 0.82rem; font-weight: 600; color: var(--ink-secondary); }}
    .speed-group {{ display: inline-flex; gap: 0.25rem; }}
    .speed-btn {{
      border: 1px solid var(--border); background: var(--surface-sheet); color: var(--ink-muted);
      font-family: inherit; font-size: 0.74rem; font-weight: 600; padding: 0.16rem 0.45rem; border-radius: 5px; cursor: pointer;
    }}
    .speed-btn.active {{ background: var(--accent); border-color: var(--accent); color: #FFFFFF; }}
    :root[data-color-mode="dark"] .speed-btn.active {{ color: #0B1018; }}
    audio {{ width: 100%; height: 36px; display: block; }}
    .notes-drawer {{ border-top: 1px solid var(--border); padding-top: 0.75rem; }}
    .notes-drawer summary {{
      cursor: pointer; font-weight: 600; font-size: 0.9rem; color: var(--accent);
      list-style: none; display: flex; justify-content: space-between; align-items: center;
    }}
    .notes-drawer summary::-webkit-details-marker {{ display: none; }}
    .notes-drawer summary::after {{ content: "펼치기"; font-size: 0.78rem; }}
    .notes-drawer[open] summary::after {{ content: "접기"; }}
    .notes {{ margin-top: 0.9rem; font-size: 0.93rem; line-height: 1.72; color: var(--ink-primary); }}
    .notes hr {{ border: none; border-top: 1px solid var(--border); margin: 0.9rem 0; }}
    .notes ul {{ padding-left: 1.2rem; margin: 0.4rem 0 0.65rem; }}
    .notes li {{ margin-bottom: 0.3rem; }}
    .empty-state {{
      text-align: center; padding: 2.5rem 1rem; color: var(--ink-muted);
      background: var(--surface-sheet); border: 1px dashed var(--border); border-radius: 10px;
    }}
  </style>
</head>
<body>
  <header class="masthead">
    <div class="masthead-inner">
      <nav class="channel-tabs" aria-label="브리핑 채널 전환">
        <a class="channel-tab active" href="index.html">글로벌 모닝 뉴스</a>
        <a class="channel-tab" href="deep/index.html">Deep Dive 심층 연구</a>
      </nav>
      <div class="masthead-tools">
        <button type="button" class="tool-btn" data-copy-rss="{esc(feed_url, quote=True)}">통합 RSS 복사</button>
        <button type="button" class="tool-btn" id="color-mode-btn">화면 테마 전환</button>
      </div>
    </div>
  </header>

  <main class="page-wrap">
    <section class="hero">
      <h1>{esc(podcast_title)}</h1>
      <p class="hero-desc">{esc(podcast_description)}</p>
      <div class="feed-bar">
        <span>매일 오전 07:00 KST 발행</span>
        <span>AntennaPod 통합 구독 RSS (뉴스 + Deep Dive): <a href="{esc(feed_url, quote=True)}">{esc(feed_url)}</a></span>
      </div>
    </section>

    <input type="search" id="news-search" class="search-input" placeholder="날짜, 기업명, 키워드로 지난 뉴스 브리핑 검색" aria-label="모닝 브리핑 검색" />
    <section id="news-cards">{cards_html}</section>
  </main>

  {SHARED_UI_SCRIPT}
  <script>
  (function(){{
    var input = document.getElementById('news-search');
    var cards = document.querySelectorAll('#news-cards .card');
    if (input) {{
      input.addEventListener('input', function(){{
        var q = input.value.trim().toLowerCase();
        cards.forEach(function(c){{
          var hay = (c.getAttribute('data-search') || '') + ' ' + c.textContent.toLowerCase();
          c.classList.toggle('hidden', Boolean(q) && hay.indexOf(q) === -1);
        }});
      }});
    }}
  }})();
  </script>
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

    now_kst = datetime.now(KST)
    time_slug = now_kst.strftime("%H%M%S")
    guid = f"global-briefing-{date_slug}-{time_slug}"

    target_mp3 = episodes_dir / f"global_briefing_{date_slug}_{time_slug}.mp3"
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

    # 같은 날짜 에피소드가 이미 있으면 교체하고 이전 MP3 파일도 정리 (CDN/안테나팟 캐시 충돌 방지)
    replaced = [
        ep
        for ep in episodes
        if ep.get("date_slug") == date_slug or ep.get("guid") == guid
    ]
    for old_ep in replaced:
        old_name = old_ep.get("mp3_filename")
        if old_name and old_name != target_mp3.name:
            (episodes_dir / old_name).unlink(missing_ok=True)

    episodes = [
        ep
        for ep in episodes
        if ep.get("date_slug") != date_slug and ep.get("guid") != guid
    ]
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

    has_cover = False
    target_cover = pages_dir / "cover.jpg"
    if DEFAULT_COVER_PATH.exists():
        if DEFAULT_COVER_PATH.resolve() != target_cover.resolve():
            shutil.copy2(DEFAULT_COVER_PATH, target_cover)
        has_cover = True
    elif target_cover.exists():
        has_cover = True

    feed_path = rebuild_unified_root_feed(
        pages_dir=pages_dir,
        base_url=base_url,
        has_cover=has_cover,
    )

    index_html = _render_index_html(episodes=episodes, base_url=base_url)
    (pages_dir / "index.html").write_text(index_html, encoding="utf-8")

    return feed_path


def _load_metadata_list(metadata_path: Path) -> list[dict[str, Any]]:
    if not metadata_path.exists():
        return []
    try:
        loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def rebuild_unified_root_feed(
    pages_dir: Path,
    base_url: str,
    has_cover: bool | None = None,
    owner_email: str | None = None,
) -> Path:
    """
    루트 뉴스(episodes/metadata.json)와 Deep Dive(deep/episodes/metadata.json)를
    단일 통합 팟캐스트 피드(feed.xml)로 병합해 최신순으로 렌더링합니다.
    각자의 메타데이터·MP3 보관함은 독립 유지되므로 덮어쓰기나 정리가 꼬이지 않습니다.
    """
    clean_base = base_url.rstrip("/")
    combined: list[dict[str, Any]] = []
    seen_guids: set[str] = set()

    # 1) 글로벌 모닝 뉴스 에피소드
    for ep in _load_metadata_list(pages_dir / "episodes" / "metadata.json"):
        mp3 = (ep.get("mp3_filename") or "").strip()
        guid = (ep.get("guid") or "").strip()
        if not mp3 or not guid or guid in seen_guids:
            continue
        seen_guids.add(guid)
        item = dict(ep)
        item["mp3_rel_path"] = f"episodes/{mp3}"
        combined.append(item)

    # 2) Deep Dive 지식 브리핑 에피소드
    for ep in _load_metadata_list(pages_dir / "deep" / "episodes" / "metadata.json"):
        mp3 = (ep.get("mp3_filename") or "").strip()
        guid = (ep.get("guid") or "").strip()
        if not mp3 or not guid or guid in seen_guids:
            continue
        seen_guids.add(guid)
        item = dict(ep)
        item["mp3_rel_path"] = f"deep/episodes/{mp3}"
        article_fn = (ep.get("article_filename") or "").strip()
        if article_fn:
            item["link_url"] = f"{clean_base}/deep/posts/{article_fn}"
        combined.append(item)

    combined.sort(key=lambda ep: ep.get("published_iso", ""), reverse=True)

    resolved_has_cover = has_cover if has_cover is not None else (pages_dir / "cover.jpg").exists()
    feed_xml = _render_rss_xml(
        episodes=combined,
        base_url=clean_base,
        has_cover=resolved_has_cover,
        owner_email=owner_email,
    )
    feed_path = pages_dir / "feed.xml"
    feed_path.parent.mkdir(parents=True, exist_ok=True)
    feed_path.write_text(feed_xml, encoding="utf-8")
    return feed_path
