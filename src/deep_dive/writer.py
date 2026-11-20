"""2단계 API 생성기: (1) 원문 충실 번역·구조화 요약 + 대화 캐스팅 설계 → (2) 2~3인 대화형 롱폼 오디오 대본 생성."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import time

from google import genai

from deep_dive.models import (
    ChapterDialogueOutput,
    CurationDecision,
    DeepDiveScript,
    DeepDiveStudyNote,
    DialogueTurn,
    FetchedDocument,
)
from deep_dive.themes import ThemeSlot
from usage_tracker import RunUsageTracker


KST = timezone(timedelta(hours=9))
DEFAULT_NOTE_MODEL = "gemini-4-hydrogen"
DEFAULT_SCRIPT_MODEL = "gemini-4-hydrogen"
# gemini-4-hydrogen 공식 지원 추론 레벨: low | medium | high (기본) | xhigh  (minimal 미지원 -> 400 에러)
DEFAULT_NOTE_THINKING = "high"       # Stage 1: 원문 정독·정확한 번역·구조화 요약·캐스팅 설계 (1회 호출)
DEFAULT_CHAPTER_THINKING = "low"     # Stage 2: 챕터 설계도 기반 2~3인 자연스러운 대화 전개
DEFAULT_BOOKEND_THINKING = "low"     # Stage 2: 오프닝/클로징 대화 (Hydrogen 최저 레벨 = low)
DEFAULT_CHARS_PER_MINUTE = 400       # 대화형 턴 간 호흡(0.28s) 포함 한국어 낭독 기준 ~400자/분


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
   - 일반적인 학술 개념이나 한국어로 충분히 뜻이 통하는 표현 뒤에 괄호로 영어를 덧붙이지 마세요 (예: `인지 예비력(cognitive reserve)`, `유병률(prevalence)` 같은 불필요한 병기 금지 → 그냥 `인지 예비력`, `유병률`로 자연스럽게 서술).
   - 한국어 글에서도 그대로 통용되는 고유명사·약어·생물학 표기(`LDL`, `GLP-1`, `APOE ε4`, `MRI`, `LLM`, 인명·기관명 등)만 예외적으로 그대로 사용하세요.
3. **엄격한 텍스트 근거주의 (AI 자의적 판단·추론 금지)**:
   - 원문에 없는 수치, 인용, 해석을 절대 지어내지 마세요.
   - 한계(`author_stated_limitations_kr`)와 결론·시사점(`author_conclusions_kr`) 역시 **반드시 원문(저자·보고서·법원·반대의견 등)에 명시된 근거와 서술에 기반**해 작성하세요.
4. **5대 핵심 분야 분류 (`primary_theme_id`)**:
   - 문헌의 실제 내용에 가장 정확히 맞는 분야(`ai_tech`, `science_mind`, `health_medicine`, `econ_geopolitics`, `law_cases`)를 지정하세요.
5. **내용 맞춤형 2~3인 대화 캐스팅 (`dialogue_cast`)**:
   - 1인 독백 대신 청취 집중도를 높일 수 있는 2인 또는 3인 대화 구성을 문헌 성격에 맞게 설계하세요:
     - `host_and_expert` (S1 사회자 + S2 패널): 단일 심층 연구·리뷰를 질문과 해설로 명쾌히 풀어갈 때
     - `two_panelists` (S1 패널 + S2 패널): 두 전문가가 동등하게 논점을 주고받거나, 글 속 상반된 두 입장을 각각 균형 있게 설명할 때
     - `host_and_two_panelists` (S1 사회자 + S2 패널 + S3 패널): 원문이 상반된 두 관점(찬반·다수/반대의견·정책 논쟁)이나 2개 이상 축을 균형 있게 다룰 때
     - `three_panelists` (S1, S2, S3 패널 3인): 다학제 대형 보고서(예: 역학·임상·정책이 결합된 위원회 보고서)나 복수 문헌 종합 시 각자 담당 축을 나눠 정리할 때
   - 화자 ID는 반드시 2인일 때 `S1, S2`, 3인일 때 `S1, S2, S3`를 사용하고, 부르기 자연스러운 짧은 한국어 이름(예: S1=`지수`, S2=`현수`, S3=`서연`)을 지정하세요.
"""


DIALOGUE_SCRIPT_SYSTEM_INSTRUCTION = """당신은 권위 있는 학술 논문·보고서·판례의 내용을 **2~3명의 출연자가 자연스럽게 대화를 나누며 충실하게 전달하는 한국어 지식 팟캐스트 작가**입니다.

[대화형 발화 품질 원칙]
1. **순수 발화용 한국어 텍스트만 작성**: 마크다운(`**`, `##`, `-`), 번호 매기기, URL, 이모지, 무대 지시문(`(웃음)`, `(잠시 멈춤)`)을 절대 넣지 마세요.
2. **불필요한 영어 병기·읽어주기 금지**: "영어로는 ~라고 하는데요" 같은 번거로운 영어 병기 없이 자연스러운 우리말로 매끄럽게 말하세요. (`LDL`, `LLM`, 고유명사 등 한국어에서도 그대로 쓰는 약어만 자연스럽게 발음)
3. **원문 충실 전달 (AI 임의 추론·뇌피셜 금지)**:
   - 모든 대화 발언은 제공된 원문의 사실, 수치, 연구 방법, 근거, 저자(또는 법원)가 직접 밝힌 한계와 결론에 충실히 기반해야 합니다.
   - 상반된 관점을 다루는 구성이라도, **원문에 실제로 제시된 양측 논거·반론·한계**만 균형 있게 대변하고 AI가 원문에 없는 주장을 지어내지 마세요.
4. **자연스럽고 밀도 있는 대화 호흡**:
   - "맞아요.", "그렇군요." 같은 의미 없는 1어절 추임새 턴은 쓰지 말고, 각 턴은 **보통 2~5문장**으로 실질적인 정보를 주고받으며 자연스럽게 다음 화자에게 바통을 넘기세요.
   - 화자 이름을 가끔 자연스럽게 불러 생동감을 살리되(예: "현수 님, 그럼 보고서에서는...", "방금 지수 님이 짚어주신 부분에서..."), 과하게 반복하지 마세요.
   - 오직 캐스팅에 포함된 `speaker_id`(`S1`, `S2`, 또는 3인일 때 `S3`)만 사용하세요.
5. **이전 챕터와의 자연스러운 연결**: 직전 챕터의 마지막 대화를 자연스럽게 이어받되 앞선 내용을 중복 반복하지 마세요.
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
    """Stage 1: 원문 전문을 자연스러운 한국어로 충실하게 번역·구조화 요약하고 2~3인 대화 캐스팅 및 챕터 설계도를 생성합니다."""
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
        f"수집 트랙: {theme.emoji} {theme.label_kr} | 세부 주제: {decision.sub_subject_kr}\n"
        f"구성 방식: {mode_label}\n"
        f"핵심 주제: {decision.unifying_angle_kr}\n\n"
        "아래 원문 텍스트의 내용을 빠짐없이 충실하게 번역·요약하고, "
        "이 글의 성격에 가장 잘 맞는 5대 핵심 분야(`primary_theme_id`)와 2~3인 대화 캐스팅(`dialogue_cast`)을 "
        "함께 설계해 JSON으로 작성해 주세요. 불필요한 영문 괄호 병기와 본문에 없는 AI 자의적 추론은 넣지 마세요.\n\n"
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


def _normalize_speaker_ids(
    turns: list[DialogueTurn],
    valid_ids: list[str],
) -> list[DialogueTurn]:
    """모델이 캐스팅에 없는 speaker_id를 냈을 경우 유효 화자로 안전 보정합니다."""
    if not valid_ids:
        return turns
    fixed: list[DialogueTurn] = []
    for idx, t in enumerate(turns):
        sid = t.speaker_id if t.speaker_id in valid_ids else valid_ids[idx % len(valid_ids)]
        cleaned_text = t.text.strip()
        if cleaned_text:
            fixed.append(DialogueTurn(speaker_id=sid, text=cleaned_text))
    return fixed


def generate_longform_script(
    docs: list[FetchedDocument],
    theme: ThemeSlot,
    decision: CurationDecision,
    note: DeepDiveStudyNote,
    target_minutes: int = 30,
    model: str | None = None,
    inter_call_sleep: float | None = None,
    usage_tracker: RunUsageTracker | None = None,
) -> DeepDiveScript:
    """
    Stage 2: 2~3인 대화 캐스팅과 챕터 설계도를 바탕으로
    오프닝 → 각 챕터 → 클로징을 분할 생성해 20~60분 분량의 다중 화자 대화 대본을 조립합니다.
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
    opening_target = 550
    closing_target = 450
    chapter_count = max(1, len(note.chapters))
    per_chapter_target = max(1200, (total_target_chars - opening_target - closing_target) // chapter_count)

    client = genai.Client(http_options={"api_version": "v1beta"})
    docs_context = _build_documents_context(docs)
    today_title_date = datetime.now(KST).strftime("%Y.%m.%d")
    episode_title = f"[{theme.label_kr}] {note.episode_title_kr} | {today_title_date}"

    cast = note.dialogue_cast
    valid_speaker_ids = [sp.speaker_id for sp in cast.speakers]
    cast_guide = (
        f"대화 포맷: {cast.format_type} ({cast.format_reason_kr})\n"
        + "\n".join(
            f"- {sp.speaker_id}: {sp.name_kr} [{sp.role_title_kr}] — {sp.perspective_kr}"
            for sp in cast.speakers
        )
    )
    chapter_toc = "\n".join(
        f"- Chapter {ch.chapter_number}. {ch.title_kr}: {ch.focus_kr}"
        for ch in note.chapters
    )

    # 1) 오프닝 대화 생성
    print(
        f"      [Script] Opening 대화 생성 중 ({len(cast.speakers)}인: {cast.format_type}, "
        f"목표 ~{opening_target}자, thinking={bookend_thinking})..."
    )
    opening_prompt = (
        f"에피소드 제목: {episode_title}\n"
        f"부제: {note.subtitle_kr}\n"
        f"원문 개요: {note.overview_and_purpose_kr}\n\n"
        f"=== 출연진 캐스팅 ===\n{cast_guide}\n\n"
        f"=== 전체 챕터 구성 ===\n{chapter_toc}\n\n"
        f"출연진({', '.join(valid_speaker_ids)})이 자연스럽게 인사를 나누며 오늘 다룰 원문의 출처와 주제를 소개하고 "
        f"전체 논의 흐름을 예고하는 오프닝 대화를 3~4턴, 총 공백 포함 약 {opening_target - 80}~{opening_target + 120}자로 작성하세요."
    )
    raw_open = _call_interactions_with_retry(
        client=client,
        model=selected_model,
        system_instruction=DIALOGUE_SCRIPT_SYSTEM_INSTRUCTION,
        prompt=opening_prompt,
        schema=ChapterDialogueOutput.model_json_schema(),
        max_output_tokens=4096,
        thinking_level=bookend_thinking,
        stage="대본 Opening",
        usage_tracker=usage_tracker,
    )
    opening_turns = _normalize_speaker_ids(
        ChapterDialogueOutput.model_validate_json(raw_open).turns, valid_speaker_ids
    )

    # 2) 챕터별 순차 생성
    rendered_chapters: list[tuple[str, list[DialogueTurn]]] = []
    name_map = {sp.speaker_id: sp.name_kr for sp in cast.speakers}
    prev_tail = "\n".join(
        f"{name_map.get(t.speaker_id, t.speaker_id)}: {t.text}"
        for t in opening_turns[-2:]
    )[-450:]

    for idx, ch in enumerate(note.chapters, start=1):
        if sleep_sec > 0:
            print(f"      [Script] RPM/TPM pacing: waiting {sleep_sec:.0f}s before Chapter {idx}...")
            time.sleep(sleep_sec)

        min_c = int(per_chapter_target * 0.85)
        max_c = int(per_chapter_target * 1.15)
        target_turns_min = max(6, min_c // 260)
        target_turns_max = max(target_turns_min + 3, max_c // 180)
        print(
            f"      [Script] Chapter {idx}/{chapter_count} '{ch.title_kr}' 대화 생성 중 "
            f"(목표 {min_c}~{max_c}자, {target_turns_min}~{target_turns_max}턴, thinking={chapter_thinking})..."
        )
        points_str = "\n".join(f"- {p}" for p in ch.key_points)
        chapter_prompt = (
            f"에피소드: {episode_title}\n\n"
            f"=== 출연진 캐스팅 (반드시 {', '.join(valid_speaker_ids)}만 사용) ===\n{cast_guide}\n\n"
            f"전체 챕터 목차:\n{chapter_toc}\n\n"
            f"=== 지금 작성할 챕터 ({idx}/{chapter_count}) ===\n"
            f"소제목: {ch.title_kr}\n"
            f"전달할 흐름: {ch.focus_kr}\n"
            f"반드시 포함할 원문 근거·수치:\n{points_str}\n\n"
            f"직전 섹션의 마지막 대화(자연스럽게 이어갈 것, 반복 금지):\n{prev_tail}\n\n"
            f"목표 분량: 총 공백 포함 **약 {min_c}~{max_c}자**, 대화 턴 **약 {target_turns_min}~{target_turns_max}턴**.\n"
            "출연진이 각자의 역할에 맞게 질문·해설·균형 잡힌 보완을 주고받으며, "
            "불필요한 영어 병기나 임의 추측 없이 아래 원문 텍스트의 내용을 충실하게 풀어내는 대화 대본을 JSON으로 작성해 주세요.\n\n"
            + docs_context
        )
        raw_ch = _call_interactions_with_retry(
            client=client,
            model=selected_model,
            system_instruction=DIALOGUE_SCRIPT_SYSTEM_INSTRUCTION,
            prompt=chapter_prompt,
            schema=ChapterDialogueOutput.model_json_schema(),
            max_output_tokens=12288,
            thinking_level=chapter_thinking,
            stage=f"대본 Chapter {idx}",
            usage_tracker=usage_tracker,
        )
        ch_turns = _normalize_speaker_ids(
            ChapterDialogueOutput.model_validate_json(raw_ch).turns, valid_speaker_ids
        )
        rendered_chapters.append((ch.title_kr, ch_turns))
        prev_tail = "\n".join(
            f"{name_map.get(t.speaker_id, t.speaker_id)}: {t.text}"
            for t in ch_turns[-2:]
        )[-450:]

    # 3) 클로징 대화 생성
    if sleep_sec > 0:
        time.sleep(min(sleep_sec, 10.0))
    print(f"      [Script] Closing 대화 생성 중 (목표 ~{closing_target}자, thinking={bookend_thinking})...")
    conclusions_str = "\n".join(f"- {c}" for c in note.author_conclusions_kr)
    closing_prompt = (
        f"에피소드: {episode_title}\n"
        f"=== 출연진 캐스팅 ===\n{cast_guide}\n\n"
        f"원문의 최종 결론 및 제언:\n{conclusions_str}\n"
        f"직전 챕터 마지막 대화:\n{prev_tail}\n\n"
        f"출연진이 원문의 핵심 결론을 자연스럽게 정리하고 청취자에게 인사하며 마무리하는 클로징 대화를 "
        f"2~4턴, 총 공백 포함 약 {closing_target - 80}~{closing_target + 120}자로 작성해 주세요."
    )
    raw_close = _call_interactions_with_retry(
        client=client,
        model=selected_model,
        system_instruction=DIALOGUE_SCRIPT_SYSTEM_INSTRUCTION,
        prompt=closing_prompt,
        schema=ChapterDialogueOutput.model_json_schema(),
        max_output_tokens=4096,
        thinking_level=bookend_thinking,
        stage="대본 Closing",
        usage_tracker=usage_tracker,
    )
    closing_turns = _normalize_speaker_ids(
        ChapterDialogueOutput.model_validate_json(raw_close).turns, valid_speaker_ids
    )

    return DeepDiveScript(
        episode_title=episode_title,
        cast=cast,
        opening_turns=opening_turns,
        chapters=rendered_chapters,
        closing_turns=closing_turns,
    )


def format_script_markdown(script: DeepDiveScript, note: DeepDiveStudyNote) -> str:
    """다중 화자 대화 대본을 읽기 좋은 마크다운 문서로 포맷합니다."""
    labels = script.speaker_label_map()
    cast_line = " · ".join(f"{sp.name_kr}({sp.role_title_kr})" for sp in script.cast.speakers)
    lines = [
        f"# {script.episode_title}",
        f"> {note.subtitle_kr}",
        f"- **대화 구성**: {script.cast.format_type} — {cast_line}",
        f"- **분량**: 총 {len(script.chapters)}개 챕터 · {script.total_turns}턴 · 약 {script.total_chars:,}자 (예상 {round(script.total_chars / DEFAULT_CHARS_PER_MINUTE)}분)",
        "",
        "## 오프닝",
    ]
    for t in script.opening_turns:
        lines.append(f"**{labels.get(t.speaker_id, t.speaker_id)}**: {t.text}\n")

    for idx, (title, turns) in enumerate(script.chapters, start=1):
        lines.append(f"## Chapter {idx}. {title}")
        for t in turns:
            lines.append(f"**{labels.get(t.speaker_id, t.speaker_id)}**: {t.text}\n")

    lines.append("## 클로징")
    for t in script.closing_turns:
        lines.append(f"**{labels.get(t.speaker_id, t.speaker_id)}**: {t.text}\n")
    return "\n".join(lines)
