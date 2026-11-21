"""Deep Dive Knowledge Briefing 메인 파이프라인 CLI 엔트리포인트."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

# `python src/deep_dive/main.py` 실행 시 `src/` 모듈 임포트 보장
SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from audio_generator import (
    DEFAULT_DEEP_GEMINI_TTS_MODEL,
    DEFAULT_EDGE_TTS_VOICE,
    generate_dialogue_audio,
)
from deep_dive.curator import DEFAULT_CURATOR_MODEL, curate_candidates
from deep_dive.drive_archiver import archive_to_google_drive, is_drive_configured
from deep_dive.gemini_pool import GeminiClientPool
from deep_dive.models import CurationDecision, CuratedPick, FetchedDocument
from deep_dive.notifier import build_deep_dive_telegram_message, notify_deep_dive_telegram
from deep_dive.sources import (
    collect_candidates_for_theme,
    fetch_full_document,
    load_manual_source,
    search_document_by_title,
)
from deep_dive.themes import CORE_THEME_IDS, THEME_SLOTS, resolve_theme_slot
from deep_dive.web_publisher import (
    load_covered_urls_from_pages,
    publish_deep_dive,
    render_study_note_body_html,
)
from deep_dive.writer import (
    DEFAULT_NOTE_MODEL,
    DEFAULT_SCRIPT_MODEL,
    format_script_markdown,
    generate_longform_script,
    generate_study_note,
    resolve_adaptive_target_minutes,
)
from usage_tracker import RunUsageTracker


KST = timezone(timedelta(hours=9))
DEFAULT_DEEP_TTS_PROVIDER = "gemini_tts"  # 기본: gemini-3.8-flash-tts (2인 네이티브 대화 + 무료->유료 자동 전환)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deep Dive Knowledge Briefing 자동화 파이프라인"
    )
    parser.add_argument(
        "--theme",
        default="auto",
        choices=["auto", *THEME_SLOTS.keys()],
        help="테마 슬롯 선택 (auto=KST 요일별 로테이션)",
    )
    parser.add_argument(
        "--target-minutes",
        type=int,
        default=0,
        help="목표 오디오 브리핑 길이(분, 0=문헌 수·분량에 따라 25~45분 자동 조절, 직접 지정 시 12~60)",
    )
    parser.add_argument(
        "--source-url",
        default=None,
        help="자동 수집 대신 직접 분석할 특정 논문/기사/PDF URL",
    )
    parser.add_argument(
        "--search-title",
        default=None,
        help="OpenAlex·Europe PMC·Semantic Scholar에서 특정 논문/보고서 제목으로 먼저 검색 (미확보 시 일반 테마 선별로 폴백)",
    )
    parser.add_argument(
        "--pdf-path",
        type=Path,
        default=None,
        help="자동 수집 대신 직접 분석할 로컬 PDF 파일 경로",
    )
    parser.add_argument(
        "--curator-model",
        default=None,
        help=f"문헌 선별 모델 (기본: {DEFAULT_CURATOR_MODEL})",
    )
    parser.add_argument(
        "--note-model",
        default=None,
        help=f"구조화 노트 모델 (기본: {DEFAULT_NOTE_MODEL})",
    )
    parser.add_argument(
        "--script-model",
        default=None,
        help=f"롱폼 오디오 대본 모델 (기본: {DEFAULT_SCRIPT_MODEL})",
    )
    parser.add_argument(
        "--tts-provider",
        choices=["gemini_tts", "google_cloud_tts", "edge_tts"],
        default=DEFAULT_DEEP_TTS_PROVIDER,
        help=f"오디오 합성 TTS 엔진 (기본: {DEFAULT_DEEP_TTS_PROVIDER})",
    )
    parser.add_argument(
        "--tts-model",
        default=DEFAULT_DEEP_GEMINI_TTS_MODEL,
        help=f"gemini_tts 선택 시 모델 (기본: {DEFAULT_DEEP_GEMINI_TTS_MODEL})",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help=f"TTS 목소리 (edge_tts 기본: {DEFAULT_EDGE_TTS_VOICE})",
    )
    parser.add_argument(
        "--inter-call-sleep",
        type=float,
        default=None,
        help="챕터 대본 생성 호출 간 대기 초 (미지정 시 free=22초, paid=2초)",
    )
    parser.add_argument(
        "--podcast-dir",
        type=Path,
        default=None,
        help="웹 리더 및 독립 RSS 배포 디렉터리 (예: _gh_pages/deep)",
    )
    parser.add_argument(
        "--podcast-base-url",
        default="",
        help="웹 리더/RSS 공개 기본 URL (예: https://dndlr56.github.io/nyt-briefing/deep)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/deep_dive"),
        help="로컬 산출물(노트/대본/MP3/원문) 저장 디렉터리",
    )
    parser.add_argument(
        "--dry-run-fetch",
        action="store_true",
        help="Gemini/TTS 호출 없이 소스 수집 및 전문 추출만 테스트",
    )
    parser.add_argument(
        "--skip-audio",
        action="store_true",
        help="구조화 노트만 생성하고 오디오(대본/MP3) 단계 생략",
    )
    parser.add_argument(
        "--skip-drive",
        action="store_true",
        help="Google Drive 3단 아카이빙 생략",
    )
    parser.add_argument(
        "--skip-telegram",
        action="store_true",
        help="텔레그램 알림 발송 생략",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = _parse_args()

    now_kst = datetime.now(KST)
    date_slug = now_kst.strftime("%Y-%m-%d")
    theme = resolve_theme_slot(args.theme, now_kst=now_kst)

    gemini_pool = GeminiClientPool()
    initial_tier = gemini_pool.active_tier_for(DEFAULT_NOTE_MODEL)
    usage_tracker = RunUsageTracker(api_tier=initial_tier)

    print(f"=== Deep Dive Knowledge Briefing ({date_slug} KST) ===")
    print(
        f"테마: {theme.emoji} {theme.label_kr} ({theme.theme_id}) | 목표 분량: ~{args.target_minutes}분 | "
        f"API 모드: {gemini_pool.mode} (무료키={'Y' if gemini_pool.has_free_key else 'N'}, "
        f"유료폴백={'Y' if gemini_pool.has_paid_fallback else 'N'})"
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    covered_urls = (
        load_covered_urls_from_pages(args.podcast_dir)
        if args.podcast_dir is not None
        else set()
    )

    # ── Step 1 & 2: 문헌 수집 + 선별 + 전문(Full-text) 확보 ──
    selected_docs: list[FetchedDocument] = []
    decision: CurationDecision | None = None

    if args.source_url or args.pdf_path:
        print("\n[1/6] 수동 지정 원문 로드 중...")
        doc = load_manual_source(
            source_url=args.source_url,
            pdf_path=args.pdf_path,
            theme_id=theme.theme_id,
        )
        selected_docs = [doc]
        decision = CurationDecision(
            mode="single_deep_dive",
            unifying_angle_kr="사용자 직접 지정 원문의 핵심 논증과 실질적 시사점 충실 요약",
            sub_subject_kr=theme.label_kr,
            picks=[CuratedPick(candidate_index=1, why_selected_kr="사용자 직접 지정 문헌")],
        )
        print(
            f"   -> 로드 완료: '{doc.candidate.title}' "
            f"({doc.text_source}, {doc.char_count:,}자, PDF={'있음' if doc.pdf_bytes else '없음'})"
        )
    elif args.search_title:
        print("\n[1/6] 지정 제목으로 개방형 학술 DB 탐색 중...")
        found_doc = search_document_by_title(args.search_title, theme_id=theme.theme_id)
        if found_doc is not None:
            selected_docs = [found_doc]
            decision = CurationDecision(
                mode="single_deep_dive",
                unifying_angle_kr=f"지정 핵심 문헌 '{found_doc.candidate.title}'의 주요 근거·방법론·결론 충실 요약",
                sub_subject_kr=theme.label_kr,
                picks=[CuratedPick(candidate_index=1, why_selected_kr="사용자 지정 제목 DB 검색 확보")],
            )
            print(
                f"   -> DB 확보 성공: '{found_doc.candidate.title}' "
                f"({found_doc.text_source}, {found_doc.char_count:,}자, PDF={'있음' if found_doc.pdf_bytes else '없음'})"
            )

    if not selected_docs:
        print("\n[1/6] 1차 소스 후보 수집 중...")
        candidates = collect_candidates_for_theme(theme)
        print(f"   -> 총 {len(candidates)}건 수집 완료 (기다룬 URL {len(covered_urls)}건 제외 예정)")

        if args.dry_run_fetch:
            print("\n[DRY-RUN] 상위 5건 전문 추출 테스트 실행...")
            ok_count = 0
            for idx, cand in enumerate(candidates[:8], start=1):
                doc = fetch_full_document(cand)
                status = (
                    f"OK ({doc.text_source}, {doc.char_count:,}자, PDF={'Y' if doc.pdf_bytes else 'N'})"
                    if doc
                    else "SKIP (페이월/본문 부족)"
                )
                print(f"   [{idx}] {cand.source_name} | {cand.title[:65]} -> {status}")
                if doc:
                    ok_count += 1
                    if ok_count >= 3:
                        break
            print(f"\n[DRY-RUN 완료] 전문 추출 성공: {ok_count}건")
            return 0

        print("\n[2/6] 문헌 선별 및 전문(Full-text) 추출 중...")
        decision = curate_candidates(
            candidates=candidates,
            theme=theme,
            covered_urls=covered_urls,
            model=args.curator_model,
            usage_tracker=usage_tracker,
            gemini_pool=gemini_pool,
        )
        fresh_pool = [
            c for c in candidates if c.url.strip().rstrip("/") not in covered_urls
        ] or candidates
        pool = fresh_pool[:35]

        print(
            f"   -> 선별 모드: {decision.mode} | 세부 주제: {decision.sub_subject_kr}\n"
            f"   -> 관통 질문: {decision.unifying_angle_kr}"
        )

        # 선별된 후보들의 전문 확보 시도
        tried_indices: set[int] = set()
        for pick in decision.picks:
            tried_indices.add(pick.candidate_index)
            cand = pool[pick.candidate_index - 1]
            print(f"   • 전문 추출 시도: [{cand.source_name}] {cand.title[:70]}")
            doc = fetch_full_document(cand)
            if doc is not None:
                selected_docs.append(doc)
                print(
                    f"     ✓ 확보 성공 ({doc.text_source}, {doc.char_count:,}자, "
                    f"PDF={'있음' if doc.pdf_bytes else '없음'})"
                )
            else:
                print("     ✗ 유료 장벽 또는 본문 부족으로 제외")

        # 1순위 문헌이 모두 페이월/403으로 실패 시:
        # (1) Curator가 미리 선정한 2~3순위 backup_picks를 우선 시도하며 주제·관통 질문을 동기화
        if not selected_docs and decision.backup_picks:
            print("   [WARN] 1순위 선별 문헌의 전문 확보 실패 -> AI 추천 2~3순위 백업 문헌 순차 시도...")
            for rank, bp in enumerate(decision.backup_picks, start=2):
                if bp.candidate_index in tried_indices or not (1 <= bp.candidate_index <= len(pool)):
                    continue
                tried_indices.add(bp.candidate_index)
                cand = pool[bp.candidate_index - 1]
                print(f"   • {rank}순위 백업 시도: [{cand.source_name}] {cand.title[:70]}")
                doc = fetch_full_document(cand)
                if doc is not None:
                    selected_docs.append(doc)
                    decision.mode = "single_deep_dive"
                    if bp.sub_subject_kr.strip():
                        decision.sub_subject_kr = bp.sub_subject_kr.strip()
                    if bp.unifying_angle_kr.strip():
                        decision.unifying_angle_kr = bp.unifying_angle_kr.strip()
                    print(
                        f"     ✓ {rank}순위 백업 확보: [{cand.source_name}] {cand.title[:70]} "
                        f"({doc.text_source}, {doc.char_count:,}자 | 세부주제: {decision.sub_subject_kr})"
                    )
                    break
                print(f"     ✗ {rank}순위 백업도 유료 장벽/403으로 제외")

        # (2) 추천 백업까지 모두 실패 시, 최후 수단으로 PDF/PMC OA 우선 백업 후보에서 자동 보충 + 메타데이터 초기화
        if not selected_docs:
            print("   [WARN] 추천 백업 문헌도 전문 확보 실패 -> 오픈액세스 자동 백업 후보 탐색...")
            prioritized = sorted(
                (
                    (idx, c)
                    for idx, c in enumerate(pool, start=1)
                    if idx not in tried_indices
                ),
                key=lambda pair: (0 if (pair[1].pmc_id or pair[1].pdf_url) else 1, pair[0]),
            )
            for _, cand in prioritized[:10]:
                doc = fetch_full_document(cand)
                if doc is not None:
                    selected_docs.append(doc)
                    decision.mode = "single_deep_dive"
                    decision.sub_subject_kr = cand.sub_topic_hint or theme.label_kr
                    decision.unifying_angle_kr = f"'{cand.title}'의 핵심 논증, 근거 및 결론 충실 요약"
                    print(
                        f"     ✓ 자동 백업 확보: [{cand.source_name}] {cand.title[:70]} "
                        f"(세부주제 재설정: {decision.sub_subject_kr})"
                    )
                    break

        if not selected_docs:
            raise RuntimeError("전문(Full-text)을 확보할 수 있는 문헌을 찾지 못했습니다.")

    assert decision is not None
    if args.dry_run_fetch:
        print("[DRY-RUN 완료]")
        return 0

    # ── Step 3: Stage 1 구조화 요약 노트 + 5대 분야 분류 + 대화 캐스팅 설계 ──
    print("\n[3/6] Stage 1: 원문 충실 요약 노트·아카이브 분류·대화 캐스팅 생성 중...")
    note = generate_study_note(
        docs=selected_docs,
        theme=theme,
        decision=decision,
        model=args.note_model,
        usage_tracker=usage_tracker,
        gemini_pool=gemini_pool,
    )

    if (
        theme.theme_id not in CORE_THEME_IDS
        or args.search_title
        or args.source_url
        or args.pdf_path
    ):
        display_theme = THEME_SLOTS.get(note.primary_theme_id, THEME_SLOTS["science_mind"])
    else:
        display_theme = theme

    if decision.sub_subject_kr == theme.label_kr and (
        theme.theme_id not in CORE_THEME_IDS or args.search_title or args.source_url or args.pdf_path
    ):
        decision.sub_subject_kr = "지정 문헌 심층 요약" if (args.search_title or args.source_url or args.pdf_path) else display_theme.label_kr

    cast_speakers_str = ", ".join(
        f"{sp.speaker_id}:{sp.name_kr}({sp.role_title_kr})" for sp in note.dialogue_cast.speakers
    )
    print(f"   -> 제목: {note.episode_title_kr}")
    print(f"   -> 배정 아카이브 탭: {display_theme.emoji} {display_theme.label_kr} ({display_theme.theme_id})")
    print(f"   -> 대화 캐스팅: [{note.dialogue_cast.format_type}] {cast_speakers_str}")
    print(f"      선정 이유: {note.dialogue_cast.format_reason_kr}")
    print(f"   -> 본문 섹션: {len(note.body_sections)}개 | 챕터: {len(note.chapters)}개 | 인용: {len(note.key_quotes)}개")

    note_json_path = args.output_dir / f"study_note_{date_slug}_{display_theme.theme_id}.json"
    note_json_path.write_text(note.model_dump_json(indent=2), encoding="utf-8")

    # ── Step 4: Stage 2 다중 화자 대화형 오디오 대본 + TTS 합성 ──
    script = None
    generated_audio = None
    mp3_path: Path | None = None
    duration_seconds = 0

    if not args.skip_audio:
        resolved_minutes = resolve_adaptive_target_minutes(
            selected_docs, requested_minutes=args.target_minutes
        )
        mode_note = "자동 조절" if args.target_minutes <= 0 else "수동 지정"
        print(
            f"\n[4/6] Stage 2: {len(note.dialogue_cast.speakers)}인 대화형 오디오 대본 생성 중 "
            f"(포맷={note.dialogue_cast.format_type}, 목표 ~{resolved_minutes}분 [{mode_note}])..."
        )
        script = generate_longform_script(
            docs=selected_docs,
            theme=display_theme,
            decision=decision,
            note=note,
            target_minutes=resolved_minutes,
            model=args.script_model,
            inter_call_sleep=args.inter_call_sleep,
            usage_tracker=usage_tracker,
            gemini_pool=gemini_pool,
        )
        script_md_path = args.output_dir / f"script_{date_slug}_{display_theme.theme_id}.md"
        script_md_path.write_text(format_script_markdown(script, note), encoding="utf-8")
        print(
            f"   -> 대본 완성: 총 {script.total_chars:,}자 / {script.total_turns}턴 ({script_md_path})"
        )

        mp3_path = args.output_dir / f"deep_dive_{date_slug}_{display_theme.theme_id}.mp3"
        print(
            f"\n   -> 다중 화자 TTS 음성 합성 시작 "
            f"(provider={args.tts_provider}, model={args.tts_model})..."
        )
        generated_audio = generate_dialogue_audio(
            dialogue_items=script.to_dialogue_items(),
            output_mp3=mp3_path,
            tts_provider=args.tts_provider,
            tts_model=args.tts_model,
            gemini_pool=gemini_pool,
            usage_tracker=usage_tracker,
        )
        duration_seconds = generated_audio.duration_seconds
        usage_tracker.set_tts(
            provider=generated_audio.provider,
            model=generated_audio.model,
            voice=generated_audio.voice,
            chars=script.total_chars,
            tier=gemini_pool.active_tier_for(args.tts_model),
        )
        print(
            f"   -> MP3 완성: {mp3_path} "
            f"({duration_seconds // 60}분 {duration_seconds % 60}초, "
            f"{generated_audio.file_size_bytes / (1024 * 1024):.1f} MB, "
            f"엔진={generated_audio.provider}/{generated_audio.model}, 화자={generated_audio.voice})"
        )
    else:
        print("\n[4/6] --skip-audio 지정으로 오디오 단계 생략")

    # 로컬 아카이빙 백업(PDF + full_text.txt)
    for idx, d in enumerate(selected_docs, start=1):
        if d.pdf_bytes:
            (args.output_dir / (d.pdf_filename or f"source_{idx}.pdf")).write_bytes(d.pdf_bytes)
    (args.output_dir / f"full_text_{date_slug}_{display_theme.theme_id}.txt").write_text(
        "\n\n".join(f"=== {d.candidate.title} ===\n{d.full_text}" for d in selected_docs),
        encoding="utf-8",
    )

    usage_footer_html = usage_tracker.format_show_notes_html()
    usage_console_text = usage_tracker.format_console_summary()

    # ── Step 5: Google Drive 3단 아카이빙 + 웹 리더/RSS 발행 ──
    print("\n[5/6] 배포 및 Google Drive 아카이빙 중...")
    published = None
    drive_result = None

    # 웹 리더 선행 생성 (Drive Sheet에 웹 리더 링크를 함께 기록하기 위해)
    if args.podcast_dir is not None and args.podcast_base_url:
        published = publish_deep_dive(
            deep_dir=args.podcast_dir,
            base_url=args.podcast_base_url,
            date_slug=date_slug,
            theme=display_theme,
            decision=decision,
            note=note,
            docs=selected_docs,
            script=script,
            mp3_path=mp3_path,
            duration_seconds=duration_seconds,
            usage_footer_html=usage_footer_html,
        )
        print(f"   -> 웹 리더 발행: {published.article_url}")
        print(f"   -> 독립 RSS 갱신: {published.feed_url}")

    if not args.skip_drive and is_drive_configured():
        try:
            doc_html = render_study_note_body_html(
                note=note,
                docs=selected_docs,
                theme=display_theme,
                decision=decision,
                article_url=published.article_url if published else None,
                usage_footer_html=usage_footer_html,
            )
            script_md = format_script_markdown(script, note) if script is not None else ""
            drive_result = archive_to_google_drive(
                date_slug=date_slug,
                theme=display_theme,
                decision=decision,
                note=note,
                docs=selected_docs,
                doc_html=doc_html,
                web_article_url=published.article_url if published else "",
                script_markdown=script_md,
            )
            print(f"   -> Google Drive 아카이빙 완료: {drive_result.folder_url}")
            print(
                f"      요약 PDF + 마크다운(.md"
                + (" + _대본.md" if script_md else "")
                + f") | 원문 PDF {drive_result.uploaded_pdf_count}건"
            )
            print(f"      Master Sheet: {drive_result.sheet_url}")
        except Exception as exc:
            print(f"   [WARN] Google Drive 아카이빙 실패 (파이프라인은 계속 진행): {exc}")
    elif not args.skip_drive:
        print("   -> Google Drive OAuth 시크릿 미설정으로 Drive 아카이빙 생략 (docs/DEEP_DIVE_SETUP.md 참고)")

    # ── Step 6: 텔레그램 알림 & 토큰 리포트 ──
    print("\n[6/6] 텔레그램 알림 및 사용량 리포트 출력...")
    print(usage_console_text)

    if not args.skip_telegram:
        if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
            tg_msg = build_deep_dive_telegram_message(
                date_slug=date_slug,
                theme=display_theme,
                decision=decision,
                note=note,
                docs=selected_docs,
                published=published,
                drive_result=drive_result,
                duration_seconds=duration_seconds,
                usage_summary_text=usage_console_text,
            )
            notify_deep_dive_telegram(tg_msg)
        else:
            print("   -> TELEGRAM_BOT_TOKEN/CHAT_ID 미설정으로 알림 생략")

    # 벤치마크 메타 JSON 저장
    bench_summary = {
        "date_slug": date_slug,
        "source_slot": theme.theme_id,
        "theme": display_theme.theme_id,
        "mode": decision.mode,
        "title": note.episode_title_kr,
        "dialogue_format": note.dialogue_cast.format_type,
        "speaker_count": len(note.dialogue_cast.speakers),
        "total_turns": script.total_turns if script else 0,
        "tts_provider": generated_audio.provider if generated_audio else "",
        "tts_model": generated_audio.model if generated_audio else "",
        "docs": [
            {
                "title": d.candidate.title,
                "source": d.candidate.source_name,
                "text_source": d.text_source,
                "char_count": d.char_count,
                "has_pdf": d.pdf_bytes is not None,
            }
            for d in selected_docs
        ],
        "script_chars": script.total_chars if script else 0,
        "duration_seconds": duration_seconds,
        "total_tokens": usage_tracker.total_tokens,
        "cached_tokens": usage_tracker.total_cached_tokens,
        "actual_paid_usd": round(usage_tracker.actual_paid_usd(), 4),
        "estimated_usd": round(usage_tracker.estimate_usd(), 4),
        "failover_events": gemini_pool.failover_events,
    }
    (args.output_dir / f"run_summary_{date_slug}_{display_theme.theme_id}.json").write_text(
        json.dumps(bench_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n✅ Deep Dive Knowledge Briefing 파이프라인 완료!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
