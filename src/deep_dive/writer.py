"""2단계 API 생성기: (1) 원문 충실 번역·구조화 요약 노트 생성 → (2) 챕터 분할 롱폼 오디오 대본 생성."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import time

from google import genai

from deep_dive.models import (
    ChapterNarrationOutput,
    CurationDecision,
    DeepDiveScript,
    DeepDiveStudyNote,
    FetchedDocument,
)
from deep_dive.themes import ThemeSlot
from usage_tracker import RunUsageTracker


KST = timezone(timedelta(hours=9))
DEFAULT_NOTE_MODEL = "gemini-4-hydrogen"
DEFAULT_SCRIPT_MODEL = "gemini-4-hydrogen"
# gemini-4-hydrogen 공식 지원 추론 레벨: low | medium | high (기본) | xhigh  (minimal 미지원 -> 400 에러)
DEFAULT_NOTE_THINKING = "high"       # Stage 1: 원문 장문 정독·정확한 번역·구조화 요약 (1회 호출)
DEFAULT_CHAPTER_THINKING = "low"     # Stage 2: 챕터 설계도 기반 자연스러운 구어체 전달
DEFAULT_BOOKEND_THINKING = "low"     # Stage 2: 오프닝/클로징 (Hydrogen 최저 레벨 = low)
DEFAULT_CHARS_PER_MINUTE = 420  # 한국어 낭독 속도 기준 약 410~430자/분


def sanitize_thinking_level(model: str, level: str) -> str:
    """모델별 공식 지원 thinking_level로 안전 보정합니다 (Gemini 4 계열의 minimal 400 에러 방지)."""
    cleaned = (level or "low").strip().lower()
    is_g4 = model.startswith("gemini-4")
    is_g38 = model.startswith("gemini-3.8") or model.startswith("gemini-3.7")
    if cleaned == "minimal" and (is_g4 or is_g38):
        return "low"
    if cleaned == "xhigh" and not (
        model.startswith("gemini-4-hydrogen") or model.startswith("gemini-4-neon")
    ):
        return "high"
    if cleaned not in ("minimal", "low", "medium", "high", "xhigh"):
        return "low"
    return cleaned


STUDY_NOTE_SYSTEM_INSTRUCTION = """당신은 학술 논문·장문 보고서·판례·칼럼의 내용을 왜곡이나 과장 없이 **자연스러운 한국어로 충실하게 번역·요약하는 전문 학술 번역가 겸 편집자**입니다.

[작성 절대 원칙]
1. **충실한 번역 및 요약 (No Over-processing)**:
   - 원문을 임의로 재가공하거나 억지로 교훈·적용점을 만들어내지 마세요.
   - 독자가 원문을 직접 읽는 것처럼 **본문의 논지, 구체적 수치, 연구 설계, 근거, 저자의 결론을 빠짐없이 깔끔하게 전달**하는 것이 최우선 목표입니다.
2. **자연스러운 한국어 문장 (불필요한 영어 병기 금지)**:
   - 일반적인 학술 개념이나 한국어로 충분히 뜻이 통하는 표현 뒤에 괄호로 영어를 덧붙이지 마세요 (예: `인지 예비력(cognitive reserve)`, `유병률(prevalence)`, `사법심사(judicial review)` 같은 불필요한 병기 금지 → 그냥 `인지 예비력`, `유병률`, `사법심사`로 자연스럽게 서술).
   - 한국어 글에서도 그대로 통용되는 고유명사·약어·생물학 표기(`LDL`, `GLP-1`, `APOE ε4`, `MRI`, `LLM`, 인명·기관명 등)만 예외적으로 그대로 사용하세요.
3. **엄격한 텍스트 근거주의 (AI 자의적 판단·추론 금지)**:
   - 원문에 없는 수치, 인용, 해석을 절대 지어내지 마세요.
   - 한계(`author_stated_limitations_kr`)와 결론·시사점(`author_conclusions_kr`) 역시 **반드시 원문(저자·보고서·법원·반대의견 등)에 명시된 근거와 서술에 기반**해 작성하세요. AI가 자기 마음대로 비평하거나, 독자의 진로·투자 등에 억지로 연결해 추측하는 문장은 쓰지 마세요.
4. **본문 중심 구성 (`body_sections`)**:
   - 원문의 핵심 장·주제 흐름을 따라 3~6개의 소제목으로 나누고, 각 섹션마다 원문의 실질적 내용·근거·수치를 밀도 높게 번역·요약하세요.
5. **핵심 원문 인용 (`key_quotes`)**:
   - 원문에서 실제로 등장한 핵심 문장 2~3개를 정확히 발췌하고 자연스러운 한국어 번역을 함께 제시하세요.
"""


CHAPTER_SCRIPT_SYSTEM_INSTRUCTION = """당신은 권위 있는 학술 논문·보고서·판례의 내용을 **귀로 듣기 편하고 자연스러운 한국어**로 충실하게 풀어 전달하는 지식 브리핑 진행자입니다.

[발화 스타일 및 품질 규칙]
1. **순수 발화용 한국어 텍스트만 작성**: 마크다운(`**`, `##`, `-`), 번호 매기기, URL, 이모지, 무대 지시문(`(잠시 멈춤)`)을 넣지 마세요.
2. **불필요한 영어 단어 병기·읽어주기 금지**: "영어로는 ~라고 하는데요" 같은 번거로운 영문 병기를 피하고, 자연스러운 우리말로 매끄럽게 설명하세요. (`LDL`, `LLM`, 고유명사 등 한국어에서도 그대로 쓰는 약어·명칭만 자연스럽게 발음)
3. **원문 충실 전달 (AI 임의 추론 금지)**:
   - 원문에 담긴 핵심 사실, 수치, 연구 구조, 근거, 그리고 저자(또는 법원)가 직접 밝힌 한계와 결론을 빠짐없이 논리적으로 전달하세요.
   - 원문에 없는 AI의 개인적 논평이나 과도한 일반화·추측은 넣지 마세요.
4. **이전 챕터와의 자연스러운 연결**: 직전 챕터 끝부분을 받아 자연스럽게 이어가되, 앞선 내용을 중복 반복하지 마세요.
5. **목표 글자 수 준수**: 프롬프트에 제시된 목표 글자 수 범위를 충실히 채워 원문의 풍부한 디테일을 담아내세요.
"""


def _build_documents_context(docs: list[FetchedDocument]) -> str:
    """여러 문헌의 전문을 프롬프트 투입용 블록으로 포맷합니다."""
    blocks: list[str] = []
    per_doc_limit = 65_000 if len(docs) == 1 else max(22_000, 72_000 // len(docs))

    for idx, doc in enumerate(docs, start=1):
        c = doc.candidate
        body = doc.full_text
        if len(body) > per_doc_limit:
            head = int(per_doc_limit * 0.75)
            tail = per_doc_limit - head - 60
            body = body[:head] + "\n\n[... 중간 세부 생략 ...]\n\n" + body[-tail:]
        blocks.append(
            f"=== 문헌 {idx} ===\n"
            f"제목: {c.title}\n"
            f"출처: {c.source_name} ({c.source_kind})\n"
            f"저자: {c.authors or '미상'}\n"
            f"발행일: {c.published or '미상'}\n"
            f"URL: {c.url}\n"
            f"전문 확보 방식: {doc.text_source} ({len(body)}자 투입)\n"
            f"--- 원문 텍스트 시작 ---\n{body}\n--- 원문 텍스트 끝 ---"
        )
    return "\n\n".join(blocks)


def _call_interactions_with_retry(
    client: genai.Client,
    model: str,
    system_instruction: str,
    prompt: str,
    schema: dict,
    max_output_tokens: int,
    thinking_level: str,
    stage: str,
    usage_tracker: RunUsageTracker | None = None,
) -> str:
    safe_thinking = sanitize_thinking_level(model, thinking_level)
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            interaction = client.interactions.create(
                model=model,
                system_instruction=system_instruction,
                input=prompt,
                response_mime_type="application/json",
                response_format=schema,
                generation_config={
                    "thinking_level": safe_thinking,
                    "max_output_tokens": max_output_tokens,
                },
                store=False,
            )
            if usage_tracker is not None:
                usage_tracker.add_interaction(interaction, stage=stage, model=model)

            raw = interaction.output_text
            if raw:
                return raw
            last_exc = RuntimeError(f"{stage}: Gemini API 빈 응답")
        except Exception as exc:
            last_exc = exc

        if attempt < 3:
            msg = str(last_exc).lower()
            is_rate = "429" in msg or "resource_exhausted" in msg or "rate" in msg or "quota" in msg
            wait_sec = 30 * attempt if is_rate else 5 * attempt
            print(f"      [WARN] {stage} failed (attempt {attempt}/3): {last_exc}. Retrying in {wait_sec}s...")
            time.sleep(wait_sec)

    raise RuntimeError(f"{stage} 3회 실패: {last_exc}")


def generate_study_note(
    docs: list[FetchedDocument],
    theme: ThemeSlot,
    decision: CurationDecision,
    model: str | None = None,
    usage_tracker: RunUsageTracker | None = None,
) -> DeepDiveStudyNote:
    """Stage 1: 원문 전문을 자연스러운 한국어로 충실하게 번역·구조화 요약하고 챕터 설계도를 생성합니다."""
    selected_model = model or os.getenv("DEEP_NOTE_MODEL", DEFAULT_NOTE_MODEL)
    note_thinking = sanitize_thinking_level(
        selected_model, os.getenv("DEEP_NOTE_THINKING", DEFAULT_NOTE_THINKING)
    )
    client = genai.Client(http_options={"api_version": "v1beta"})

    today_kst = datetime.now(KST).strftime("%Y년 %m월 %d일")
    mode_label = (
        "단일 문헌 충실 요약 (Single Deep Dive)"
        if decision.mode == "single_deep_dive"
        else f"복수 문헌 종합 요약 ({len(docs)}편 Multi-Synthesis)"
    )

    prompt = (
        f"오늘 날짜(KST): {today_kst}\n"
        f"테마: {theme.emoji} {theme.label_kr} | 세부 주제: {decision.sub_subject_kr}\n"
        f"구성 방식: {mode_label}\n"
        f"핵심 주제: {decision.unifying_angle_kr}\n\n"
        "아래 원문 텍스트의 내용을 빠짐없이 충실하게 번역·요약해 JSON으로 작성해 주세요. "
        "불필요한 영문 괄호 병기와 본문에 없는 AI 자의적 추론·비평은 넣지 마세요.\n\n"
        + _build_documents_context(docs)
    )

    raw_json = _call_interactions_with_retry(
        client=client,
        model=selected_model,
        system_instruction=STUDY_NOTE_SYSTEM_INSTRUCTION,
        prompt=prompt,
        schema=DeepDiveStudyNote.model_json_schema(),
        max_output_tokens=32768,
        thinking_level=note_thinking,
        stage="원문 번역·구조화 요약",
        usage_tracker=usage_tracker,
    )
    return DeepDiveStudyNote.model_validate_json(raw_json)


def generate_longform_script(
    docs: list[FetchedDocument],
    theme: ThemeSlot,
    decision: CurationDecision,
    note: DeepDiveStudyNote,
    target_minutes: int = 35,
    model: str | None = None,
    inter_call_sleep: float | None = None,
    usage_tracker: RunUsageTracker | None = None,
) -> DeepDiveScript:
    """
    Stage 2: 챕터 설계도를 바탕으로 오프닝 → 각 챕터 → 클로징을 분할 생성해
    원문 내용을 자연스러운 한국어 구어체로 충실히 전달하는 롱폼 오디오 대본을 조립합니다.
    """
    selected_model = model or os.getenv("DEEP_SCRIPT_MODEL", DEFAULT_SCRIPT_MODEL)
    chapter_thinking = sanitize_thinking_level(
        selected_model, os.getenv("DEEP_CHAPTER_THINKING", DEFAULT_CHAPTER_THINKING)
    )
    bookend_thinking = sanitize_thinking_level(
        selected_model, os.getenv("DEEP_BOOKEND_THINKING", DEFAULT_BOOKEND_THINKING)
    )
    api_tier = (os.getenv("GEMINI_API_TIER") or "free").strip().lower()
    sleep_sec = (
        inter_call_sleep
        if inter_call_sleep is not None
        else (22.0 if api_tier == "free" else 2.0)
    )

    clamped_minutes = max(12, min(60, int(target_minutes)))
    total_target_chars = clamped_minutes * DEFAULT_CHARS_PER_MINUTE
    opening_target = 500
    closing_target = 400
    chapter_count = max(1, len(note.chapters))
    per_chapter_target = max(1200, (total_target_chars - opening_target - closing_target) // chapter_count)

    client = genai.Client(http_options={"api_version": "v1beta"})
    docs_context = _build_documents_context(docs)
    today_title_date = datetime.now(KST).strftime("%Y.%m.%d")
    episode_title = f"[{theme.label_kr}] {note.episode_title_kr} | {today_title_date}"

    chapter_toc = "\n".join(
        f"- Chapter {ch.chapter_number}. {ch.title_kr}: {ch.focus_kr}"
        for ch in note.chapters
    )

    # 1) 오프닝 생성
    print(f"      [Script] Opening 생성 중 (목표 ~{opening_target}자, thinking={bookend_thinking})...")
    opening_prompt = (
        f"에피소드 제목: {episode_title}\n"
        f"부제: {note.subtitle_kr}\n"
        f"원문 개요: {note.overview_and_purpose_kr}\n"
        f"전체 챕터 구성:\n{chapter_toc}\n\n"
        f"원문의 출처와 주제를 간결하고 정확하게 소개하고, 앞으로 어떤 순서로 내용을 살펴볼지 안내하는 "
        f"자연스러운 한국어 오프닝을 공백 포함 약 {opening_target - 80}~{opening_target + 100}자로 작성하세요."
    )
    raw_open = _call_interactions_with_retry(
        client=client,
        model=selected_model,
        system_instruction=CHAPTER_SCRIPT_SYSTEM_INSTRUCTION,
        prompt=opening_prompt,
        schema=ChapterNarrationOutput.model_json_schema(),
        max_output_tokens=4096,
        thinking_level=bookend_thinking,
        stage="대본 Opening",
        usage_tracker=usage_tracker,
    )
    opening_text = ChapterNarrationOutput.model_validate_json(raw_open).narration.strip()

    # 2) 챕터별 순차 생성
    rendered_chapters: list[tuple[str, str]] = []
    prev_tail = opening_text[-350:]

    for idx, ch in enumerate(note.chapters, start=1):
        if sleep_sec > 0:
            print(f"      [Script] RPM/TPM pacing: waiting {sleep_sec:.0f}s before Chapter {idx}...")
            time.sleep(sleep_sec)

        min_c = int(per_chapter_target * 0.85)
        max_c = int(per_chapter_target * 1.15)
        print(
            f"      [Script] Chapter {idx}/{chapter_count} '{ch.title_kr}' 생성 중 "
            f"(목표 {min_c}~{max_c}자, thinking={chapter_thinking})..."
        )
        points_str = "\n".join(f"- {p}" for p in ch.key_points)
        chapter_prompt = (
            f"에피소드: {episode_title}\n"
            f"전체 챕터 목차:\n{chapter_toc}\n\n"
            f"=== 지금 작성할 챕터 ({idx}/{chapter_count}) ===\n"
            f"소제목: {ch.title_kr}\n"
            f"전달할 흐름: {ch.focus_kr}\n"
            f"반드시 포함할 원문 근거·수치:\n{points_str}\n\n"
            f"직전 섹션의 마지막 발화(자연스럽게 이어갈 것, 반복 금지):\n\"...{prev_tail}\"\n\n"
            f"목표 분량: 공백 포함 **약 {min_c}~{max_c}자**.\n"
            "아래 원문 텍스트에 충실하게 근거해, 불필요한 영어 병기나 임의 추측 없이 "
            "원문의 논지와 근거를 자연스러운 한국어 구어체로 풀어내는 순수 발화 대본을 JSON으로 작성해 주세요.\n\n"
            + docs_context
        )
        raw_ch = _call_interactions_with_retry(
            client=client,
            model=selected_model,
            system_instruction=CHAPTER_SCRIPT_SYSTEM_INSTRUCTION,
            prompt=chapter_prompt,
            schema=ChapterNarrationOutput.model_json_schema(),
            max_output_tokens=12288,
            thinking_level=chapter_thinking,
            stage=f"대본 Chapter {idx}",
            usage_tracker=usage_tracker,
        )
        ch_text = ChapterNarrationOutput.model_validate_json(raw_ch).narration.strip()
        rendered_chapters.append((ch.title_kr, ch_text))
        prev_tail = ch_text[-350:]

    # 3) 클로징 생성
    if sleep_sec > 0:
        time.sleep(min(sleep_sec, 10.0))
    print(f"      [Script] Closing 생성 중 (목표 ~{closing_target}자, thinking={bookend_thinking})...")
    conclusions_str = "\n".join(f"- {c}" for c in note.author_conclusions_kr)
    closing_prompt = (
        f"에피소드: {episode_title}\n"
        f"원문의 최종 결론 및 제언:\n{conclusions_str}\n"
        f"직전 챕터의 마지막 발화:\n\"...{prev_tail}\"\n\n"
        f"원문이 제시한 핵심 결론을 차분하게 정리하며 마무리하는 클로징을 "
        f"공백 포함 약 {closing_target - 80}~{closing_target + 100}자로 작성해 주세요."
    )
    raw_close = _call_interactions_with_retry(
        client=client,
        model=selected_model,
        system_instruction=CHAPTER_SCRIPT_SYSTEM_INSTRUCTION,
        prompt=closing_prompt,
        schema=ChapterNarrationOutput.model_json_schema(),
        max_output_tokens=4096,
        thinking_level=bookend_thinking,
        stage="대본 Closing",
        usage_tracker=usage_tracker,
    )
    closing_text = ChapterNarrationOutput.model_validate_json(raw_close).narration.strip()

    return DeepDiveScript(
        episode_title=episode_title,
        opening=opening_text,
        chapters=rendered_chapters,
        closing=closing_text,
    )


def format_script_markdown(script: DeepDiveScript, note: DeepDiveStudyNote) -> str:
    """대본과 메타정보를 읽기 좋은 마크다운 문서로 포맷합니다."""
    lines = [
        f"# {script.episode_title}",
        f"> {note.subtitle_kr}",
        f"(총 {len(script.chapters)}개 챕터 · 약 {script.total_chars:,}자 · 예상 {round(script.total_chars / DEFAULT_CHARS_PER_MINUTE)}분)",
        "",
        "## 오프닝",
        script.opening,
        "",
    ]
    for idx, (title, body) in enumerate(script.chapters, start=1):
        lines.extend([f"## Chapter {idx}. {title}", body, ""])
    lines.extend(["## 클로징", script.closing, ""])
    return "\n".join(lines)
