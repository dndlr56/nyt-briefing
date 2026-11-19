"""The Guardian(메인) + NYT(보완) 글로벌 모닝 라운드업 브리핑 파이프라인 실행 진입점."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

from dotenv import load_dotenv

from audio_generator import generate_audio_briefing
from curator import select_articles, summarize_full_articles
from guardian_fetcher import (
    ArticleCandidate,
    fetch_article_full_texts,
    fetch_guardian_candidates,
)
from nyt_fetcher import fetch_recent_articles as fetch_nyt_articles
from script_writer import (
    format_script_preview,
    get_script_segments,
    write_roundup_script,
)
from telegram_sender import (
    build_messages,
    send_telegram_audio,
    send_telegram_messages,
)


KST = timezone(timedelta(hours=9))
NYT_SUPPLEMENT_SECTIONS = {"Business", "Economy", "Technology", "World", "Science", "Health"}


def _fetch_combined_candidates(hours: int) -> list[ArticleCandidate]:
    """The Guardian(전문 가능, 메인)과 NYT RSS(미국·글로벌 보완) 후보를 합칩니다."""
    guardian_candidates = fetch_guardian_candidates(hours=hours)
    print(f"      Guardian candidates: {len(guardian_candidates)}")

    nyt_candidates: list[ArticleCandidate] = []
    try:
        nyt_raw = fetch_nyt_articles(hours=hours, max_per_feed=8)
        filtered = [a for a in nyt_raw if a.section in NYT_SUPPLEMENT_SECTIONS][:20]
        for idx, art in enumerate(filtered, start=1):
            nyt_candidates.append(
                ArticleCandidate(
                    article_id=f"nyt:{idx}",
                    section=art.section,
                    title=art.title,
                    trail_text=art.description,
                    url=art.url,
                    published_at=art.published_at,
                    wordcount=0,
                    source="NYT",
                )
            )
        print(f"      NYT supplement candidates: {len(nyt_candidates)}")
    except Exception as exc:
        print(f"[WARN] NYT supplement fetch skipped due to error: {exc}")

    # Guardian(전문 조회 가능)을 앞쪽에 최대 55개, NYT 보완을 뒤쪽에 최대 20개 배치
    return guardian_candidates[:55] + nyt_candidates[:20]


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="The Guardian(메인) + NYT(보완) 8~10개 글로벌 기사를 큐레이션해 오디오/텍스트 브리핑으로 전송합니다."
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
        help="읽기용 텍스트 요약 브리핑과 함께 오디오 브리핑도 생성·전송합니다.",
    )
    parser.add_argument(
        "--audio-only",
        action="store_true",
        help="텍스트 브리핑은 생략하고 오디오 브리핑만 생성·전송합니다.",
    )
    parser.add_argument(
        "--tts-provider",
        type=str,
        choices=("google_cloud_tts", "gemini_tts"),
        default=None,
        help="사용할 TTS 엔진 (기본값: google_cloud_tts)",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default=None,
        help="앵커 목소리 이름 (예: ko-KR-Chirp3-HD-Kore, ko-KR-Neural2-C)",
    )
    parser.add_argument(
        "--tts-model",
        type=str,
        default=None,
        help="gemini_tts 선택 시 사용할 모델 (기본값: gemini-3.8-flash-tts)",
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

    print(f"[1/5] Fetching global article candidates (last {args.hours}h)...")
    candidates = _fetch_combined_candidates(hours=args.hours)
    print(f"      Total candidate pool: {len(candidates)} articles.")

    if not candidates:
        print("[ERROR] No candidate articles fetched.", file=sys.stderr)
        return 1

    print("[2/5] Stage 1 — Selecting top 8~10 global stories with Gemini...")
    selected_pairs = select_articles(candidates)
    guardian_cnt = sum(1 for c, _ in selected_pairs if c.source == "The Guardian")
    nyt_cnt = sum(1 for c, _ in selected_pairs if c.source == "NYT")
    print(
        f"      Selected {len(selected_pairs)} stories "
        f"(Guardian: {guardian_cnt}, NYT supplement: {nyt_cnt})."
    )

    print("[3/5] Stage 2 — Loading full texts for Guardian stories & NYT summaries...")
    full_articles = fetch_article_full_texts(selected_pairs)
    print(f"      Loaded {len(full_articles)} articles.")

    if not full_articles:
        print("[ERROR] Failed to load selected articles.", file=sys.stderr)
        return 1

    messages: list[str] = []
    if include_text:
        print("[4/5] Writing reading-friendly text summary for Telegram...")
        briefing = summarize_full_articles(full_articles)
        messages = build_messages(briefing)
        print(f"      Prepared {len(messages)} Telegram text message(s).")

    script = None
    generated_audio = None
    if include_audio:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        date_slug = datetime.now(KST).strftime("%Y%m%d")

        print("[4/5] Writing 7~9 min single-anchor roundup radio script...")
        script = write_roundup_script(full_articles)
        script_preview = format_script_preview(script)
        script_path = args.output_dir / f"global_briefing_{date_slug}_script.txt"
        script_path.write_text(script_preview, encoding="utf-8")
        total_chars = sum(len(text) for _, text in get_script_segments(script))
        print(
            f"      Prepared script ({len(script.stories)} stories, {total_chars} chars, "
            f"saved to {script_path})."
        )

        print("      Synthesizing single-voice MP3 audio...")
        mp3_path = args.output_dir / f"global_briefing_{date_slug}.mp3"
        generated_audio = generate_audio_briefing(
            script=script,
            output_mp3=mp3_path,
            tts_provider=args.tts_provider,
            voice=args.voice,
            tts_model=args.tts_model,
        )
        size_mb = generated_audio.file_size_bytes / (1024 * 1024)
        print(
            f"      Generated {generated_audio.mp3_path} "
            f"({generated_audio.duration_seconds}s, {size_mb:.2f} MB, "
            f"provider={generated_audio.provider}, voice={generated_audio.voice})."
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
            f"약 {minutes}분 {seconds:02d}초 · 글로벌 주요 뉴스 {len(script.stories)}건 "
            f"({generated_audio.voice})"
        )
        send_telegram_audio(
            audio_path=generated_audio.mp3_path,
            title=script.episode_title,
            performer="글로벌 모닝 브리핑",
            caption=caption,
            duration=generated_audio.duration_seconds,
        )

    print("[DONE] Global morning briefing delivered successfully!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
