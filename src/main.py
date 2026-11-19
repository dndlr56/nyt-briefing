"""The Guardian 기사 전문 기반 글로벌 모닝 브리핑 파이프라인 실행 진입점."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

from dotenv import load_dotenv

from audio_generator import generate_audio_briefing
from curator import select_articles, summarize_full_articles
from guardian_fetcher import fetch_article_full_texts, fetch_guardian_candidates
from script_writer import format_script_preview, write_longform_script
from telegram_sender import (
    build_messages,
    send_telegram_audio,
    send_telegram_messages,
)


KST = timezone(timedelta(hours=9))


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="The Guardian 주요 기사 전문을 큐레이션해 10~13분 오디오/텍스트 브리핑으로 전송합니다."
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
        help="텍스트 브리핑과 함께 10~13분 오디오 브리핑도 생성·전송합니다.",
    )
    parser.add_argument(
        "--audio-only",
        action="store_true",
        help="텍스트 브리핑은 생략하고 오디오 브리핑만 생성·전송합니다.",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default=None,
        help="Gemini 3.8 TTS 앵커 목소리 이름 또는 커스텀 보이스 ID (기본값: Kore)",
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

    print(f"[1/5] Fetching Guardian article candidates (last {args.hours}h)...")
    candidates = fetch_guardian_candidates(hours=args.hours)
    print(f"      Collected {len(candidates)} candidate articles.")

    if not candidates:
        print("[ERROR] No candidate articles fetched from The Guardian API.", file=sys.stderr)
        return 1

    print("[2/5] Stage 1 — Selecting top 5~6 articles with Gemini...")
    selected_pairs = select_articles(candidates)
    print(f"      Selected {len(selected_pairs)} articles for full-text deep dive.")

    print("[3/5] Stage 2 — Fetching full article texts from The Guardian...")
    full_articles = fetch_article_full_texts(selected_pairs)
    print(f"      Loaded full text for {len(full_articles)} articles.")

    if not full_articles:
        print("[ERROR] Failed to load full text for selected articles.", file=sys.stderr)
        return 1

    messages: list[str] = []
    if include_text:
        print("[4/5] Summarizing full articles for Telegram text briefing...")
        briefing = summarize_full_articles(full_articles)
        messages = build_messages(briefing)
        print(f"      Prepared {len(messages)} Telegram text message(s).")

    script = None
    generated_audio = None
    if include_audio:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        date_slug = datetime.now(KST).strftime("%Y%m%d")

        print("[4/5] Writing 10~13 min single-anchor longform radio script...")
        script = write_longform_script(full_articles)
        script_preview = format_script_preview(script)
        script_path = args.output_dir / f"guardian_briefing_{date_slug}_script.txt"
        script_path.write_text(script_preview, encoding="utf-8")
        total_chars = len(script.part1_text) + len(script.part2_text)
        print(f"      Prepared script ({total_chars} chars, saved to {script_path}).")

        print("      Synthesizing single-voice longform MP3 with Gemini 3.8 TTS...")
        mp3_path = args.output_dir / f"guardian_briefing_{date_slug}.mp3"
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

    print("[5/5] Delivering briefing to Telegram...")
    if include_text:
        send_telegram_messages(messages)

    if generated_audio is not None and script is not None:
        minutes, seconds = divmod(generated_audio.duration_seconds, 60)
        caption = (
            f"🎧 <b>{script.episode_title}</b>\n"
            f"약 {minutes}분 {seconds:02d}초 · The Guardian 전문 심층 브리핑 "
            f"({generated_audio.voice})"
        )
        send_telegram_audio(
            audio_path=generated_audio.mp3_path,
            title=script.episode_title,
            performer="글로벌 모닝 브리핑 (The Guardian)",
            caption=caption,
            duration=generated_audio.duration_seconds,
        )

    print("[DONE] Morning briefing delivered successfully!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
