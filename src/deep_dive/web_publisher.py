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

from deep_dive.models import (
    CurationDecision,
    DeepDiveScript,
    DeepDiveStudyNote,
    FetchedDocument,
    resolve_section_headings,
)
from deep_dive.themes import CORE_THEME_IDS, THEME_SLOTS, ThemeSlot


KST = timezone(timedelta(hours=9))
DEFAULT_DEEP_PODCAST_TITLE = "Deep Dive Knowledge Briefing"
DEFAULT_DEEP_PODCAST_DESCRIPTION = (
    "학술 저널(Nature·Science·PMC OA·arXiv), 정책·경제 연구소(NBER·BIS·Brookings), "
    "주요 대법원 판례와 심층 에세이를 원문 근거 중심으로 해부하는 일간 연구 브리핑입니다."
)
DEFAULT_DEEP_PODCAST_AUTHOR = "Deep Dive Knowledge Briefing"
DEFAULT_COVER_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "cover.jpg"
MAX_DEEP_EPISODES = 60

SHARED_FONTS_HEAD = (
    '<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin />'
    '<link rel="preconnect" href="https://fonts.googleapis.com" />'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />'
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css" />'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@400;600&display=swap" />'
)

THEME_INIT_SCRIPT = """<script>
(function(){
  try {
    var saved = localStorage.getItem('briefing-color-mode');
    if (saved === 'dark' || saved === 'light') {
      document.documentElement.setAttribute('data-color-mode', saved);
    }
  } catch (e) {}
})();
</script>"""


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
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").lower()).strip("-")
    return cleaned[:42].strip("-")


def _ascii_post_slug(date_slug: str, theme_id: str, docs: list[FetchedDocument]) -> str:
    """YouTube 팟캐스트·RSS 리다이렉트에서 절대 깨지지 않는 순수 ASCII 파일 슬러그를 생성합니다."""
    title_slug = ""
    if docs and docs[0].candidate.title:
        title_slug = _slugify(docs[0].candidate.title)
    if title_slug:
        return f"{date_slug}_{theme_id}_{title_slug}"
    return f"{date_slug}_{theme_id}"


def _render_paragraphs_html(text: str) -> str:
    """줄바꿈(\\n\\n 또는 \\n)으로 나뉜 텍스트를 깔끔한 다중 <p> 문단으로 렌더링합니다."""
    raw = (text or "").strip()
    if not raw:
        return ""
    blocks = [b.strip() for b in re.split(r"\n{2,}", raw) if b.strip()]
    if not blocks:
        return ""
    return "".join(f"<p>{html.escape(b)}</p>" for b in blocks)


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
    archive_url: str | None = None,
    drive_doc_url: str | None = None,  # 공개 쇼노트·웹 리더에는 비공개 Drive 링크를 노출하지 않음
    usage_footer_html: str | None = None,
    plain_url_links: bool = False,
) -> str:
    """웹 리더·팟캐스트 쇼노트에서 공통 활용 가능한 원문 기반 연구 노트 HTML 본문을 생성합니다."""
    del drive_doc_url
    esc = html.escape
    doc_lens = getattr(note, "doc_lens", "empirical_or_review") or "empirical_or_review"
    headings = resolve_section_headings(doc_lens)

    tldr_items = "".join(f"<li>{esc(b)}</li>" for b in note.tldr_bullets)
    limits_items = "".join(f"<li>{esc(l)}</li>" for l in note.author_stated_limitations_kr)
    conclusions_items = "".join(f"<li>{esc(c)}</li>" for c in note.author_conclusions_kr)

    body_sections_html = "".join(
        "<div class='body-sec'>"
        f"<h3><span class='sec-idx'>{idx}.</span> {esc(sec.heading_kr)}</h3>"
        f"{_render_paragraphs_html(sec.content_kr)}"
        "</div>"
        for idx, sec in enumerate(note.body_sections, start=1)
    )

    table_rows = "".join(
        f"<tr><th scope='row'>{esc(row.item)}</th><td>{esc(row.detail_kr)}</td></tr>"
        for row in note.summary_table
    )

    quotes_html = "".join(
        "<blockquote class='quote-block'>"
        f"<p class='quote-en'>“{esc(q.quote_original)}”</p>"
        f"<p class='quote-kr'>{esc(q.translation_kr)}</p>"
        f"<footer>{esc(q.source_label)}</footer>"
        "</blockquote>"
        for q in note.key_quotes
    )

    chapters_items = "".join(
        f"<li><strong>Ch.{ch.chapter_number}. {esc(ch.title_kr)}</strong> — {esc(ch.focus_kr)}</li>"
        for ch in note.chapters
    )

    sources_items = "".join(
        "<li>"
        f"<strong>{esc(d.candidate.title)}</strong> "
        f"<span class='src-venue'>({esc(d.candidate.source_name)}{', ' + esc(d.candidate.published) if d.candidate.published else ''})</span> "
        f"<a href='{esc(d.candidate.url, quote=True)}' target='_blank' rel='noopener'>원문 보기</a>"
        + (
            f" <span class='sep'>/</span> <a href='{esc(d.candidate.pdf_url, quote=True)}' target='_blank' rel='noopener'>PDF 원문</a>"
            if d.candidate.pdf_url
            else ""
        )
        + "</li>"
        for d in docs
    )

    ext_links: list[str] = []
    if article_url:
        if plain_url_links:
            ext_links.append(
                f"웹 리더에서 읽기: <a href='{esc(article_url, quote=True)}'>{esc(article_url)}</a>"
            )
        else:
            ext_links.append(f"<a href='{esc(article_url, quote=True)}'>웹 리더에서 읽기</a>")
    if archive_url:
        if plain_url_links:
            ext_links.append(
                f"전체 아카이브: <a href='{esc(archive_url, quote=True)}'>{esc(archive_url)}</a>"
            )
        else:
            ext_links.append(f"<a href='{esc(archive_url, quote=True)}'>전체 아카이브</a>")
    if plain_url_links and ext_links:
        ext_bar = f"<p class='ext-links'>{'<br/>'.join(ext_links)}</p>"
    elif ext_links:
        ext_bar = f"<p class='ext-links'>{' / '.join(ext_links)}</p>"
    else:
        ext_bar = ""

    mode_badge = "1편 심층 분석" if decision.mode == "single_deep_dive" else f"{len(docs)}편 종합 분석"
    footer = f"\n<div class='usage-meta'>{usage_footer_html}</div>" if usage_footer_html else ""

    return f"""
<div class="meta-tags">
  <span class="tag primary">{esc(theme.emoji)} {esc(theme.label_kr)}</span>
  <span class="tag">{esc(decision.sub_subject_kr)}</span>
  <span class="tag muted">{esc(mode_badge)}</span>
</div>
<p class="subtitle">{esc(note.subtitle_kr)}</p>
{ext_bar}

<section id="sec-tldr" class="tldr-box" data-toc="핵심 요약">
  <h2>핵심 요약</h2>
  <ul>{tldr_items}</ul>
</section>

<section id="sec-1" data-toc="{esc(headings['sec1'])}">
  <h2>{esc(headings['sec1'])}</h2>
  {_render_paragraphs_html(note.overview_and_purpose_kr)}
</section>

<section id="sec-2" data-toc="{esc(headings['sec2'])}">
  <h2>{esc(headings['sec2'])}</h2>
  {_render_paragraphs_html(note.methodology_and_evidence_kr)}
</section>

<section id="sec-3" data-toc="{esc(headings['sec3'])}">
  <h2>{esc(headings['sec3'])}</h2>
  {body_sections_html}
</section>

<section id="sec-4" data-toc="{esc(headings['sec4'])}">
  <h2>{esc(headings['sec4'])}</h2>
  <div class="table-wrap">
    <table class="analysis-table">
      <thead><tr><th scope="col">항목</th><th scope="col">원문 핵심 내용</th></tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
  </div>
</section>

<section id="sec-5" data-toc="{esc(headings['sec5'])}">
  <h2>{esc(headings['sec5'])}</h2>
  {quotes_html}
</section>

<section id="sec-6" data-toc="{esc(headings['sec6'])}">
  <h2>{esc(headings['sec6'])}</h2>
  <ul>{limits_items}</ul>
</section>

<section id="sec-7" data-toc="{esc(headings['sec7'])}">
  <h2>{esc(headings['sec7'])}</h2>
  <ul>{conclusions_items}</ul>
</section>

<section id="sec-8" data-toc="8. 오디오 챕터 구성">
  <h2>8. 오디오 챕터 구성</h2>
  <ol class="chapter-list">{chapters_items}</ol>
</section>

<section id="sec-sources" data-toc="원문 출처">
  <h2>원문 출처</h2>
  <ul class="source-list">{sources_items}</ul>
</section>
{footer}
""".strip()


def _format_dialogue_cast_line(script: DeepDiveScript) -> str:
    speakers_str = ", ".join(
        f"{sp.name_kr}({sp.role_title_kr})" for sp in script.cast.speakers
    )
    return f"{len(script.cast.speakers)}인 대화 ({speakers_str})"


SHARED_EDITORIAL_CSS = """
    :root {
      --surface-page: #F5F7FA;
      --surface-sheet: #FFFFFF;
      --surface-subtle: #EDF1F6;
      --ink-primary: #0F172A;
      --ink-secondary: #334155;
      --ink-muted: #64748B;
      --accent: #1E40AF;
      --accent-hover: #1E3A8A;
      --accent-soft: #EBF2FF;
      --speaker-alt: #0F766E;
      --border: #D8E0EA;
      --border-strong: #BAC6D6;
      --font-sans: "Pretendard Variable", Pretendard, -apple-system, BlinkMacSystemFont, "Noto Sans KR", "Malgun Gothic", sans-serif;
      --font-serif: "Noto Serif KR", Georgia, serif;
    }
    @media (prefers-color-scheme: dark) {
      :root:not([data-color-mode="light"]) {
        --surface-page: #0B1018;
        --surface-sheet: #121A26;
        --surface-subtle: #1A2434;
        --ink-primary: #E6EDF6;
        --ink-secondary: #B4C2D4;
        --ink-muted: #7C8FA6;
        --accent: #7FB3FF;
        --accent-hover: #A5C8FF;
        --accent-soft: #172742;
        --speaker-alt: #5EEAD4;
        --border: #233044;
        --border-strong: #33455F;
      }
    }
    :root[data-color-mode="dark"] {
      --surface-page: #0B1018;
      --surface-sheet: #121A26;
      --surface-subtle: #1A2434;
      --ink-primary: #E6EDF6;
      --ink-secondary: #B4C2D4;
      --ink-muted: #7C8FA6;
      --accent: #7FB3FF;
      --accent-hover: #A5C8FF;
      --accent-soft: #172742;
      --speaker-alt: #5EEAD4;
      --border: #233044;
      --border-strong: #33455F;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    @media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
    body {
      margin: 0;
      font-family: var(--font-sans);
      background: var(--surface-page);
      color: var(--ink-primary);
      line-height: 1.78;
      word-break: keep-all;
      overflow-wrap: break-word;
      -webkit-font-smoothing: antialiased;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { color: var(--accent-hover); text-decoration: underline; }
    a:focus-visible, button:focus-visible, input:focus-visible, summary:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }
    .masthead {
      border-bottom: 1px solid var(--border);
      background: var(--surface-sheet);
    }
    .masthead-inner {
      max-width: 1080px;
      margin: 0 auto;
      padding: 0.85rem 1.25rem;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
    }
    .channel-tabs {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      background: var(--surface-subtle);
      padding: 0.25rem;
      border-radius: 8px;
      border: 1px solid var(--border);
    }
    .channel-tab {
      padding: 0.38rem 0.8rem;
      border-radius: 6px;
      font-size: 0.86rem;
      font-weight: 600;
      color: var(--ink-secondary);
      text-decoration: none;
    }
    .channel-tab:hover { color: var(--ink-primary); text-decoration: none; }
    .channel-tab.active {
      background: var(--surface-sheet);
      color: var(--accent);
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
    }
    .masthead-tools {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .tool-btn {
      border: 1px solid var(--border);
      background: var(--surface-sheet);
      color: var(--ink-secondary);
      font-family: inherit;
      font-size: 0.82rem;
      font-weight: 500;
      padding: 0.36rem 0.72rem;
      border-radius: 6px;
      cursor: pointer;
    }
    .tool-btn:hover { border-color: var(--border-strong); color: var(--ink-primary); }
"""


SHARED_UI_SCRIPT = """<script>
(function(){
  var modeBtn = document.getElementById('color-mode-btn');
  if (modeBtn) {
    modeBtn.addEventListener('click', function(){
      var root = document.documentElement;
      var current = root.getAttribute('data-color-mode');
      var isDark = current ? current === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
      var next = isDark ? 'light' : 'dark';
      root.setAttribute('data-color-mode', next);
      try { localStorage.setItem('briefing-color-mode', next); } catch (e) {}
    });
  }
  document.querySelectorAll('[data-copy-rss]').forEach(function(btn){
    btn.addEventListener('click', function(){
      var url = btn.getAttribute('data-copy-rss');
      if (!url || !navigator.clipboard) return;
      var orig = btn.textContent;
      navigator.clipboard.writeText(url).then(function(){
        btn.textContent = 'RSS 주소 복사됨';
        setTimeout(function(){ btn.textContent = orig; }, 1800);
      });
    });
  });
  document.querySelectorAll('[data-speed]').forEach(function(btn){
    btn.addEventListener('click', function(){
      var rate = parseFloat(btn.getAttribute('data-speed') || '1');
      var container = btn.closest('.audio-deck') || document;
      var audio = container.querySelector('audio');
      if (audio) audio.playbackRate = rate;
      container.querySelectorAll('[data-speed]').forEach(function(b){
        b.classList.toggle('active', b === btn);
      });
    });
  });
})();
</script>"""


def render_article_page_from_parts(
    episode_title: str,
    date_label: str,
    body_html: str,
    mp3_rel_path: str | None = None,
    duration_seconds: int = 0,
    cast_line: str = "",
    transcript_html: str = "",
    feed_rel_path: str = "../feed.xml",
    rss_copy_url: str = "",
) -> str:
    """새 에피소드 발행 및 과거 gh-pages HTML 소급 업그레이드에서 공통으로 쓰는 아티클 페이지 렌더러."""
    esc = html.escape
    display_h1 = re.sub(r"^\[[^\]]+\]\s*", "", episode_title)
    display_h1 = re.sub(r"\s*\|\s*\d{4}\.\d{2}\.\d{2}$", "", display_h1).strip() or episode_title
    audio_block = ""
    if mp3_rel_path:
        dur_str = _format_itunes_duration(duration_seconds)
        cast_part = f" / {cast_line}" if cast_line else ""
        audio_block = f"""
        <div class="audio-deck">
          <div class="audio-deck-top">
            <span class="audio-title">오디오 브리핑{esc(cast_part)} ({esc(dur_str)})</span>
            <div class="speed-group" role="group" aria-label="재생 배속">
              <button type="button" class="speed-btn active" data-speed="1">1.0x</button>
              <button type="button" class="speed-btn" data-speed="1.25">1.25x</button>
              <button type="button" class="speed-btn" data-speed="1.5">1.5x</button>
              <button type="button" class="speed-btn" data-speed="1.75">1.75x</button>
            </div>
          </div>
          <audio controls preload="metadata" src="{esc(mp3_rel_path, quote=True)}"></audio>
        </div>
        """.strip()

    copy_btn_html = (
        f'<button type="button" class="tool-btn" data-copy-rss="{esc(rss_copy_url, quote=True)}">RSS 주소 복사</button>'
        if rss_copy_url
        else f'<a class="tool-btn" href="{esc(feed_rel_path, quote=True)}">RSS XML</a>'
    )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{esc(episode_title)} — Deep Dive Knowledge Briefing</title>
  <link rel="alternate" type="application/rss+xml" title="Deep Dive Knowledge Briefing" href="{esc(feed_rel_path, quote=True)}" />
  {SHARED_FONTS_HEAD}
  {THEME_INIT_SCRIPT}
  <style>
    {SHARED_EDITORIAL_CSS}
    .progress-track {{
      position: fixed; top: 0; left: 0; right: 0; height: 3px;
      background: transparent; z-index: 40; pointer-events: none;
    }}
    .progress-bar {{
      height: 100%; width: 0%; background: var(--accent);
      transition: width 80ms linear;
    }}
    .reader-shell {{
      max-width: 1080px;
      margin: 0 auto;
      padding: 2rem 1.25rem 4.5rem;
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 2.25rem;
    }}
    @media (min-width: 1040px) {{
      .reader-shell {{
        grid-template-columns: 210px minmax(0, 740px);
        justify-content: space-between;
        align-items: start;
      }}
    }}
    .toc-rail {{
      display: none;
    }}
    @media (min-width: 1040px) {{
      .toc-rail {{
        display: block;
        position: sticky;
        top: 1.5rem;
        padding-right: 0.75rem;
        border-right: 1px solid var(--border);
      }}
    }}
    .toc-heading {{
      font-size: 0.78rem;
      font-weight: 700;
      color: var(--ink-muted);
      margin: 0 0 0.65rem;
    }}
    .toc-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }}
    .toc-link {{
      display: block;
      font-size: 0.83rem;
      line-height: 1.4;
      color: var(--ink-muted);
      padding: 0.25rem 0.45rem;
      border-radius: 5px;
      text-decoration: none;
    }}
    .toc-link:hover {{ color: var(--ink-primary); text-decoration: none; background: var(--surface-subtle); }}
    .toc-link.active {{ color: var(--accent); font-weight: 600; background: var(--accent-soft); }}
    .back-link {{
      display: inline-block;
      margin-bottom: 1rem;
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--ink-muted);
    }}
    .paper-sheet {{
      background: var(--surface-sheet);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 2.25rem 2.25rem 2.75rem;
    }}
    @media (max-width: 640px) {{
      .paper-sheet {{ padding: 1.4rem 1.15rem 2rem; border-radius: 10px; }}
    }}
    .date-meta {{
      font-size: 0.85rem;
      font-variant-numeric: tabular-nums;
      color: var(--ink-muted);
      margin-bottom: 0.55rem;
    }}
    h1 {{
      font-size: clamp(1.5rem, 2.8vw, 1.95rem);
      line-height: 1.32;
      letter-spacing: -0.025em;
      margin: 0 0 0.9rem;
      color: var(--ink-primary);
    }}
    .meta-tags, .badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      margin: 0.75rem 0 0.9rem;
    }}
    .tag, .badge {{
      display: inline-flex;
      align-items: center;
      font-size: 0.8rem;
      font-weight: 600;
      padding: 0.22rem 0.62rem;
      border-radius: 6px;
      background: var(--surface-subtle);
      color: var(--ink-secondary);
      border: 1px solid var(--border);
    }}
    .tag.primary, .badge:first-child {{
      background: var(--accent-soft);
      color: var(--accent);
      border-color: transparent;
    }}
    .tag.muted, .badge.outline {{
      background: transparent;
      color: var(--ink-muted);
    }}
    .subtitle {{
      font-family: var(--font-serif);
      font-size: 1.06rem;
      line-height: 1.7;
      color: var(--ink-secondary);
      margin: 0.6rem 0 1.4rem;
    }}
    .audio-deck {{
      background: var(--surface-subtle);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.9rem 1.05rem;
      margin: 1.25rem 0 1.75rem;
    }}
    .audio-deck-top {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 0.5rem;
      margin-bottom: 0.6rem;
    }}
    .audio-title {{
      font-size: 0.86rem;
      font-weight: 600;
      color: var(--ink-primary);
    }}
    .speed-group {{ display: inline-flex; gap: 0.25rem; }}
    .speed-btn {{
      border: 1px solid var(--border);
      background: var(--surface-sheet);
      color: var(--ink-muted);
      font-family: inherit;
      font-size: 0.75rem;
      font-weight: 600;
      padding: 0.18rem 0.48rem;
      border-radius: 5px;
      cursor: pointer;
    }}
    .speed-btn.active {{
      background: var(--accent);
      border-color: var(--accent);
      color: #FFFFFF;
    }}
    @media (prefers-color-scheme: dark) {{
      :root:not([data-color-mode="light"]) .speed-btn.active {{ color: #0B1018; }}
    }}
    :root[data-color-mode="dark"] .speed-btn.active {{ color: #0B1018; }}
    audio {{ width: 100%; height: 38px; display: block; }}
    main {{ max-width: 68ch; }}
    .tldr-box {{
      background: var(--accent-soft);
      border-left: 3px solid var(--accent);
      padding: 1.05rem 1.25rem;
      border-radius: 0 8px 8px 0;
      margin: 1.5rem 0 2rem;
    }}
    .tldr-box h2 {{
      margin: 0 0 0.55rem;
      padding: 0;
      border: none;
      font-size: 1.02rem;
      color: var(--accent);
    }}
    .tldr-box ul {{ margin: 0; padding-left: 1.2rem; }}
    .tldr-box li {{ margin-bottom: 0.4rem; }}
    .tldr-box li:last-child {{ margin-bottom: 0; }}
    section {{ margin: 2.15rem 0; scroll-margin-top: 1.2rem; }}
    section > h2 {{
      font-size: 1.2rem;
      letter-spacing: -0.015em;
      border-bottom: 1px solid var(--border);
      padding-bottom: 0.4rem;
      margin: 0 0 0.95rem;
      color: var(--ink-primary);
    }}
    section p {{ margin: 0 0 0.9rem; color: var(--ink-primary); }}
    section p:last-child {{ margin-bottom: 0; }}
    .body-sec {{
      margin: 1.4rem 0;
      padding-left: 0.85rem;
      border-left: 2px solid var(--border);
    }}
    .body-sec h3 {{
      font-size: 1.04rem;
      margin: 0 0 0.5rem;
      color: var(--ink-primary);
    }}
    .sec-idx {{ color: var(--accent); margin-right: 0.2rem; }}
    .table-wrap {{ overflow-x: auto; margin: 1rem 0; }}
    .analysis-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
      border-top: 2px solid var(--border-strong);
      border-bottom: 2px solid var(--border-strong);
    }}
    .analysis-table th, .analysis-table td {{
      border-bottom: 1px solid var(--border);
      padding: 0.7rem 0.85rem;
      text-align: left;
      vertical-align: top;
    }}
    .analysis-table thead th {{
      background: var(--surface-subtle);
      font-weight: 700;
      color: var(--ink-primary);
    }}
    .analysis-table tbody th {{
      width: 27%;
      font-weight: 600;
      color: var(--ink-primary);
      background: transparent;
    }}
    .quote-block {{
      background: var(--surface-subtle);
      border-left: 3px solid var(--border-strong);
      margin: 1.1rem 0;
      padding: 1rem 1.2rem;
      border-radius: 0 8px 8px 0;
    }}
    .quote-en {{
      font-family: var(--font-serif);
      font-size: 0.96rem;
      line-height: 1.68;
      margin: 0 0 0.5rem;
      color: var(--ink-primary);
    }}
    .quote-kr {{
      font-size: 0.94rem;
      margin: 0 0 0.4rem;
      color: var(--ink-secondary);
    }}
    .quote-block footer {{
      font-size: 0.81rem;
      color: var(--ink-muted);
    }}
    .chapter-list, .source-list {{ padding-left: 1.25rem; }}
    .chapter-list li, .source-list li {{ margin-bottom: 0.45rem; }}
    .src-venue, .sep {{ color: var(--ink-muted); }}
    .usage-meta {{
      margin-top: 2rem;
      padding-top: 0.9rem;
      border-top: 1px dashed var(--border);
      font-size: 0.82rem;
      color: var(--ink-muted);
    }}
    .transcript {{
      margin-top: 2.5rem;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--surface-subtle);
      overflow: hidden;
    }}
    .transcript summary {{
      cursor: pointer;
      padding: 0.95rem 1.15rem;
      font-weight: 600;
      font-size: 0.95rem;
      color: var(--ink-primary);
      list-style: none;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .transcript summary::-webkit-details-marker {{ display: none; }}
    .transcript summary::after {{
      content: "펼치기";
      font-size: 0.8rem;
      color: var(--accent);
      font-weight: 600;
    }}
    .transcript[open] summary::after {{ content: "접기"; }}
    .transcript-body {{
      padding: 0.25rem 1.2rem 1.35rem;
      border-top: 1px solid var(--border);
      background: var(--surface-sheet);
    }}
    .transcript-filter {{
      width: 100%;
      margin: 0.9rem 0 1rem;
      padding: 0.5rem 0.75rem;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface-page);
      color: var(--ink-primary);
      font-family: inherit;
      font-size: 0.86rem;
    }}
    .cast-box {{
      background: var(--surface-subtle);
      border-radius: 8px;
      padding: 0.75rem 1rem;
      margin: 0.85rem 0 1.2rem;
      font-size: 0.88rem;
    }}
    .cast-reason {{ color: var(--ink-muted); font-size: 0.83rem; margin-top: 0.2rem; }}
    .transcript-body h3 {{
      font-size: 1rem;
      margin: 1.4rem 0 0.6rem;
      padding-bottom: 0.25rem;
      border-bottom: 1px solid var(--border);
    }}
    .turn {{ margin: 0.7rem 0; line-height: 1.74; font-size: 0.94rem; }}
    .turn.hidden {{ display: none; }}
    .speaker {{
      display: inline-block;
      font-size: 0.82rem;
      font-weight: 700;
      color: var(--accent);
      margin-right: 0.4rem;
    }}
    .speaker.s2 {{ color: var(--speaker-alt); }}
  </style>
</head>
<body>
  <div class="progress-track" aria-hidden="true"><div class="progress-bar" id="reading-progress"></div></div>
  <header class="masthead">
    <div class="masthead-inner">
      <nav class="channel-tabs" aria-label="브리핑 채널 전환">
        <a class="channel-tab" href="../../index.html">글로벌 모닝 뉴스</a>
        <a class="channel-tab active" href="../index.html">Deep Dive 심층 연구</a>
      </nav>
      <div class="masthead-tools">
        {copy_btn_html}
        <button type="button" class="tool-btn" id="color-mode-btn">화면 테마 전환</button>
      </div>
    </div>
  </header>

  <div class="reader-shell">
    <aside class="toc-rail" aria-label="문서 목차">
      <a class="back-link" href="../index.html">아카이브 목록으로</a>
      <p class="toc-heading">문서 구성</p>
      <ul class="toc-list" id="toc-list"></ul>
    </aside>

    <article class="paper-sheet">
      <header>
        <div class="date-meta">{esc(date_label)}</div>
        <h1>{esc(display_h1)}</h1>
      </header>
      {audio_block}
      <main id="article-main">
        {body_html}
        {transcript_html}
      </main>
    </article>
  </div>

  {SHARED_UI_SCRIPT}
  <script>
  (function(){{
    var bar = document.getElementById('reading-progress');
    window.addEventListener('scroll', function(){{
      if (!bar) return;
      var doc = document.documentElement;
      var max = doc.scrollHeight - doc.clientHeight;
      bar.style.width = max > 0 ? Math.min(100, (doc.scrollTop / max) * 100) + '%' : '0%';
    }}, {{ passive: true }});

    var tocList = document.getElementById('toc-list');
    var mainEl = document.getElementById('article-main');
    if (tocList && mainEl) {{
      var secs = Array.prototype.slice.call(mainEl.querySelectorAll('section, details.transcript'));
      var links = [];
      secs.forEach(function(sec, idx){{
        if (!sec.id) sec.id = 'auto-sec-' + (idx + 1);
        var label = sec.getAttribute('data-toc');
        if (!label) {{
          var h = sec.querySelector('h2, summary');
          label = h ? h.textContent.replace(/^[^가-힣a-zA-Z0-9]+/, '').trim() : ('섹션 ' + (idx + 1));
        }}
        if (label.length > 22) label = label.slice(0, 22) + '…';
        var li = document.createElement('li');
        var a = document.createElement('a');
        a.className = 'toc-link';
        a.href = '#' + sec.id;
        a.textContent = label;
        li.appendChild(a);
        tocList.appendChild(li);
        links.push({{ sec: sec, link: a }});
      }});

      if ('IntersectionObserver' in window && links.length) {{
        var obs = new IntersectionObserver(function(entries){{
          entries.forEach(function(entry){{
            if (entry.isIntersecting) {{
              links.forEach(function(item){{
                item.link.classList.toggle('active', item.sec === entry.target);
              }});
            }}
          }});
        }}, {{ rootMargin: '-15% 0px -72% 0px' }});
        links.forEach(function(item){{ obs.observe(item.sec); }});
      }}
    }}

    var tf = document.getElementById('transcript-search');
    if (tf) {{
      tf.addEventListener('input', function(){{
        var q = tf.value.trim().toLowerCase();
        document.querySelectorAll('.transcript-body .turn').forEach(function(p){{
          p.classList.toggle('hidden', Boolean(q) && p.textContent.toLowerCase().indexOf(q) === -1);
        }});
      }});
    }}
  }})();
  </script>
</body>
</html>
"""


def _render_article_page_html(
    episode_title: str,
    date_label: str,
    body_html: str,
    mp3_rel_path: str | None,
    duration_seconds: int,
    script: DeepDiveScript | None,
    feed_rel_path: str = "../feed.xml",
    rss_copy_url: str = "",
) -> str:
    esc = html.escape
    cast_line = _format_dialogue_cast_line(script) if script is not None else ""
    transcript_block = ""

    if script is not None:
        label_map = script.speaker_label_map()

        def _render_turns(turns: list[Any]) -> str:
            out: list[str] = []
            for t in turns:
                txt = getattr(t, "text", "").strip()
                if not txt:
                    continue
                sid = getattr(t, "speaker_id", "S1")
                sp_cls = "speaker s2" if sid != "S1" else "speaker"
                out.append(
                    f"<p class='turn'><strong class='{sp_cls}'>{esc(label_map.get(sid, sid))}</strong> "
                    f"{esc(txt)}</p>"
                )
            return "".join(out)

        cast_intro = (
            "<div class='cast-box'>"
            f"<div><strong>진행 구성:</strong> {esc(cast_line)}</div>"
            f"<div class='cast-reason'>{esc(script.cast.format_reason_kr)}</div>"
            "</div>"
        )
        ch_html = [
            cast_intro,
            "<input type='search' id='transcript-search' class='transcript-filter' placeholder='대본 내 키워드 검색' aria-label='대본 내 키워드 검색' />",
            f"<h3>오프닝</h3>{_render_turns(script.opening_turns)}",
        ]
        for idx, (ch_title, ch_turns) in enumerate(script.chapters, start=1):
            ch_html.append(f"<h3>Chapter {idx}. {esc(ch_title)}</h3>{_render_turns(ch_turns)}")
        ch_html.append(f"<h3>클로징</h3>{_render_turns(script.closing_turns)}")
        transcript_block = (
            "<details class='transcript' id='sec-transcript' data-toc='전체 대화 대본'>"
            f"<summary>전체 대화 대본 ({script.total_chars:,}자 / {script.total_turns}턴)</summary>"
            f"<div class='transcript-body'>{''.join(ch_html)}</div>"
            "</details>"
        )

    return render_article_page_from_parts(
        episode_title=episode_title,
        date_label=date_label,
        body_html=body_html,
        mp3_rel_path=mp3_rel_path,
        duration_seconds=duration_seconds,
        cast_line=cast_line,
        transcript_html=transcript_block,
        feed_rel_path=feed_rel_path,
        rss_copy_url=rss_copy_url,
    )


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
    root_base = clean_base[:-5] if clean_base.endswith("/deep") else clean_base
    unified_feed_url = f"{root_base}/feed.xml"
    deep_feed_url = f"{clean_base}/feed.xml"

    counts_by_theme: dict[str, int] = {tid: 0 for tid in CORE_THEME_IDS}
    for ep in episodes:
        tid = ep.get("theme_id", "")
        if tid in counts_by_theme:
            counts_by_theme[tid] += 1

    filter_buttons = [
        f"<button type='button' class='pill active' data-theme='all'>전체 <span class='count'>{len(episodes)}</span></button>"
    ]
    for tid in CORE_THEME_IDS:
        slot = THEME_SLOTS[tid]
        cnt = counts_by_theme.get(tid, 0)
        filter_buttons.append(
            f"<button type='button' class='pill' data-theme='{esc(tid)}'>{esc(slot.emoji)} {esc(slot.label_kr)} <span class='count'>{cnt}</span></button>"
        )

    cards: list[str] = []
    for idx, ep in enumerate(episodes):
        tid = ep.get("theme_id", "")
        theme_label = ep.get("theme_label", tid)
        sub_subject = ep.get("sub_subject_kr", "")
        article_href = f"posts/{esc(ep['article_filename'], quote=True)}"
        dur = (
            _format_itunes_duration(ep.get("duration_seconds", 0))
            if ep.get("mp3_filename")
            else "텍스트 노트"
        )
        cast_summary = ep.get("dialogue_cast_summary", "")
        date_str = ep.get("date_slug", "").replace("-", ".")
        raw_title = ep.get("title", "")
        # 날짜/테마 대괄호 접두사가 제목에 들어있는 경우 깔끔하게 본 제목 추출
        clean_title = re.sub(r"^\[[^\]]+\]\s*", "", raw_title)
        clean_title = re.sub(r"\s*\|\s*\d{4}\.\d{2}\.\d{2}$", "", clean_title).strip() or raw_title

        search_blob = f"{clean_title} {ep.get('subtitle_kr', '')} {sub_subject} {ep.get('summary_plain', '')}".lower()
        featured_cls = " featured" if idx == 0 else ""
        featured_tag = "<span class='latest-tag'>최신 브리핑</span>" if idx == 0 else ""

        audio_tag = (
            "<div class='audio-deck compact'>"
            f"<audio controls preload='none' src='episodes/{esc(ep['mp3_filename'], quote=True)}'></audio>"
            "</div>"
            if ep.get("mp3_filename")
            else ""
        )

        meta_chips = [f"<span class='meta-date'>{esc(date_str)}</span>", f"<span class='meta-chip'>{esc(theme_label)}</span>"]
        if sub_subject:
            meta_chips.append(f"<span class='meta-chip subtle'>{esc(sub_subject)}</span>")
        if cast_summary:
            meta_chips.append(f"<span class='meta-info'>{esc(cast_summary)}</span>")
        meta_chips.append(f"<span class='meta-info'>{esc(dur)}</span>")

        cards.append(
            f"<article class='card{featured_cls}' data-theme='{esc(tid)}' data-search='{esc(search_blob, quote=True)}'>"
            f"<div class='card-top'>{featured_tag}<div class='meta-row'>{''.join(meta_chips)}</div></div>"
            f"<h2><a href='{article_href}'>{esc(clean_title)}</a></h2>"
            f"<p class='sub'>{esc(ep.get('subtitle_kr', ''))}</p>"
            f"{audio_tag}"
            f"<div class='actions'><a class='read-btn' href='{article_href}'>연구 노트 정독하기</a></div>"
            "</article>"
        )

    cards_html = "\n".join(cards) if cards else "<p class='empty-state'>아직 발행된 심층 브리핑이 없습니다.</p>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{esc(DEFAULT_DEEP_PODCAST_TITLE)}</title>
  <link rel="alternate" type="application/rss+xml" title="{esc(DEFAULT_DEEP_PODCAST_TITLE)}" href="feed.xml" />
  {SHARED_FONTS_HEAD}
  {THEME_INIT_SCRIPT}
  <style>
    {SHARED_EDITORIAL_CSS}
    .page-wrap {{
      max-width: 920px;
      margin: 0 auto;
      padding: 2.25rem 1.25rem 4.5rem;
    }}
    .hero {{
      margin-bottom: 1.75rem;
      padding-bottom: 1.35rem;
      border-bottom: 1px solid var(--border);
    }}
    .hero h1 {{
      margin: 0 0 0.45rem;
      font-size: clamp(1.6rem, 3vw, 2.05rem);
      letter-spacing: -0.025em;
    }}
    .hero-desc {{
      margin: 0 0 1rem;
      color: var(--ink-secondary);
      font-size: 0.96rem;
      max-width: 66ch;
    }}
    .feed-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem 0.9rem;
      font-size: 0.84rem;
      color: var(--ink-muted);
    }}
    .controls-bar {{
      display: flex;
      flex-direction: column;
      gap: 0.85rem;
      margin-bottom: 1.5rem;
    }}
    .search-input {{
      width: 100%;
      padding: 0.62rem 0.9rem;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface-sheet);
      color: var(--ink-primary);
      font-family: inherit;
      font-size: 0.92rem;
    }}
    .search-input::placeholder {{ color: var(--ink-muted); }}
    .filters {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
    }}
    .pill {{
      border: 1px solid var(--border);
      background: var(--surface-sheet);
      color: var(--ink-secondary);
      font-family: inherit;
      padding: 0.38rem 0.78rem;
      border-radius: 7px;
      cursor: pointer;
      font-size: 0.84rem;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
    }}
    .pill:hover {{ border-color: var(--border-strong); color: var(--ink-primary); }}
    .pill.active {{
      background: var(--accent);
      color: #FFFFFF;
      border-color: var(--accent);
    }}
    @media (prefers-color-scheme: dark) {{
      :root:not([data-color-mode="light"]) .pill.active,
      :root:not([data-color-mode="light"]) .latest-tag {{ color: #0B1018; }}
    }}
    :root[data-color-mode="dark"] .pill.active {{ color: #0B1018; }}
    .pill .count {{
      font-size: 0.75rem;
      opacity: 0.82;
      font-variant-numeric: tabular-nums;
    }}
    .card {{
      background: var(--surface-sheet);
      border: 1px solid var(--border);
      border-radius: 11px;
      padding: 1.35rem 1.5rem;
      margin-bottom: 0.95rem;
    }}
    .card.featured {{
      border-color: var(--accent);
      border-width: 1.5px;
    }}
    .card.hidden {{ display: none; }}
    .card-top {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem;
      margin-bottom: 0.5rem;
    }}
    .latest-tag {{
      background: var(--accent);
      color: #FFFFFF;
      font-size: 0.73rem;
      font-weight: 700;
      padding: 0.16rem 0.5rem;
      border-radius: 4px;
    }}
    :root[data-color-mode="dark"] .latest-tag {{ color: #0B1018; }}
    .meta-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.4rem 0.55rem;
      font-size: 0.81rem;
      color: var(--ink-muted);
    }}
    .meta-date {{ font-variant-numeric: tabular-nums; font-weight: 600; color: var(--ink-secondary); }}
    .meta-chip {{
      background: var(--surface-subtle);
      color: var(--ink-secondary);
      padding: 0.14rem 0.48rem;
      border-radius: 4px;
      font-weight: 600;
    }}
    .meta-chip.subtle {{ background: transparent; border: 1px solid var(--border); font-weight: 500; }}
    .card h2 {{
      margin: 0 0 0.45rem;
      font-size: 1.22rem;
      line-height: 1.38;
      letter-spacing: -0.015em;
    }}
    .card h2 a {{ color: var(--ink-primary); text-decoration: none; }}
    .card h2 a:hover {{ color: var(--accent); }}
    .sub {{
      font-family: var(--font-serif);
      color: var(--ink-secondary);
      margin: 0 0 0.95rem;
      font-size: 0.95rem;
      line-height: 1.65;
    }}
    .audio-deck.compact {{ margin: 0 0 0.85rem; }}
    .audio-deck.compact audio {{ width: 100%; height: 36px; }}
    .actions {{ display: flex; align-items: center; }}
    .read-btn {{
      display: inline-flex;
      align-items: center;
      font-size: 0.86rem;
      font-weight: 600;
      color: var(--accent);
      padding: 0.34rem 0.72rem;
      border-radius: 6px;
      background: var(--accent-soft);
      text-decoration: none;
    }}
    .read-btn:hover {{ text-decoration: none; opacity: 0.9; }}
    .empty-state {{
      text-align: center;
      padding: 2.5rem 1rem;
      color: var(--ink-muted);
      background: var(--surface-sheet);
      border: 1px dashed var(--border);
      border-radius: 10px;
    }}
  </style>
</head>
<body>
  <header class="masthead">
    <div class="masthead-inner">
      <nav class="channel-tabs" aria-label="브리핑 채널 전환">
        <a class="channel-tab" href="../index.html">글로벌 모닝 뉴스</a>
        <a class="channel-tab active" href="index.html">Deep Dive 심층 연구</a>
      </nav>
      <div class="masthead-tools">
        <button type="button" class="tool-btn" data-copy-rss="{esc(unified_feed_url, quote=True)}">통합 RSS 복사</button>
        <button type="button" class="tool-btn" data-copy-rss="{esc(deep_feed_url, quote=True)}">Deep Dive 단독 RSS 복사</button>
        <button type="button" class="tool-btn" id="color-mode-btn">화면 테마 전환</button>
      </div>
    </div>
  </header>

  <main class="page-wrap">
    <section class="hero">
      <h1>{esc(DEFAULT_DEEP_PODCAST_TITLE)}</h1>
      <p class="hero-desc">{esc(DEFAULT_DEEP_PODCAST_DESCRIPTION)}</p>
      <div class="feed-bar">
        <span>매일 오전 07:30 KST 발행</span>
        <span>통합 RSS: <a href="{esc(unified_feed_url, quote=True)}">{esc(unified_feed_url)}</a></span>
      </div>
    </section>

    <section class="controls-bar" aria-label="아카이브 검색 및 분야 필터">
      <input type="search" id="archive-search" class="search-input" placeholder="논문 제목, 세부 주제, 핵심 키워드로 검색" aria-label="브리핑 검색" />
      <div class="filters">{''.join(filter_buttons)}</div>
    </section>

    <section id="cards">{cards_html}</section>
    <p id="no-match" class="empty-state" style="display:none;">선택한 조건에 일치하는 브리핑이 없습니다. 검색어를 지우거나 전체 탭을 선택해 보세요.</p>
  </main>

  {SHARED_UI_SCRIPT}
  <script>
  (function(){{
    var pills = document.querySelectorAll('.pill');
    var cards = document.querySelectorAll('.card');
    var searchInput = document.getElementById('archive-search');
    var noMatch = document.getElementById('no-match');
    var activeTheme = 'all';

    function applyFilter() {{
      var q = searchInput ? searchInput.value.trim().toLowerCase() : '';
      var visible = 0;
      cards.forEach(function(c){{
        var themeOk = activeTheme === 'all' || c.getAttribute('data-theme') === activeTheme;
        var hay = (c.getAttribute('data-search') || '').toLowerCase();
        var searchOk = !q || hay.indexOf(q) !== -1;
        var show = themeOk && searchOk;
        c.classList.toggle('hidden', !show);
        if (show) visible++;
      }});
      if (noMatch) noMatch.style.display = (cards.length > 0 && visible === 0) ? 'block' : 'none';
    }}

    pills.forEach(function(btn){{
      btn.addEventListener('click', function(){{
        pills.forEach(function(p){{ p.classList.remove('active'); }});
        btn.classList.add('active');
        activeTheme = btn.getAttribute('data-theme') || 'all';
        applyFilter();
      }});
    }});

    if (searchInput) {{
      searchInput.addEventListener('input', applyFilter);
    }}
  }})();
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
    del drive_doc_url
    episodes_dir = deep_dir / "episodes"
    posts_dir = deep_dir / "posts"
    episodes_dir.mkdir(parents=True, exist_ok=True)
    posts_dir.mkdir(parents=True, exist_ok=True)

    now_kst = datetime.now(KST)
    time_slug = now_kst.strftime("%H%M%S")
    slug = _ascii_post_slug(date_slug, theme.theme_id, docs)
    guid = f"deep-dive-{date_slug}-{theme.theme_id}-{time_slug}"

    episode_title = (
        script.episode_title
        if script is not None
        else f"[{theme.label_kr}] {note.episode_title_kr} | {date_slug.replace('-', '.')}"
    )

    clean_base = base_url.rstrip("/")
    archive_url = f"{clean_base}/"
    article_filename = f"{slug}.html"
    article_url = f"{clean_base}/posts/{article_filename}"
    deep_feed_url = f"{clean_base}/feed.xml"

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

    article_body_html = render_study_note_body_html(
        note=note,
        docs=docs,
        theme=theme,
        decision=decision,
        article_url=None,
        archive_url=None,
        drive_doc_url=None,
        usage_footer_html=usage_footer_html,
        plain_url_links=False,
    )
    rss_body_html = render_study_note_body_html(
        note=note,
        docs=docs,
        theme=theme,
        decision=decision,
        article_url=article_url,
        archive_url=archive_url,
        drive_doc_url=None,
        usage_footer_html=usage_footer_html,
        plain_url_links=True,
    )

    article_html = _render_article_page_html(
        episode_title=episode_title,
        date_label=now_kst.strftime("%Y년 %m월 %d일 (%a) KST"),
        body_html=article_body_html,
        mp3_rel_path=f"../episodes/{mp3_filename}" if mp3_filename else None,
        duration_seconds=duration_seconds,
        script=script,
        rss_copy_url=deep_feed_url,
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
        "description_html": rss_body_html,
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

    unified_feed_url = f"{clean_base}/feed.xml"
    if deep_dir.name == "deep":
        from podcast_feed import rebuild_unified_root_feed

        root_base = clean_base[:-5] if clean_base.endswith("/deep") else clean_base
        rebuild_unified_root_feed(
            pages_dir=deep_dir.parent,
            base_url=root_base,
        )
        unified_feed_url = f"{root_base}/feed.xml"

    index_html = _render_archive_index_html(episodes=episodes, base_url=clean_base)
    (deep_dir / "index.html").write_text(index_html, encoding="utf-8")

    return PublishedDeepDive(
        feed_path=feed_path,
        article_path=article_path,
        article_url=article_url,
        feed_url=unified_feed_url,
        mp3_url=mp3_url,
        slug=slug,
    )
