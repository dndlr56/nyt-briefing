"""독립 팟캐스트 RSS(deep/feed.xml) + 전용 웹 리더 아티클 페이지(deep/posts/*.html) + 아카이브 인덱스(deep/index.html) 생성기."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import html
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from deep_dive.models import CurationDecision, DeepDiveScript, DeepDiveStudyNote, FetchedDocument
from deep_dive.themes import CORE_THEME_IDS, THEME_SLOTS, ThemeSlot


KST = timezone(timedelta(hours=9))
DEFAULT_DEEP_PODCAST_TITLE = "Deep Dive Knowledge Briefing"
DEFAULT_DEEP_PODCAST_DESCRIPTION = (
    "탑티어 학술 저널(Nature·Science·PMC OA·arXiv), NBER·BIS·Brookings 보고서, "
    "주요 대법원 판례와 장문 에세이를 원문 기반으로 깊이 해부하는 개인 심층 지식 브리핑입니다."
)
DEFAULT_DEEP_PODCAST_AUTHOR = "Deep Dive Knowledge Briefing"
DEFAULT_COVER_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "cover.jpg"
MAX_DEEP_EPISODES = 60


@dataclass
class PublishedDeepDive:
    feed_path: Path
    article_path: Path
    article_url: str
    feed_url: str
    mp3_url: str | None
    slug: str


def _format_itunes_duration(seconds: int) -> str:
    total = max(1, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _cdata(text: str) -> str:
    safe = text.replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{safe}]]>"


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9가-힣]+", "-", text).strip("-")
    return cleaned[:48] or "episode"


def load_covered_urls_from_pages(deep_dir: Path) -> set[str]:
    """기존 deep/episodes/metadata.json에서 이미 다룬 원문 URL 목록을 읽어 중복 선별을 방지합니다."""
    metadata_path = deep_dir / "episodes" / "metadata.json"
    if not metadata_path.exists():
        return set()
    try:
        loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            return set()
        urls: set[str] = set()
        for ep in loaded:
            for u in ep.get("source_urls", []):
                if isinstance(u, str) and u.strip():
                    urls.add(u.strip().rstrip("/"))
        return urls
    except (OSError, json.JSONDecodeError):
        return set()


def render_study_note_body_html(
    note: DeepDiveStudyNote,
    docs: list[FetchedDocument],
    theme: ThemeSlot,
    decision: CurationDecision,
    article_url: str | None = None,
    drive_doc_url: str | None = None,
    usage_footer_html: str | None = None,
) -> str:
    """웹 리더·Google Doc·팟캐스트 쇼노트에서 공통 활용 가능한 원문 충실 요약 HTML 본문을 생성합니다."""
    esc = html.escape

    tldr_items = "".join(f"<li>{esc(b)}</li>" for b in note.tldr_bullets)
    limits_items = "".join(f"<li>{esc(l)}</li>" for l in note.author_stated_limitations_kr)
    conclusions_items = "".join(f"<li>{esc(c)}</li>" for c in note.author_conclusions_kr)

    body_sections_html = "".join(
        "<div class='body-sec'>"
        f"<h3>{idx}. {esc(sec.heading_kr)}</h3>"
        f"<p>{esc(sec.content_kr)}</p>"
        "</div>"
        for idx, sec in enumerate(note.body_sections, start=1)
    )

    table_rows = "".join(
        f"<tr><th>{esc(row.item)}</th><td>{esc(row.detail_kr)}</td></tr>"
        for row in note.summary_table
    )

    quotes_html = "".join(
        "<blockquote class='quote-block'>"
        f"<p class='quote-en'>“{esc(q.quote_original)}”</p>"
        f"<p class='quote-kr'>{esc(q.translation_kr)}</p>"
        f"<footer>— {esc(q.source_label)}</footer>"
        "</blockquote>"
        for q in note.key_quotes
    )

    chapters_items = "".join(
        f"<li><strong>Ch.{ch.chapter_number}. {esc(ch.title_kr)}</strong>: {esc(ch.focus_kr)}</li>"
        for ch in note.chapters
    )

    sources_items = "".join(
        "<li>"
        f"<strong>{esc(d.candidate.title)}</strong> "
        f"({esc(d.candidate.source_name)}{', ' + esc(d.candidate.published) if d.candidate.published else ''}) — "
        f"<a href='{esc(d.candidate.url, quote=True)}' target='_blank' rel='noopener'>원문 보기</a>"
        + (
            f" · <a href='{esc(d.candidate.pdf_url, quote=True)}' target='_blank' rel='noopener'>PDF</a>"
            if d.candidate.pdf_url
            else ""
        )
        + "</li>"
        for d in docs
    )

    ext_links: list[str] = []
    if article_url:
        ext_links.append(f"<a href='{esc(article_url, quote=True)}'>📖 웹 리더에서 읽기</a>")
    if drive_doc_url:
        ext_links.append(f"<a href='{esc(drive_doc_url, quote=True)}'>📁 Google Drive 노트 열기</a>")
    ext_bar = f"<p class='ext-links'>{' · '.join(ext_links)}</p>" if ext_links else ""

    mode_badge = "1편 충실 요약" if decision.mode == "single_deep_dive" else f"{len(docs)}편 종합 요약"
    footer = f"\n{usage_footer_html}" if usage_footer_html else ""

    return f"""
<p class="badges">
  <span class="badge">{esc(theme.emoji)} {esc(theme.label_kr)}</span>
  <span class="badge secondary">{esc(decision.sub_subject_kr)}</span>
  <span class="badge outline">{esc(mode_badge)}</span>
</p>
<p class="subtitle">{esc(note.subtitle_kr)}</p>
{ext_bar}

<section class="tldr-box">
  <h2>⚡ 핵심 요약</h2>
  <ul>{tldr_items}</ul>
</section>

<section>
  <h2>1. 개요 및 문제의식</h2>
  <p>{esc(note.overview_and_purpose_kr)}</p>
</section>

<section>
  <h2>2. 연구 방법 및 근거 구조</h2>
  <p>{esc(note.methodology_and_evidence_kr)}</p>
</section>

<section>
  <h2>3. 본문 핵심 내용</h2>
  {body_sections_html}
</section>

<section>
  <h2>4. 주요 수치·내용 요약표</h2>
  <table class="analysis-table">
    <thead><tr><th>항목</th><th>원문 핵심 내용</th></tr></thead>
    <tbody>{table_rows}</tbody>
  </table>
</section>

<section>
  <h2>5. 원문 핵심 문장 발췌</h2>
  {quotes_html}
</section>

<section>
  <h2>6. 원문이 밝힌 한계 및 주의점</h2>
  <ul>{limits_items}</ul>
</section>

<section>
  <h2>7. 원문의 결론 및 제언</h2>
  <ul>{conclusions_items}</ul>
</section>

<section>
  <h2>8. 오디오 챕터 구성</h2>
  <ol>{chapters_items}</ol>
</section>

<section>
  <h2>📚 원문 출처</h2>
  <ul>{sources_items}</ul>
</section>
{footer}
""".strip()


def _format_dialogue_cast_line(script: DeepDiveScript) -> str:
    speakers_str = " · ".join(
        f"{sp.name_kr}({sp.role_title_kr})" for sp in script.cast.speakers
    )
    return f"{len(script.cast.speakers)}인 대화 · {speakers_str}"


def _render_article_page_html(
    episode_title: str,
    date_label: str,
    body_html: str,
    mp3_rel_path: str | None,
    duration_seconds: int,
    script: DeepDiveScript | None,
    feed_rel_path: str = "../feed.xml",
) -> str:
    esc = html.escape
    audio_block = ""
    if mp3_rel_path:
        dur_str = _format_itunes_duration(duration_seconds)
        cast_suffix = f" · {_format_dialogue_cast_line(script)}" if script is not None else ""
        audio_block = (
            "<div class='audio-card'>"
            f"<div class='audio-meta'>🎧 심층 오디오 브리핑{esc(cast_suffix)} · 재생 시간 {esc(dur_str)}</div>"
            f"<audio controls preload='metadata' src='{esc(mp3_rel_path, quote=True)}'></audio>"
            "</div>"
        )

    transcript_block = ""
    if script is not None:
        label_map = script.speaker_label_map()

        def _render_turns(turns: list[Any]) -> str:
            return "".join(
                f"<p class='turn'><strong class='speaker'>{esc(label_map.get(t.speaker_id, t.speaker_id))}</strong> "
                f"{esc(t.text)}</p>"
                for t in turns
                if getattr(t, "text", "").strip()
            )

        cast_intro = (
            "<div class='cast-box'>"
            f"<div><strong>진행 구성:</strong> {esc(_format_dialogue_cast_line(script))}</div>"
            f"<div class='cast-reason'>{esc(script.cast.format_reason_kr)}</div>"
            "</div>"
        )
        ch_html = [cast_intro, f"<h3>오프닝</h3>{_render_turns(script.opening_turns)}"]
        for idx, (ch_title, ch_turns) in enumerate(script.chapters, start=1):
            ch_html.append(f"<h3>Chapter {idx}. {esc(ch_title)}</h3>{_render_turns(ch_turns)}")
        ch_html.append(f"<h3>클로징</h3>{_render_turns(script.closing_turns)}")
        transcript_block = (
            "<details class='transcript'>"
            f"<summary>🎙️ 전체 대화 대본 펼쳐보기 ({script.total_chars:,}자 · {script.total_turns}턴)</summary>"
            f"<div class='transcript-body'>{''.join(ch_html)}</div>"
            "</details>"
        )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{esc(episode_title)} — Deep Dive Knowledge Briefing</title>
  <link rel="alternate" type="application/rss+xml" title="Deep Dive Knowledge Briefing" href="{esc(feed_rel_path, quote=True)}" />
  <style>
    :root {{
      --bg: #fafaf9; --card: #ffffff; --text: #1c1917; --muted: #57534e;
      --border: #e7e5e4; --accent: #1d4ed8; --accent-soft: #eff6ff;
      --quote-bg: #f5f5f4;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0c0a09; --card: #1c1917; --text: #f5f5f4; --muted: #a8a29e;
        --border: #292524; --accent: #60a5fa; --accent-soft: #172554;
        --quote-bg: #292524;
      }}
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Pretendard", "Apple SD Gothic Neo", "Segoe UI", sans-serif;
      background: var(--bg); color: var(--text); max-width: 820px;
      margin: 0 auto; padding: 2rem 1.25rem 4rem; line-height: 1.75;
    }}
    nav {{ margin-bottom: 1.5rem; font-size: 0.92rem; display: flex; justify-content: space-between; }}
    nav a {{ color: var(--accent); text-decoration: none; font-weight: 500; }}
    h1 {{ font-size: 1.85rem; line-height: 1.3; margin: 0.5rem 0 0.4rem; letter-spacing: -0.02em; }}
    .date-meta {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 1rem; }}
    .subtitle {{ font-size: 1.12rem; color: var(--muted); margin: 0.5rem 0 1.25rem; }}
    .badges {{ display: flex; flex-wrap: wrap; gap: 0.45rem; margin: 0.75rem 0; }}
    .badge {{ background: var(--accent-soft); color: var(--accent); padding: 0.2rem 0.65rem; border-radius: 999px; font-size: 0.82rem; font-weight: 600; }}
    .badge.secondary {{ background: var(--quote-bg); color: var(--text); }}
    .badge.outline {{ background: transparent; border: 1px solid var(--border); color: var(--muted); }}
    .audio-card {{
      background: var(--card); border: 1px solid var(--border); border-radius: 12px;
      padding: 1rem 1.2rem; margin: 1.25rem 0 1.75rem;
    }}
    .audio-meta {{ font-size: 0.9rem; font-weight: 600; margin-bottom: 0.55rem; }}
    audio {{ width: 100%; }}
    .tldr-box {{
      background: var(--accent-soft); border-left: 4px solid var(--accent);
      padding: 1rem 1.25rem; border-radius: 0 10px 10px 0; margin: 1.5rem 0;
    }}
    .tldr-box h2 {{ margin-top: 0; font-size: 1.1rem; }}
    section {{ margin: 2rem 0; }}
    h2 {{ font-size: 1.28rem; border-bottom: 1px solid var(--border); padding-bottom: 0.35rem; margin-bottom: 0.9rem; }}
    .body-sec {{ margin: 1.25rem 0; }}
    .body-sec h3 {{ font-size: 1.08rem; margin: 0 0 0.45rem; color: var(--text); }}
    .body-sec p {{ margin: 0; white-space: pre-line; }}
    .analysis-table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.95rem; }}
    .analysis-table th, .analysis-table td {{ border: 1px solid var(--border); padding: 0.7rem 0.85rem; text-align: left; vertical-align: top; }}
    .analysis-table th {{ background: var(--quote-bg); width: 26%; font-weight: 600; }}
    .quote-block {{
      background: var(--quote-bg); border-left: 3px solid var(--muted);
      margin: 1rem 0; padding: 0.9rem 1.15rem; border-radius: 0 8px 8px 0;
    }}
    .quote-en {{ font-style: italic; margin: 0 0 0.45rem; }}
    .quote-kr {{ margin: 0 0 0.35rem; }}
    .quote-block footer {{ font-size: 0.84rem; color: var(--muted); }}
    .transcript {{ margin-top: 2.5rem; border: 1px solid var(--border); border-radius: 10px; padding: 1rem 1.2rem; background: var(--card); }}
    .transcript summary {{ cursor: pointer; font-weight: 600; color: var(--accent); }}
    .transcript-body p {{ white-space: pre-line; }}
    .cast-box {{ background: var(--quote-bg); border-radius: 8px; padding: 0.75rem 1rem; margin: 0.9rem 0 1.25rem; font-size: 0.9rem; }}
    .cast-reason {{ color: var(--muted); font-size: 0.85rem; margin-top: 0.25rem; }}
    .turn {{ margin: 0.65rem 0; }}
    .speaker {{ color: var(--accent); margin-right: 0.35rem; }}
    a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <nav>
    <a href="../index.html">← Deep Dive 아카이브 목록</a>
    <a href="{esc(feed_rel_path, quote=True)}">RSS 구독 피드</a>
  </nav>
  <header>
    <div class="date-meta">{esc(date_label)}</div>
    <h1>{esc(episode_title)}</h1>
  </header>
  {audio_block}
  <main>
    {body_html}
    {transcript_block}
  </main>
</body>
</html>
"""


def _render_rss_xml(
    episodes: list[dict[str, Any]],
    base_url: str,
    has_cover: bool = True,
    owner_email: str | None = None,
) -> str:
    clean_base = base_url.rstrip("/")
    feed_url = f"{clean_base}/feed.xml"
    cover_url = f"{clean_base}/cover.jpg"
    last_build = format_datetime(datetime.now(KST))

    resolved_email = (owner_email or os.getenv("PODCAST_OWNER_EMAIL") or "").strip()
    owner_block = ""
    if resolved_email:
        owner_block = (
            "    <itunes:owner>\n"
            f"      <itunes:name>{xml_escape(DEFAULT_DEEP_PODCAST_AUTHOR)}</itunes:name>\n"
            f"      <itunes:email>{xml_escape(resolved_email)}</itunes:email>\n"
            "    </itunes:owner>\n"
        )

    cover_block = ""
    if has_cover:
        cover_block = (
            f"    <itunes:image href=\"{xml_escape(cover_url)}\"/>\n"
            "    <image>\n"
            f"      <url>{xml_escape(cover_url)}</url>\n"
            f"      <title>{xml_escape(DEFAULT_DEEP_PODCAST_TITLE)}</title>\n"
            f"      <link>{xml_escape(clean_base)}/</link>\n"
            "    </image>\n"
        )

    items_xml: list[str] = []
    for ep in episodes:
        if not ep.get("mp3_filename"):
            continue
        mp3_url = f"{clean_base}/episodes/{ep['mp3_filename']}"
        article_url = f"{clean_base}/posts/{ep['article_filename']}"
        title = xml_escape(ep["title"])
        guid = xml_escape(ep["guid"])
        pub_date = xml_escape(ep["pub_date_rfc2822"])
        duration = xml_escape(_format_itunes_duration(ep.get("duration_seconds", 1)))
        size_bytes = int(ep.get("file_size_bytes", 0))
        summary_plain = xml_escape(ep.get("summary_plain") or ep["title"])
        desc_html = ep.get("description_html") or f"<p>{title}</p>"

        items_xml.append(
            "    <item>\n"
            f"      <title>{title}</title>\n"
            f"      <link>{xml_escape(article_url)}</link>\n"
            f"      <guid isPermaLink=\"false\">{guid}</guid>\n"
            f"      <pubDate>{pub_date}</pubDate>\n"
            f"      <description>{_cdata(desc_html)}</description>\n"
            f"      <itunes:summary>{summary_plain}</itunes:summary>\n"
            f"      <itunes:duration>{duration}</itunes:duration>\n"
            f"      <itunes:explicit>false</itunes:explicit>\n"
            f"      <enclosure url=\"{xml_escape(mp3_url)}\" length=\"{size_bytes}\" type=\"audio/mpeg\"/>\n"
            "    </item>"
        )

    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<rss version=\"2.0\"\n"
        "     xmlns:itunes=\"http://www.itunes.com/dtds/podcast-1.0.dtd\"\n"
        "     xmlns:atom=\"http://www.w3.org/2005/Atom\">\n"
        "  <channel>\n"
        f"    <title>{xml_escape(DEFAULT_DEEP_PODCAST_TITLE)}</title>\n"
        f"    <link>{xml_escape(clean_base)}/</link>\n"
        f"    <atom:link href=\"{xml_escape(feed_url)}\" rel=\"self\" type=\"application/rss+xml\"/>\n"
        "    <language>ko-kr</language>\n"
        f"    <description>{xml_escape(DEFAULT_DEEP_PODCAST_DESCRIPTION)}</description>\n"
        f"    <lastBuildDate>{xml_escape(last_build)}</lastBuildDate>\n"
        f"    <itunes:author>{xml_escape(DEFAULT_DEEP_PODCAST_AUTHOR)}</itunes:author>\n"
        f"{owner_block}"
        f"    <itunes:summary>{xml_escape(DEFAULT_DEEP_PODCAST_DESCRIPTION)}</itunes:summary>\n"
        "    <itunes:explicit>false</itunes:explicit>\n"
        f"{cover_block}"
        "    <itunes:category text=\"Education\"/>\n"
        "    <itunes:category text=\"Science\"/>\n"
        f"{chr(10).join(items_xml)}\n"
        "  </channel>\n"
        "</rss>\n"
    )


def _render_archive_index_html(episodes: list[dict[str, Any]], base_url: str) -> str:
    esc = html.escape
    clean_base = base_url.rstrip("/")
    feed_url = f"{clean_base}/feed.xml"

    filter_buttons = ["<button class='pill active' data-theme='all'>전체</button>"]
    for tid in CORE_THEME_IDS:
        slot = THEME_SLOTS[tid]
        filter_buttons.append(
            f"<button class='pill' data-theme='{esc(tid)}'>{esc(slot.emoji)} {esc(slot.label_kr)}</button>"
        )

    cards: list[str] = []
    for ep in episodes:
        tid = ep.get("theme_id", "")
        theme_label = ep.get("theme_label", tid)
        sub_subject = ep.get("sub_subject_kr", "")
        article_href = f"posts/{esc(ep['article_filename'], quote=True)}"
        dur = _format_itunes_duration(ep.get("duration_seconds", 0)) if ep.get("mp3_filename") else "텍스트 전용"
        cast_summary = ep.get("dialogue_cast_summary", "")
        meta_parts = [ep.get("date_slug", ""), theme_label, sub_subject]
        if cast_summary:
            meta_parts.append(cast_summary)
        meta_parts.append(dur)
        meta_line = " · ".join(p for p in meta_parts if p)
        audio_tag = (
            f"<audio controls preload='none' src='episodes/{esc(ep['mp3_filename'], quote=True)}'></audio>"
            if ep.get("mp3_filename")
            else ""
        )
        cards.append(
            f"<article class='card' data-theme='{esc(tid)}'>"
            f"<div class='meta'>{esc(meta_line)}</div>"
            f"<h2><a href='{article_href}'>{esc(ep['title'])}</a></h2>"
            f"<p class='sub'>{esc(ep.get('subtitle_kr', ''))}</p>"
            f"{audio_tag}"
            f"<div class='actions'><a class='read-btn' href='{article_href}'>📖 구조화 독해 노트 열기 →</a></div>"
            "</article>"
        )

    cards_html = "\n".join(cards) if cards else "<p>아직 등록된 심층 브리핑이 없습니다.</p>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{esc(DEFAULT_DEEP_PODCAST_TITLE)}</title>
  <link rel="alternate" type="application/rss+xml" title="{esc(DEFAULT_DEEP_PODCAST_TITLE)}" href="feed.xml" />
  <style>
    :root {{
      --bg: #fafaf9; --card: #ffffff; --text: #1c1917; --muted: #57534e;
      --border: #e7e5e4; --accent: #1d4ed8; --accent-soft: #eff6ff;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0c0a09; --card: #1c1917; --text: #f5f5f4; --muted: #a8a29e;
        --border: #292524; --accent: #60a5fa; --accent-soft: #172554;
      }}
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Pretendard", "Segoe UI", sans-serif;
      background: var(--bg); color: var(--text); max-width: 860px;
      margin: 0 auto; padding: 2rem 1.25rem 4rem; line-height: 1.65;
    }}
    h1 {{ margin: 0 0 0.4rem; font-size: 1.9rem; }}
    .desc {{ color: var(--muted); margin: 0 0 1.25rem; }}
    .rss-box {{ background: var(--card); border: 1px solid var(--border); padding: 0.9rem 1.1rem; border-radius: 10px; margin-bottom: 1.5rem; word-break: break-all; font-size: 0.92rem; }}
    .filters {{ display: flex; flex-wrap: wrap; gap: 0.45rem; margin-bottom: 1.5rem; }}
    .pill {{ border: 1px solid var(--border); background: var(--card); color: var(--text); padding: 0.35rem 0.8rem; border-radius: 999px; cursor: pointer; font-size: 0.86rem; }}
    .pill.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
    .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 1.25rem 1.35rem; margin-bottom: 1rem; }}
    .card.hidden {{ display: none; }}
    .meta {{ font-size: 0.84rem; color: var(--muted); margin-bottom: 0.35rem; }}
    .card h2 {{ margin: 0 0 0.4rem; font-size: 1.2rem; }}
    .card h2 a {{ color: var(--text); text-decoration: none; }}
    .card h2 a:hover {{ color: var(--accent); }}
    .sub {{ color: var(--muted); margin: 0 0 0.9rem; font-size: 0.95rem; }}
    audio {{ width: 100%; margin-bottom: 0.75rem; }}
    .read-btn {{ color: var(--accent); font-weight: 600; text-decoration: none; font-size: 0.93rem; }}
    a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <h1>📘 {esc(DEFAULT_DEEP_PODCAST_TITLE)}</h1>
  <p class="desc">{esc(DEFAULT_DEEP_PODCAST_DESCRIPTION)}</p>
  <div class="rss-box">
    <strong>🎧 AntennaPod 독립 구독 RSS:</strong>
    <a href="{esc(feed_url, quote=True)}">{esc(feed_url)}</a>
    &nbsp;·&nbsp;
    <a href="../index.html">🗞 글로벌 모닝 뉴스 브리핑으로 이동</a>
  </div>
  <div class="filters">{''.join(filter_buttons)}</div>
  <section id="cards">{cards_html}</section>
  <script>
    const pills = document.querySelectorAll('.pill');
    const cards = document.querySelectorAll('.card');
    pills.forEach(btn => btn.addEventListener('click', () => {{
      pills.forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const t = btn.dataset.theme;
      cards.forEach(c => c.classList.toggle('hidden', t !== 'all' && c.dataset.theme !== t));
    }}));
  </script>
</body>
</html>
"""


def publish_deep_dive(
    deep_dir: Path,
    base_url: str,
    date_slug: str,
    theme: ThemeSlot,
    decision: CurationDecision,
    note: DeepDiveStudyNote,
    docs: list[FetchedDocument],
    script: DeepDiveScript | None = None,
    mp3_path: Path | None = None,
    duration_seconds: int = 0,
    drive_doc_url: str | None = None,
    usage_footer_html: str | None = None,
    max_episodes: int = MAX_DEEP_EPISODES,
) -> PublishedDeepDive:
    """웹 리더 페이지(posts/*.html), 독립 RSS(feed.xml), 아카이브(index.html)를 갱신합니다."""
    episodes_dir = deep_dir / "episodes"
    posts_dir = deep_dir / "posts"
    episodes_dir.mkdir(parents=True, exist_ok=True)
    posts_dir.mkdir(parents=True, exist_ok=True)

    now_kst = datetime.now(KST)
    time_slug = now_kst.strftime("%H%M%S")
    title_slug = _slugify(note.episode_title_kr)
    slug = f"{date_slug}_{theme.theme_id}_{title_slug}"
    guid = f"deep-dive-{date_slug}-{theme.theme_id}-{time_slug}"

    episode_title = (
        script.episode_title
        if script is not None
        else f"[{theme.label_kr}] {note.episode_title_kr} | {date_slug.replace('-', '.')}"
    )

    clean_base = base_url.rstrip("/")
    article_filename = f"{slug}.html"
    article_url = f"{clean_base}/posts/{article_filename}"

    mp3_filename = ""
    mp3_size = 0
    mp3_url: str | None = None
    if mp3_path is not None and mp3_path.exists():
        mp3_filename = f"deep_dive_{date_slug}_{theme.theme_id}_{time_slug}.mp3"
        target_mp3 = episodes_dir / mp3_filename
        if mp3_path.resolve() != target_mp3.resolve():
            shutil.copy2(mp3_path, target_mp3)
        mp3_size = target_mp3.stat().st_size
        mp3_url = f"{clean_base}/episodes/{mp3_filename}"

    body_html = render_study_note_body_html(
        note=note,
        docs=docs,
        theme=theme,
        decision=decision,
        article_url=article_url,
        drive_doc_url=drive_doc_url,
        usage_footer_html=usage_footer_html,
    )

    article_html = _render_article_page_html(
        episode_title=episode_title,
        date_label=now_kst.strftime("%Y년 %m월 %d일 (%a) KST"),
        body_html=body_html,
        mp3_rel_path=f"../episodes/{mp3_filename}" if mp3_filename else None,
        duration_seconds=duration_seconds,
        script=script,
    )
    article_path = posts_dir / article_filename
    article_path.write_text(article_html, encoding="utf-8")

    metadata_path = episodes_dir / "metadata.json"
    episodes: list[dict[str, Any]] = []
    if metadata_path.exists():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                episodes = loaded
        except (OSError, json.JSONDecodeError):
            episodes = []

    new_source_urls_norm = {
        d.candidate.url.strip().rstrip("/") for d in docs if d.candidate.url.strip()
    }
    new_entry: dict[str, Any] = {
        "guid": guid,
        "date_slug": date_slug,
        "theme_id": theme.theme_id,
        "theme_label": f"{theme.emoji} {theme.label_kr}",
        "sub_subject_kr": decision.sub_subject_kr,
        "title": episode_title,
        "subtitle_kr": note.subtitle_kr,
        "dialogue_cast_summary": (
            f"{len(script.cast.speakers)}인 대화" if script is not None else ""
        ),
        "pub_date_rfc2822": format_datetime(now_kst),
        "published_iso": now_kst.isoformat(),
        "article_filename": article_filename,
        "mp3_filename": mp3_filename,
        "file_size_bytes": mp3_size,
        "duration_seconds": int(duration_seconds),
        "source_urls": [d.candidate.url for d in docs],
        "summary_plain": f"{note.subtitle_kr} — " + " / ".join(note.tldr_bullets[:2]),
        "description_html": body_html,
    }

    def _is_replaced_entry(ep: dict[str, Any]) -> bool:
        if ep.get("date_slug") == date_slug and ep.get("theme_id") == theme.theme_id:
            return True
        if new_source_urls_norm:
            ep_urls = {
                u.strip().rstrip("/")
                for u in ep.get("source_urls", [])
                if isinstance(u, str) and u.strip()
            }
            if ep_urls and ep_urls == new_source_urls_norm:
                return True
        return False

    # 같은 날짜·같은 테마 또는 동일 원문(예: weekend_special -> 핵심 테마 재분류) 재실행 시 기존 항목 교체
    replaced = [ep for ep in episodes if _is_replaced_entry(ep)]
    for old_ep in replaced:
        old_mp3 = old_ep.get("mp3_filename")
        if old_mp3 and old_mp3 != mp3_filename:
            (episodes_dir / old_mp3).unlink(missing_ok=True)
        old_art = old_ep.get("article_filename")
        if old_art and old_art != article_filename:
            (posts_dir / old_art).unlink(missing_ok=True)

    episodes = [ep for ep in episodes if not _is_replaced_entry(ep)]
    episodes.insert(0, new_entry)
    episodes.sort(key=lambda ep: ep.get("published_iso", ""), reverse=True)

    if len(episodes) > max_episodes:
        removed = episodes[max_episodes:]
        episodes = episodes[:max_episodes]
        for old_ep in removed:
            if old_ep.get("mp3_filename"):
                (episodes_dir / old_ep["mp3_filename"]).unlink(missing_ok=True)

    metadata_path.write_text(
        json.dumps(episodes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    has_cover = False
    target_cover = deep_dir / "cover.jpg"
    if DEFAULT_COVER_PATH.exists():
        if DEFAULT_COVER_PATH.resolve() != target_cover.resolve():
            shutil.copy2(DEFAULT_COVER_PATH, target_cover)
        has_cover = True
    elif target_cover.exists():
        has_cover = True

    feed_xml = _render_rss_xml(episodes=episodes, base_url=clean_base, has_cover=has_cover)
    feed_path = deep_dir / "feed.xml"
    feed_path.write_text(feed_xml, encoding="utf-8")

    index_html = _render_archive_index_html(episodes=episodes, base_url=clean_base)
    (deep_dir / "index.html").write_text(index_html, encoding="utf-8")

    return PublishedDeepDive(
        feed_path=feed_path,
        article_path=article_path,
        article_url=article_url,
        feed_url=f"{clean_base}/feed.xml",
        mp3_url=mp3_url,
        slug=slug,
    )
