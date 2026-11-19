"""큐레이션된 브리핑을 텔레그램 HTML 메시지로 포맷하고 발송합니다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import os
from pathlib import Path

import requests

from curator import CuratedArticle, DailyBriefing


KST = timezone(timedelta(hours=9))
TELEGRAM_MAX_LEN = 4000  # 4096자 제한에 여유 버퍼 확보


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def _format_article(idx: int, art: CuratedArticle) -> str:
    cat = _escape(art.category_kr)
    title_kr = _escape(art.title_kr)
    title_en = _escape(art.title_en)
    url = _escape(art.url)

    bullets = "\n".join(f"• {_escape(b)}" for b in art.summary_bullets)

    return (
        f"<b>{idx}. [{cat}] {title_kr}</b>\n"
        f"<i>{title_en}</i>\n"
        f"{bullets}\n"
        f"🔗 <a href=\"{url}\">원문 보기</a>"
    )


def build_messages(briefing: DailyBriefing) -> list[str]:
    """텔레그램 길이 제한(4096자)을 넘지 않도록 기사 단위로 안전하게 분할합니다."""
    today_kst = datetime.now(KST).strftime("%Y.%m.%d (%a)")
    header = (
        f"🗞 <b>글로벌 모닝 브리핑 (The Guardian) | {_escape(today_kst)}</b>\n"
        f"{_escape(briefing.morning_intro_kr)}\n"
        f"{'─' * 20}"
    )

    article_blocks = [
        _format_article(idx, art)
        for idx, art in enumerate(briefing.articles, start=1)
    ]

    messages: list[str] = []
    current = header

    for block in article_blocks:
        candidate = f"{current}\n\n{block}"
        if len(candidate) > TELEGRAM_MAX_LEN:
            messages.append(current)
            current = block
        else:
            current = candidate

    if current:
        messages.append(current)

    return messages


def send_telegram_messages(
    messages: list[str],
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> None:
    """포맷된 HTML 메시지를 텔레그램 Bot API로 전송합니다."""
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    target_chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

    if not token or not target_chat_id:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN 및 TELEGRAM_CHAT_ID 환경변수가 필요합니다."
        )

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"

    for idx, text in enumerate(messages, start=1):
        resp = requests.post(
            api_url,
            json={
                "chat_id": target_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if not resp.ok:
            raise RuntimeError(
                f"텔레그램 전송 실패 (part {idx}/{len(messages)}): "
                f"{resp.status_code} {resp.text}"
            )
        print(f"[INFO] Sent Telegram message part {idx}/{len(messages)}")


def send_telegram_audio(
    audio_path: Path,
    title: str,
    performer: str = "글로벌 모닝 브리핑 (The Guardian)",
    caption: str | None = None,
    duration: int | None = None,
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> None:
    """MP3 오디오 브리핑을 텔레그램 sendAudio API로 전송합니다."""
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    target_chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

    if not token or not target_chat_id:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN 및 TELEGRAM_CHAT_ID 환경변수가 필요합니다."
        )

    if not audio_path.exists():
        raise FileNotFoundError(f"오디오 파일을 찾을 수 없습니다: {audio_path}")

    api_url = f"https://api.telegram.org/bot{token}/sendAudio"
    data: dict[str, str | int] = {
        "chat_id": target_chat_id,
        "title": title,
        "performer": performer,
    }
    if duration and duration > 0:
        data["duration"] = duration
    if caption:
        data["caption"] = caption
        data["parse_mode"] = "HTML"

    with audio_path.open("rb") as audio_file:
        resp = requests.post(
            api_url,
            data=data,
            files={"audio": (audio_path.name, audio_file, "audio/mpeg")},
            timeout=180,
        )

    if not resp.ok:
        raise RuntimeError(
            f"텔레그램 오디오 전송 실패: {resp.status_code} {resp.text}"
        )
    print(f"[INFO] Sent Telegram audio: {audio_path.name}")
