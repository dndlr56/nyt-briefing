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
from podcast_feed import publish_podcast_episode
from rss_news_fetcher import fetch_multi_outlet_candidates
from script_writer import (
    build_daily_briefing_from_script,
    format_script_preview,
    get_script_segments,
    write_roundup_script,
)
from telegram_sender import (
    build_messages,
    send_telegram_audio,
    send_telegram_messages,
)
from usage_tracker import RunUsageTracker


KST = timezone(timedelta(hours=9))
_DEDUP_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "of", "to", "in", "on", "at", "by", "with",
    "from", "as", "is", "are", "was", "were", "be", "been", "being", "it", "its",
    "that", "this", "after", "over", "into", "about", "new", "says", "say", "said",
    "how", "why", "what", "when", "who", "will", "amid", "up", "out", "more", "than",
}


def _title_keywords(title: str) -> set[str]:
    import re

    tokens = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {t for t in tokens if len(t) >= 3 and t not in _DEDUP_STOPWORDS}


def _deduplicate_cross_outlet_candidates(
    candidates: list[ArticleCandidate],
) -> list[ArticleCandidate]:
    """
    서로 다른 매체가 오늘 동시간대에 명백히 같은 단일 속보를 보도한 경우만 보수적으로 정리합니다.
    (핵심 키워드 >= 4개 겹치고 Jaccard 유사도 >= 0.55 이상일 때만 중복으로 판단해
     다른 각도/후속 보도가 억울하게 제거되지 않도록 합니다.)
    """
    kept: list[ArticleCandidate] = []
    kept_kws: list[set[str]] = []

    for cand in candidates:
        kws = _title_keywords(cand.title)
        is_dup = False
        if len(kws) >= 4:
            for prev, prev_kws in zip(kept, kept_kws):
                if prev.source == cand.source:
                    continue
                inter = kws & prev_kws
                union = kws | prev_kws
                if len(inter) >= 4 and union and (len(inter) / len(union)) >= 0.55:
                    is_dup = True
                    break
        if not is_dup:
            kept.append(cand)
            kept_kws.append(kws)
    return kept


def _fetch_combined_candidates(hours: int) -> list[ArticleCandidate]:
    """The Guardian(API) + BBC·CNBC·NPR(공식 RSS) 4개 전문 매체 후보를 균형 있게 수집합니다."""
    guardian_candidates = fetch_guardian_candidates(hours=hours)[:22]
    print(f"      Guardian candidates: {len(guardian_candidates)}")

    rss_candidates: list[ArticleCandidate] = []
    try:
        rss_candidates = fetch_multi_outlet_candidates(
            hours=hours,
            max_per_outlet={"BBC": 20, "CNBC": 20, "NPR": 20},
        )
        counts: dict[str, int] = {}
        for c in rss_candidates:
            counts[c.source] = counts.get(c.source, 0) + 1
        summary = ", ".join(f"{k}: {v}" for k, v in counts.items()) or "0"
        print(f"      BBC/CNBC/NPR candidates: {len(rss_candidates)} ({summary})")
    except Exception as exc:
        print(f"[WARN] Multi-outlet RSS fetch skipped due to error: {exc}")

    combined = guardian_candidates + rss_candidates
    deduped = _deduplicate_cross_outlet_candidates(combined)
    removed = len(combined) - len(deduped)
    if removed > 0:
        print(f"      Cross-outlet breaking-news dedup: removed {removed} exact duplicate(s).")
    return deduped


def _load_recent_covered_urls(podcast_dir: Path, max_episodes: int = 3) -> set[str]:
    """최근 발행된 팟캐스트 에피소드(episodes/metadata.json)에 포함된 원문 URL을 추출합니다."""
    import json
    import re

    meta_path = podcast_dir / "episodes" / "metadata.json"
    if not meta_path.exists():
        return set()
    try:
        episodes = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(episodes, list):
            return set()
    except (OSError, json.JSONDecodeError):
        return set()

    urls: set[str] = set()
    for ep in episodes[:max_episodes]:
        if not isinstance(ep, dict):
            continue
        desc = ep.get("description_html", "") or ""
        for u in re.findall(r"href=['\"](https?://[^'\"]+)['\"]", desc):
            cleaned = u.strip().rstrip("/")
            if cleaned:
                urls.add(cleaned)
    return urls


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="The Guardian(메인) + NYT(보완) 8~10개 글로벌 기사를 큐레이션해 팟캐스트 RSS 및 텔레그램 브리핑으로 전송합니다."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="텔레그램으로 전송하지 않고 생성된 결과만 콘솔 출력 및 로컬 저장합니다.",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="수집할 최근 기사 시간 범위 (기본값: 24시간)",
    )
    parser.add_argument(
        "--with-audio",
        action="store_true",
        help="읽기용 텍스트 요약과 함께 오디오 브리핑도 생성합니다.",
    )
    parser.add_argument(
        "--audio-only",
        action="store_true",
        help="읽기용 텍스트 요약 없이 오디오 브리핑만 생성합니다.",
    )
    parser.add_argument(
        "--skip-telegram-text",
        action="store_true",
        help="텔레그램 텍스트 메시지 전송을 건너뜁니다.",
    )
    parser.add_argument(
        "--skip-telegram-audio",
        action="store_true",
        help="텔레그램 MP3 오디오 파일 전송을 건너뜁니다 (AntennaPod RSS 전용 배포 시 사용).",
    )
    parser.add_argument(
        "--podcast-dir",
        type=Path,
        default=None,
        help="AntennaPod RSS feed.xml과 MP3를 저장할 GitHub Pages 디렉터리",
    )
    parser.add_argument(
        "--podcast-base-url",
        type=str,
        default=None,
        help="GitHub Pages 공개 기본 URL (예: https://dndlr56.github.io/nyt-briefing)",
    )
    parser.add_argument(
        "--tts-provider",
        type=str,
        choices=("edge_tts", "google_cloud_tts", "gemini_tts"),
        default=None,
        help="사용할 TTS 엔진 (기본값: gemini_tts)",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default=None,
        help="앵커 목소리 이름 (기본값: Kore)",
    )
    parser.add_argument(
        "--tts-model",
        type=str,
        default=None,
        help="gemini_tts 선택 시 사용할 모델 (기본값: gemini-3.8-flash-lite-tts)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="생성된 대본과 MP3 오디오를 저장할 폴더 (기본값: output)",
    )
    args = parser.parse_args()

    publish_podcast = args.podcast_dir is not None
    if publish_podcast and not args.podcast_base_url:
        print("[ERROR] --podcast-dir 사용 시 --podcast-base-url도 함께 지정해야 합니다.", file=sys.stderr)
        return 1

    include_audio = args.with_audio or args.audio_only or publish_podcast
    # 팟캐스트 쇼노트(기사 요약 + 원문 링크)나 텔레그램 텍스트 중 하나라도 필요하면 읽기용 요약 생성
    need_text_summary = (not args.audio_only) or publish_podcast
    send_text_to_telegram = (not args.audio_only) and (not args.skip_telegram_text)
    send_audio_to_telegram = include_audio and (not args.skip_telegram_audio)

    print(f"[1/5] Fetching global article candidates (last {args.hours}h)...")
    candidates = _fetch_combined_candidates(hours=args.hours)
    if len(candidates) < 18 and args.hours < 36:
        print(f"      Only {len(candidates)} articles in last {args.hours}h; widening window to 36h...")
        candidates = _fetch_combined_candidates(hours=36)
    print(f"      Total candidate pool: {len(candidates)} articles.")

    if not candidates:
        print("[ERROR] No candidate articles fetched.", file=sys.stderr)
        return 1

    if args.podcast_dir is not None:
        covered_urls = _load_recent_covered_urls(args.podcast_dir, max_episodes=3)
        if covered_urls:
            before_cnt = len(candidates)
            filtered = [
                c for c in candidates if c.url.strip().rstrip("/") not in covered_urls
            ]
            if len(filtered) >= 12:
                candidates = filtered
                removed_cnt = before_cnt - len(candidates)
                if removed_cnt > 0:
                    print(f"      Excluded {removed_cnt} recently covered article(s) from candidate pool.")

    usage_tracker = RunUsageTracker()

    print("[2/5] Stage 1 — Selecting top 10~12 global stories with Gemini...")
    selected_pairs = select_articles(candidates, usage_tracker=usage_tracker)
    src_counts: dict[str, int] = {}
    for c, _ in selected_pairs:
        src_counts[c.source] = src_counts.get(c.source, 0) + 1
    src_summary = ", ".join(f"{k}: {v}" for k, v in src_counts.items())
    print(f"      Selected {len(selected_pairs)} stories ({src_summary}).")

    print("[3/5] Stage 2 — Loading full texts for Guardian stories & NYT summaries...")
    full_articles = fetch_article_full_texts(selected_pairs)
    print(f"      Loaded {len(full_articles)} articles.")

    if not full_articles:
        print("[ERROR] Failed to load selected articles.", file=sys.stderr)
        return 1

    briefing = None
    messages: list[str] = []
    script = None
    generated_audio = None
    date_slug = datetime.now(KST).strftime("%Y%m%d")

    if include_audio:
        args.output_dir.mkdir(parents=True, exist_ok=True)

        print("[4/5] Writing show-notes summary & 15~18 min briefing script (single pass)...")
        script = write_roundup_script(full_articles, usage_tracker=usage_tracker)
        if need_text_summary:
            briefing = build_daily_briefing_from_script(script, full_articles)
            messages = build_messages(briefing)
            print(f"      Prepared {len(messages)} reading-friendly message(s) from unified output.")

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
        usage_tracker.set_tts(
            provider=generated_audio.provider,
            voice=generated_audio.voice,
            chars=total_chars,
            model=generated_audio.model,
        )
        print(usage_tracker.format_console_summary())

        if publish_podcast and args.podcast_dir and args.podcast_base_url:
            feed_path = publish_podcast_episode(
                pages_dir=args.podcast_dir,
                base_url=args.podcast_base_url,
                mp3_path=generated_audio.mp3_path,
                episode_title=script.episode_title,
                duration_seconds=generated_audio.duration_seconds,
                date_slug=date_slug,
                briefing=briefing,
                usage_footer_html=usage_tracker.format_show_notes_html(),
            )
            print(f"      Updated AntennaPod RSS feed: {feed_path}")
    else:
        if need_text_summary:
            print("[4/5] Writing reading-friendly summary (text-only mode)...")
            briefing = summarize_full_articles(full_articles, usage_tracker=usage_tracker)
            messages = build_messages(briefing)
            print(f"      Prepared {len(messages)} reading-friendly message(s).")
        print(usage_tracker.format_console_summary())

    if args.dry_run:
        print("\n========== DRY RUN OUTPUT ==========")
        if messages:
            for idx, msg in enumerate(messages, start=1):
                print(f"\n--- Text Part {idx}/{len(messages)} ---\n{msg}")
        if script is not None:
            print(f"\n--- Audio Script ---\n{format_script_preview(script)}")
        if generated_audio is not None:
            print(f"\n--- Audio File ---\n{generated_audio.mp3_path.resolve()}")
        print("\n====================================")
        return 0

    print("[5/5] Delivering briefing...")
    if send_text_to_telegram and messages:
        send_telegram_messages(messages)

    if send_audio_to_telegram and generated_audio is not None and script is not None:
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

    print("[DONE] Global morning briefing completed successfully!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
