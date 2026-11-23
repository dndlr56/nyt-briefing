"""2단계 API 생성기: (1) 원문 충실 번역·구조화 요약 + 대화 캐스팅 설계 → (2) 컨텍스트 캐싱 최적화 묶음 대화형 대본 생성."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import time

from deep_dive.gemini_pool import GeminiClientPool
from deep_dive.models import (
    CurationDecision,
    DeepDiveScript,
    DeepDiveStudyNote,
    DialogueTurn,
    FetchedDocument,
    PartDialogueOutput,
)
from deep_dive.themes import ThemeSlot
from usage_tracker import RunUsageTracker


KST = timezone(timedelta(hours=9))
DEFAULT_NOTE_MODEL = "gemini-4-hydrogen"
DEFAULT_SCRIPT_MODEL = "gemini-4-hydrogen"
# gemini-4-hydrogen 공식 지원 추론 레벨: low | medium | high (기본) | xhigh  (minimal 미지원 -> 400 에러)
DEFAULT_NOTE_THINKING = "high"       # Stage 1: 원문 정독·정확한 번역·구조화 요약·캐스팅 설계 (1회 호출)
DEFAULT_CHAPTER_THINKING = "low"     # Stage 2: 묶음 파트별 담백한 대화형 대본 전개
DEFAULT_CHARS_PER_MINUTE = 400       # 대화형 턴 간 호흡 포함 한국어 낭독 기준 ~400자/분


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
5. **담백한 대화 캐스팅 (`dialogue_cast`) — 기본 2인, 상황극 금지**:
   - **기본값은 반드시 `host_and_expert` (사회자 1명 `S1` + 패널 1명 `S2`)**로 설정하세요.
   - **예외적인 경우에만 구성을 변경하세요**:
     - 원문이 상반된 두 견해·찬반 논쟁·다수의견 대 반대의견(판례)·서로 다른 두 논문을 뚜렷하게 대조하는 경우에만 `two_panelists` (패널 2명 `S1, S2`) 또는 `host_and_two_panelists` (사회자 `S1` + 패널 2명 `S2, S3`)를 선택하세요.
     - 단순히 보고서가 길거나 분야가 넓다는 이유만으로 3인 구성을 쓰지 마세요 (`three_panelists`는 극히 예외적일 때만 사용).
   - **화자 ID와 성별 일치**: `S1`은 여성(`지수`), `S2`는 남성(`현수`), 예외적 3인일 때 `S3`는 남성(`인준`)으로 지정하고, 과장된 가상 전문가 직함 대신 담백한 라벨(`사회자`, `패널`, `다수의견 해설`, `반대의견 해설` 등)을 사용하세요.
"""


DIALOGUE_SCRIPT_SYSTEM_INSTRUCTION = """당신은 권위 있는 학술 논문·보고서·판례의 내용을 **두 사람(예외 시 세 사람)의 서로 다른 목소리로 담백하고 명료하게 전달하는 한국어 지식 브리핑 작가**입니다.

[대화형 발화 품질 원칙]
1. **상황극·인위적 역할극 금지 (가장 중요)**:
   - "안녕하세요, 역학 담당 지수입니다", "오늘 모신 임상 전문가 현수 님 반갑습니다", "정말 놀랍네요!", "저도 공감합니다" 같은 **어색한 라디오 상황극, 가상 전문가 연기, 과장된 추임새·맞장구를 절대 넣지 마세요.**
   - 여러 목소리를 쓰는 목적은 장시간 청취 시 집중력을 유지하고 논점을 깔끔하게 구분하기 위함입니다.
   - **사회자(`S1`)**는 간결하게 주제를 전환하고 독자가 궁금해할 핵심 질문을 짚어주며, **패널(`S2`, 필요 시 `S3`)**은 원문의 내용·수치·근거를 차분하고 또렷하게 설명하는 담백한 정보 전달 톤을 유지하세요.
   - 서로 이름을 불필요하게 자주 부르거나 친한 척 농담하지 마세요.
2. **순수 발화용 한국어 텍스트만 작성**: 마크다운(`**`, `##`, `-`), 번호 매기기, URL, 이모지, 무대 지시문(`(웃음)`, `(잠시 멈춤)`)을 절대 넣지 마세요.
3. **불필요한 영어 병기·읽어주기 금지**: "영어로는 ~라고 하는데요" 같은 번거로운 영어 병기 없이 자연스러운 우리말로 매끄럽게 말하세요. (`LDL`, `LLM`, 고유명사 등 한국어에서도 그대로 쓰는 약어만 자연스럽게 발음)
4. **원문 충실 전달 (AI 임의 추론·뇌피셜 금지)**:
   - 모든 발언은 제공된 원문의 사실, 수치, 연구 방법, 근거, 저자(또는 법원)가 직접 밝힌 한계와 결론에 충실히 기반해야 합니다.
   - 상반된 관점을 다루는 구성이라도, **원문에 실제로 제시된 양측 논거·반론·한계**만 균형 있게 전달하고 AI가 원문에 없는 주장을 지어내지 마세요.
5. **밀도 있는 턴 호흡**:
   - "맞아요.", "그렇군요." 같은 의미 없는 1어절 턴은 쓰지 말고, 질문 턴은 1~2문장으로 간결하게, 해설 턴은 **3~6문장**으로 밀도 있게 구성해 자연스럽게 목소리를 교차하세요.
   - 오직 캐스팅에 포함된 `speaker_id`만 사용하세요.
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


def _call_json_interaction(
    pool: GeminiClientPool,
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
    interaction, _ = pool.create_interaction(
        model=model,
        stage=stage,
        usage_tracker=usage_tracker,
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
    raw = interaction.output_text
    if not raw:
        raise RuntimeError(f"{stage}: Gemini API 빈 응답")
    return raw


def generate_study_note(
    docs: list[FetchedDocument],
    theme: ThemeSlot,
    decision: CurationDecision,
    model: str | None = None,
    usage_tracker: RunUsageTracker | None = None,
    gemini_pool: GeminiClientPool | None = None,
) -> DeepDiveStudyNote:
    """Stage 1: 원문 전문을 자연스러운 한국어로 충실하게 번역·구조화 요약하고 대화 캐스팅 및 챕터 설계도를 생성합니다."""
    selected_model = model or os.getenv("DEEP_NOTE_MODEL", DEFAULT_NOTE_MODEL)
    note_thinking = sanitize_thinking_level(
        selected_model, os.getenv("DEEP_NOTE_THINKING", DEFAULT_NOTE_THINKING)
    )
    pool = gemini_pool or GeminiClientPool()
    docs_context = _build_documents_context(docs)

    mode_guide = (
        "단일 원문을 처음부터 끝까지 빠짐없이 충실하게 번역·요약하는 모드입니다."
        if len(docs) == 1
        else f"선별된 {len(docs)}편의 원문 핵심 내용을 왜곡 없이 충실하게 정리·대조하는 모드입니다."
    )

    # 컨텍스트 캐시 친화적으로 대용량 원문(docs_context)을 맨 앞에 배치
    prompt = (
        f"{docs_context}\n\n"
        f"=== 요약 및 설계 지시 ===\n"
        f"오늘의 테마 힌트: {theme.emoji} {theme.label_kr} ({theme.theme_id})\n"
        f"세부 주제: {decision.sub_subject_kr}\n"
        f"핵심 주제: {decision.unifying_angle_kr}\n"
        f"모드: {mode_guide}\n\n"
        "위 원문 텍스트만을 엄격한 근거로 삼아, 불필요한 영어 병기나 AI의 임의 추론·재가공 없이 "
        "원문 내용을 충실하게 담은 한국어 구조화 요약 노트(DeepDiveStudyNote), "
        "5대 핵심 아카이브 분야 분류(primary_theme_id), "
        "담백한 대화 캐스팅(dialogue_cast — 기본은 사회자1+패널1, 상반된 두 관점 대조 시에만 예외 변경), "
        "4~6개 오디오 챕터 설계도를 JSON으로 작성해 주세요."
    )

    tier_label = pool.active_tier_for(selected_model)
    print(
        f"      [Stage 1] 원문 충실 번역·구조화 요약·캐스팅 생성 중 "
        f"(model={selected_model}, thinking={note_thinking}, tier={tier_label})..."
    )
    raw_json = _call_json_interaction(
        pool=pool,
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
    """생성된 턴의 speaker_id가 캐스팅 목록 안에 있도록 보정합니다."""
    if not valid_ids:
        return turns
    fixed: list[DialogueTurn] = []
    for idx, t in enumerate(turns):
        sid = t.speaker_id if t.speaker_id in valid_ids else valid_ids[idx % len(valid_ids)]
        cleaned_text = t.text.strip()
        if cleaned_text:
            fixed.append(DialogueTurn(speaker_id=sid, text=cleaned_text))
    return fixed


def resolve_adaptive_target_minutes(
    docs: list[FetchedDocument],
    requested_minutes: int = 0,
) -> int:
    """
    요청 분량이 0(auto)이면 그날 다루는 문헌 수와 원문 분량에 따라 25~45분으로 자동 결정합니다.
    (직접 지정 시 12~60분 범위로 클램핑)
    """
    if requested_minutes > 0:
        return max(12, min(60, int(requested_minutes)))

    total_chars = sum(d.char_count for d in docs)
    sample = "".join(d.full_text[:1000] for d in docs)
    hangul_ratio = (
        sum(1 for ch in sample if "\uac00" <= ch <= "\ud7a3") / max(1, len(sample))
    )
    effective_chars = int(total_chars * 1.8) if hangul_ratio >= 0.2 else total_chars
    doc_count = len(docs)

    if effective_chars >= 65_000 or (doc_count >= 3 and effective_chars >= 40_000):
        return 45
    if effective_chars >= 40_000 or doc_count >= 2:
        return 35
    if effective_chars >= 20_000:
        return 30
    return 25


def _build_section_batches(
    chapter_count: int,
    target_minutes: int,
) -> list[list[str]]:
    """
    호출 수와 입력 토큰을 줄이면서도 목표 분량이 과도하게 압축되지 않도록 섹션을 2~3개 파트로 묶습니다.
    - <=29분: 2파트
    - >=30분: 3파트 (closing이 단독 파트로 떨어져 원문을 불필요하게 재전송하지 않도록 균등 분할)
    """
    all_ids = ["opening", *[f"ch_{i}" for i in range(1, chapter_count + 1)], "closing"]
    num_batches = 2 if target_minutes <= 29 else 3
    if len(all_ids) <= num_batches:
        return [[sid] for sid in all_ids]

    batches: list[list[str]] = []
    base_size, rem = divmod(len(all_ids), num_batches)
    cursor = 0
    for b_idx in range(num_batches):
        size = base_size + (1 if b_idx < rem else 0)
        batches.append(all_ids[cursor : cursor + size])
        cursor += size
    return batches


def generate_longform_script(
    docs: list[FetchedDocument],
    theme: ThemeSlot,
    decision: CurationDecision,
    note: DeepDiveStudyNote,
    target_minutes: int = 25,
    model: str | None = None,
    inter_call_sleep: float | None = None,
    usage_tracker: RunUsageTracker | None = None,
    gemini_pool: GeminiClientPool | None = None,
) -> DeepDiveScript:
    """
    Stage 2: 컨텍스트 캐싱 친화적 고정 프리픽스 + 2~3회 묶음 호출로
    담백한 2인(예외 시 3인) 대화형 롱폼 오디오 대본을 생성합니다.
    """
    selected_model = model or os.getenv("DEEP_SCRIPT_MODEL", DEFAULT_SCRIPT_MODEL)
    chapter_thinking = sanitize_thinking_level(
        selected_model, os.getenv("DEEP_CHAPTER_THINKING", DEFAULT_CHAPTER_THINKING)
    )
    pool = gemini_pool or GeminiClientPool()

    clamped_minutes = max(12, min(60, int(target_minutes)))
    total_target_chars = clamped_minutes * DEFAULT_CHARS_PER_MINUTE
    opening_target = 450
    closing_target = 400
    chapter_count = max(1, len(note.chapters))
    per_chapter_target = max(1200, (total_target_chars - opening_target - closing_target) // chapter_count)

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
        f"- ch_{ch.chapter_number} (Chapter {ch.chapter_number}. {ch.title_kr}): {ch.focus_kr}"
        for ch in note.chapters
    )
    conclusions_str = "\n".join(f"- {c}" for c in note.author_conclusions_kr)

    # Implicit Context Caching 적중률 극대화: 대용량 원문 + 고정 메타를 바이트 동일 프리픽스로 맨 앞에 배치!
    shared_prefix = (
        f"{docs_context}\n\n"
        f"=== 고정 에피소드 설정 ===\n"
        f"에피소드 제목: {episode_title}\n"
        f"부제: {note.subtitle_kr}\n"
        f"원문 개요: {note.overview_and_purpose_kr}\n\n"
        f"출연진 (반드시 {', '.join(valid_speaker_ids)}만 사용, 상황극/가상 직함 소개 금지):\n{cast_guide}\n\n"
        f"전체 챕터 목차:\n{chapter_toc}\n\n"
        f"원문의 최종 결론 및 제언:\n{conclusions_str}\n"
    )

    batches = _build_section_batches(chapter_count=chapter_count, target_minutes=clamped_minutes)
    chapter_by_id = {f"ch_{ch.chapter_number}": ch for ch in note.chapters}
    name_map = {sp.speaker_id: sp.name_kr for sp in cast.speakers}
    sections_map: dict[str, list[DialogueTurn]] = {}
    prev_tail = ""

    cache_name: str | None = None
    cache_tier: str | None = None
    if len(batches) >= 2:
        cache_name, cache_tier = pool.create_context_cache(
            model=selected_model,
            system_instruction=DIALOGUE_SCRIPT_SYSTEM_INSTRUCTION,
            contents=shared_prefix,
            ttl_seconds=900,
            display_name=f"deep-dive-{theme.theme_id}",
        )

    try:
        for batch_idx, sec_ids in enumerate(batches, start=1):
            if batch_idx > 1:
                wait_sec = (
                    inter_call_sleep
                    if inter_call_sleep is not None
                    else (22.0 if pool.is_free_tier_for(selected_model) else 1.5)
                )
                if wait_sec > 0:
                    print(
                        f"      [Script] Pacing ({pool.active_tier_for(selected_model)}): "
                        f"waiting {wait_sec:.0f}s before Part {batch_idx}/{len(batches)}..."
                    )
                    time.sleep(wait_sec)

            spec_lines: list[str] = []
            part_target_chars = 0
            for sid in sec_ids:
                if sid == "opening":
                    part_target_chars += opening_target
                    spec_lines.append(
                        f"• section_id='opening' (오프닝): 목표 약 {opening_target - 80}~{opening_target + 100}자, 2~3턴. "
                        "불필요한 인사말·상황극 없이 오늘 다룰 원문의 출처, 핵심 주제, 전체 구성만 담백하고 간결하게 소개."
                    )
                elif sid == "closing":
                    part_target_chars += closing_target
                    spec_lines.append(
                        f"• section_id='closing' (클로징): 목표 약 {closing_target - 80}~{closing_target + 100}자, 2~3턴. "
                        "원문의 최종 결론과 핵심 제언을 차분하게 정리하고 간결하게 마무리."
                    )
                else:
                    ch = chapter_by_id[sid]
                    min_c = int(per_chapter_target * 0.85)
                    max_c = int(per_chapter_target * 1.15)
                    t_min = max(4, min_c // 320)
                    t_max = max(t_min + 3, max_c // 200)
                    part_target_chars += per_chapter_target
                    pts = " / ".join(ch.key_points)
                    spec_lines.append(
                        f"• section_id='{sid}' (Chapter {ch.chapter_number}. {ch.title_kr}): "
                        f"목표 약 {min_c}~{max_c}자, {t_min}~{t_max}턴.\n"
                        f"  - 핵심 흐름: {ch.focus_kr}\n"
                        f"  - 반드시 포함할 원문 근거·수치: {pts}"
                    )

            tail_block = (
                f"\n직전 파트의 마지막 대화(자연스럽게 이어가고 반복 금지):\n{prev_tail}\n"
                if prev_tail
                else ""
            )
            part_only_prompt = (
                f"=== 지금 작성할 파트 ({batch_idx}/{len(batches)}) ===\n"
                f"아래 {len(sec_ids)}개 섹션을 순서대로 빠짐없이 작성해 JSON(`sections`)으로 반환하세요 "
                f"(파트 합계 약 {int(part_target_chars * 0.85):,}~{int(part_target_chars * 1.15):,}자).\n"
                + "\n".join(spec_lines)
                + tail_block
                + "\n다시 강조: 어색한 상황극·가상 직함 소개·과장된 리액션·불필요한 영어 병기·원문에 없는 AI 추측을 모두 배제하고, "
                "담백한 진행 질문과 충실한 원문 해설로 대화 턴을 작성하세요."
            )
            full_part_prompt = f"{shared_prefix}\n{part_only_prompt}"

            print(
                f"      [Script] Part {batch_idx}/{len(batches)} ({', '.join(sec_ids)}) 생성 중 "
                f"(목표 ~{part_target_chars:,}자, thinking={chapter_thinking}, "
                f"tier={pool.active_tier_for(selected_model)}"
                f"{', cache=ON' if cache_name else ''})..."
            )
            raw_part = _call_json_interaction(
                pool=pool,
                model=selected_model,
                system_instruction=DIALOGUE_SCRIPT_SYSTEM_INSTRUCTION,
                prompt=part_only_prompt if cache_name else full_part_prompt,
                schema=PartDialogueOutput.model_json_schema(),
                max_output_tokens=20480,
                thinking_level=chapter_thinking,
                stage=f"대본 Part {batch_idx}/{len(batches)}",
                usage_tracker=usage_tracker,
                cached_content=cache_name,
                cached_tier=cache_tier,
                fallback_prompt=full_part_prompt,
            )
            parsed = PartDialogueOutput.model_validate_json(raw_part)

            # section_id 매칭 (모델이 식별자를 약간 다르게 썼을 경우 순서 기반 폴백)
            by_id = {s.section_id.strip().lower(): s.turns for s in parsed.sections}
            for pos, expected_id in enumerate(sec_ids):
                raw_turns = by_id.get(expected_id.lower())
                if raw_turns is None and pos < len(parsed.sections):
                    raw_turns = parsed.sections[pos].turns
                if raw_turns:
                    sections_map[expected_id] = _normalize_speaker_ids(raw_turns, valid_speaker_ids)

            last_sec_turns = sections_map.get(sec_ids[-1], [])
            if last_sec_turns:
                prev_tail = "\n".join(
                    f"{name_map.get(t.speaker_id, t.speaker_id)}: {t.text}"
                    for t in last_sec_turns[-2:]
                )[-450:]
    finally:
        pool.delete_context_cache(cache_name, cache_tier)

    opening_turns = sections_map.get("opening", [])
    closing_turns = sections_map.get("closing", [])
    rendered_chapters: list[tuple[str, list[DialogueTurn]]] = [
        (ch.title_kr, sections_map.get(f"ch_{ch.chapter_number}", []))
        for ch in note.chapters
        if sections_map.get(f"ch_{ch.chapter_number}")
    ]

    if not rendered_chapters:
        raise RuntimeError("Stage 2 대본 생성 결과에 유효한 챕터가 없습니다.")

    return DeepDiveScript(
        episode_title=episode_title,
        cast=cast,
        opening_turns=opening_turns,
        chapters=rendered_chapters,
        closing_turns=closing_turns,
    )


def format_script_markdown(script: DeepDiveScript, note: DeepDiveStudyNote) -> str:
    """다중 화자 대화 대본을 읽기 좋은 마크다운 문서로 포맷합니다."""
    def _esc(text: str) -> str:
        return (text or "").replace("~", "∼")

    labels = script.speaker_label_map()
    cast_line = " · ".join(f"{sp.name_kr}({sp.role_title_kr})" for sp in script.cast.speakers)
    lines = [
        f"# {_esc(script.episode_title)}",
        f"> {_esc(note.subtitle_kr)}",
        f"- **대화 구성**: {script.cast.format_type} — {cast_line}",
        f"- **분량**: 총 {len(script.chapters)}개 챕터 · {script.total_turns}턴 · 약 {script.total_chars:,}자 (예상 {round(script.total_chars / DEFAULT_CHARS_PER_MINUTE)}분)",
        "",
        "## 오프닝",
    ]
    for t in script.opening_turns:
        lines.append(f"**{labels.get(t.speaker_id, t.speaker_id)}**: {_esc(t.text)}\n")

    for idx, (title, turns) in enumerate(script.chapters, start=1):
        lines.append(f"## Chapter {idx}. {_esc(title)}")
        for t in turns:
            lines.append(f"**{labels.get(t.speaker_id, t.speaker_id)}**: {_esc(t.text)}\n")

    lines.append("## 클로징")
    for t in script.closing_turns:
        lines.append(f"**{labels.get(t.speaker_id, t.speaker_id)}**: {_esc(t.text)}\n")
    return "\n".join(lines)
