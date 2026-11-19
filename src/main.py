"""NYT 모닝 브리핑 파이프라인 실행 진입점."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

from dotenv import load_dotenv

from audio_generator import generate_audio_briefing
from curator import curate_briefing
from nyt_fetcher import fetch_recent_articles
from script_writer import format_script_preview, write_audio_script
from telegram_sender import (
    build_messages,
    send_telegram_audio,
    send_telegram_messages,
)


KST = timezone(timedelta(hours=9))


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="뉴욕타임즈 주요 기사를 번역·큐레이션해 텔레그램으로 전송합니다."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="텔레그램으로 전송하지 않고 생성된 결과만 콘솔 출력 및 로컬 저장합니다.",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=36,
        help="수집할 최근 기사 시간 범위 (기본값: 36시간)",
    )
    parser.add_argument(
        "--with-audio",
        action="store_true",
        help="텍스트 브리핑과 함께 1인 라디오 앵커 오디오 브리핑도 생성·전송합니다.",
    )
    parser.add_argument(
        "--audio-only",
        action="store_true",
        help="텍스트 브리핑은 보내지 않고 오디오 브리핑만 생성·전송합니다.",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default=None,
        help="Gemini 3.8 TTS 목소리 이름 또는 커스텀 보이스 ID (기본값: Kore)",
    )
    parser.add_argument(
        "--tts-model",
        type=str,
        default=None,
        help="사용할 Gemini TTS 모델 (기본값: gemini-3.8-flash-tts)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="생성된 대본과 MP3 오디오를 저장할 폴더 (기본값: output)",
    )
    args = parser.parse_args()

    include_audio = args.with_audio or args.audio_only
    include_text = not args.audio_only
    total_steps = 5 if include_audio else 3
    step = 1

    print(f"[{step}/{total_steps}] Fetching NYT RSS articles (last {args.hours}h)...")
    articles = fetch_recent_articles(hours=args.hours)
    print(f"      Collected {len(articles)} unique articles.")
    step += 1

    if not articles:
        print("[ERROR] No articles fetched from NYT RSS feeds.", file=sys.stderr)
        return 1

    print(f"[{step}/{total_steps}] Curating & translating briefing with Gemini...")
    briefing = curate_briefing(articles)
    messages = build_messages(briefing) if include_text else []
    if include_text:
        print(f"      Prepared {len(messages)} Telegram text message(s).")
    step += 1

    script = None
    generated_audio = None
    if include_audio:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        date_slug = datetime.now(KST).strftime("%Y%m%d")

        print(f"[{step}/{total_steps}] Writing 1-anchor radio briefing script...")
        script = write_audio_script(briefing)
        script_preview = format_script_preview(script)
        script_path = args.output_dir / f"nyt_briefing_{date_slug}_script.txt"
        script_path.write_text(script_preview, encoding="utf-8")
        print(
            f"      Prepared {len(script.segments)} segments "
            f"(saved script to {script_path})."
        )
        step += 1

        print(f"[{step}/{total_steps}] Synthesizing audio with Gemini 3.8 TTS...")
        mp3_path = args.output_dir / f"nyt_briefing_{date_slug}.mp3"
        generated_audio = generate_audio_briefing(
            script=script,
            output_mp3=mp3_path,
            voice=args.voice,
            tts_model=args.tts_model,
        )
        size_mb = generated_audio.file_size_bytes / (1024 * 1024)
        print(
            f"      Generated {generated_audio.mp3_path} "
            f"({generated_audio.duration_seconds}s, {size_mb:.2f} MB, "
            f"voice={generated_audio.voice}, model={generated_audio.model})."
        )
        step += 1

    if args.dry_run:
        print("\n========== DRY RUN OUTPUT ==========")
        if include_text:
            for idx, msg in enumerate(messages, start=1):
                print(f"\n--- Text Part {idx}/{len(messages)} ---\n{msg}")
        if script is not None:
            print(f"\n--- Audio Script ---\n{format_script_preview(script)}")
        if generated_audio is not None:
            print(f"\n--- Audio File ---\n{generated_audio.mp3_path.resolve()}")
        print("\n====================================")
        return 0

    print(f"[{step}/{total_steps}] Delivering briefing to Telegram...")
    if include_text:
        send_telegram_messages(messages)

    if generated_audio is not None and script is not None:
        minutes, seconds = divmod(generated_audio.duration_seconds, 60)
        caption = (
            f"🎧 <b>{script.episode_title}</b>\n"
            f"약 {minutes}분 {seconds:02d}초 · 1인 라디오 브리핑 "
            f"({generated_audio.voice})"
        )
        send_telegram_audio(
            audio_path=generated_audio.mp3_path,
            title=script.episode_title,
            performer="NYT 모닝 브리핑",
            caption=caption,
            duration=generated_audio.duration_seconds,
        )

    print("[DONE] Morning briefing delivered successfully!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
