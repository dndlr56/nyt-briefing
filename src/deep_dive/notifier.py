"""Deep Dive 브리핑 완료 시 텔레그램으로 핵심 요약·웹 리더·Drive 링크 알림을 발송합니다."""

from __future__ import annotations

import html

from deep_dive.drive_archiver import DriveArchiveResult
from deep_dive.models import CurationDecision, DeepDiveStudyNote, FetchedDocument
from deep_dive.themes import ThemeSlot
from deep_dive.web_publisher import PublishedDeepDive
from telegram_sender import send_telegram_messages


def build_deep_dive_telegram_message(
    date_slug: str,
    theme: ThemeSlot,
    decision: CurationDecision,
    note: DeepDiveStudyNote,
    docs: list[FetchedDocument],
    published: PublishedDeepDive | None = None,
    drive_result: DriveArchiveResult | None = None,
    duration_seconds: int = 0,
    usage_summary_text: str | None = None,
) -> str:
    esc = html.escape
    mode_label = "1편 심층 해부" if decision.mode == "single_deep_dive" else f"{len(docs)}편 종합 브리핑"
    dur_min = round(duration_seconds / 60) if duration_seconds > 0 else 0
    dur_str = f" · 🎧 약 {dur_min}분" if dur_min > 0 else ""

    bullets = "\n".join(f"• {esc(b)}" for b in note.tldr_bullets)
    sources = "\n".join(
        f"  - {esc(d.candidate.source_name)}: <a href=\"{esc(d.candidate.url, quote=True)}\">{esc(d.candidate.title[:70])}</a>"
        for d in docs
    )

    links: list[str] = []
    if published is not None:
        links.append(f"📖 <a href=\"{esc(published.article_url, quote=True)}\">웹 리더에서 정독하기</a>")
        if published.mp3_url:
            links.append(f"🎧 <a href=\"{esc(published.feed_url, quote=True)}\">팟캐스트 RSS</a>")
    if drive_result is not None:
        links.append(f"📝 <a href=\"{esc(drive_result.doc_url, quote=True)}\">Google Doc</a>")
        links.append(f"📁 <a href=\"{esc(drive_result.folder_url, quote=True)}\">Drive(PDF)</a>")
    links_line = " | ".join(links)

    parts = [
        f"{esc(theme.emoji)} <b>Deep Dive 브리핑 | {esc(date_slug)}</b>",
        f"<i>{esc(theme.label_kr)} · {esc(decision.sub_subject_kr)} · {esc(mode_label)}{esc(dur_str)}</i>",
        "",
        f"<b>{esc(note.episode_title_kr)}</b>",
        f"{esc(note.subtitle_kr)}",
        "",
        f"{bullets}",
        "",
        f"<b>📚 원문 출처</b>\n{sources}",
    ]
    if links_line:
        parts.extend(["", links_line])
    if usage_summary_text:
        parts.extend(["", f"<pre>{esc(usage_summary_text)}</pre>"])

    return "\n".join(parts)


def notify_deep_dive_telegram(message: str) -> None:
    send_telegram_messages([message])
