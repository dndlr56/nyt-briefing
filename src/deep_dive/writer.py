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


STUDY_NOTE_SYSTEM_INSTRUCTION = """당신은 학술 논문·장문 보고서·판례·심층 에세이를 왜곡이나 공허한 미사여구 없이 **밀도 높고 읽기 편한 한국어 연구 노트로 구조화하는 수석 학술 편집자**입니다.

[작성 절대 원칙]
1. **구체적 수치·작동 메커니즘·트레이드오프 보존 (No Abstraction Slop)**:
   - "성능이 크게 향상되었다", "유의미한 결과를 보였다", "심도 있게 고찰한다", "다각도로 조명한다", "중추적인 역할을 한다" 같은 **공허한 AI 상투어와 메타 서술("이 논문은 ~을 다룬다")을 철저히 배제**하세요.
   - 반드시 원문의 **구체적 수치, 표본 크기(N), 비교 기준선, 사건/조문 번호, 인과적 작동 원리(왜 A가 B를 낳는가), 그리고 트레이드오프(무엇을 얻는 대신 어떤 비용·제약이 따르는가)**로 서술하세요.
2. **문헌 성격별 맞춤 렌즈 (`doc_lens`) 적용**:
   - 학술 실험·리뷰·메타분석 논문은 `empirical_or_review`(기존 통념 대 반박 테제, 표본·식별 설계, 작동 원리, 외적 타당성 경계 조건)로 정리하세요.
   - 판례·법학 논문·법조 칼럼은 `law_and_cases`(사실관계 뼈대와 핵심 쟁점, 관련 조문·선례 지형, 문언·체계·취지·이익형량 논거 대립, 사안 포섭 및 구별 포인트)로 정리하세요.
   - 정책·경제 보고서나 심층 에세이(Quanta, Aeon, Brookings 등)는 `policy_or_essay`(문제 제기와 핵심 테제, 분석 프레임워크와 실증·사례 논거, 반론·트레이드오프, 정책·전략적 함의)로 정리하세요.
3. **사람이 정독하기 좋은 문단 호흡**:
   - 7~8문장을 한 덩어리 통문단으로 뭉치지 마세요. `overview_and_purpose_kr`, `methodology_and_evidence_kr`, 각 `body_sections.content_kr`에서 논리 단계가 넘어갈 때 `\n\n`으로 **2개의 정돈된 문단(문단당 3~4문장)**으로 나누어 가독성을 높이세요.
4. **자연스러운 한국어 문장 (불필요한 영어 병기 금지)**:
   - 한국어로 뜻이 명확한 개념 뒤에 습관적으로 영어를 괄호 병기하지 마세요(`인지 예비력(cognitive reserve)` 금지 → `인지 예비력`).
   - 한국어 학술·실무 글에서도 그대로 쓰는 고유명사·약어(`LDL`, `GLP-1`, `APOE ε4`, `RLHF`, `LLM`, 인명·기관명·법령 약칭 등)만 자연스럽게 표기하세요.
5. **엄격한 원문 근거주의 (AI 자의적 추론 금지)**:
   - 원문에 없는 수치, 인용, 외부 사례, 억지 교훈을 절대 덧붙이지 마세요.
   - 한계·경계 조건(`author_stated_limitations_kr`)과 결론(`author_conclusions_kr`)도 반드시 원문(저자·보고서·법원·반대의견 등)이 직접 제시한 근거와 조건에 기반해 작성하세요.
6. **담백한 대화 캐스팅 (`dialogue_cast`) — 기본 2인, 상황극 금지**:
   - **기본값은 반드시 `host_and_expert` (사회자 1명 `S1` + 패널 1명 `S2`)**로 설정하세요.
   - 원문이 상반된 두 견해·다수의견 대 반대의견(판례)·서로 다른 두 이론/논문을 뚜렷하게 대조할 때만 `two_panelists` 또는 `host_and_two_panelists`를 예외 선택하세요 (`three_panelists`는 극히 예외적일 때만).
   - `S1`은 여성(`지수`), `S2`는 남성(`현수`), 예외적 3인 시 `S3`는 남성(`인준`)으로 고정하고, 가상 전문가 직함 없이 담백한 역할명(`진행`, `해설`, `다수의견 해설`, `반대의견 해설`)을 쓰세요.
"""


DIALOGUE_SCRIPT_SYSTEM_INSTRUCTION = """당신은 권위 있는 학술 논문·보고서·판례의 내용을 **두 사람(예외 시 세 사람)의 서로 다른 목소리로 깊이 있고 명쾌하게 풀어내는 한국어 심층 지식 브리핑 작가**입니다.

[대화형 발화 품질 원칙]
1. **상황극·인위적 역할극 금지 & 기계적 문답 탈피**:
   - "안녕하세요, 역학 담당 지수입니다", "정말 놀랍네요!", "저도 공감합니다" 같은 **어색한 라디오 상황극, 가상 직함 연기, 과장된 맞장구를 절대 넣지 마세요.**
   - 동시에 "다음 장은 무엇인가요?" → "다음은 ~입니다" 같은 **기계적인 목차 낭독식 질의응답도 피하세요.**
   - **진행자(`S1`)**는 청취자가 설명을 듣다가 자연스럽게 품을 법한 **날카로운 핵심 질문**("직관적으로는 A가 맞을 것 같은데 왜 실험에서는 반대로 나왔나요?", "그렇다면 어떤 조건에서는 이 법리나 효과가 성립하지 않나요?")으로 논점을 전환하고,
   - **해설자(`S2`, 필요 시 `S3`)**는 추상적 명사 나열 대신 **원문의 구체적 실험 설계·사례·비유·수치·인과 사슬**을 귀로만 들어도 머릿속에 그려지도록 차분하게 풀어서 설명하세요.
2. **순수 발화용 한국어 텍스트만 작성**: 마크다운(`**`, `##`, `-`), 번호 매기기, URL, 이모지, 무대 지시문(`(웃음)`, `(잠시 멈춤)`)을 절대 넣지 마세요.
3. **불필요한 영어 병기·읽어주기 금지**: "영어로는 ~라고 하는데요" 같은 군더더기 없이 자연스러운 우리말로 말하세요(`LDL`, `LLM`, 고유명사 등 통용 약어만 자연스럽게 발음).
4. **원문 충실 전달 (AI 임의 추론 금지)**:
   - 모든 발언은 원문의 사실, 수치, 연구 방법/판단 기준, 저자(또는 법원)가 밝힌 한계와 결론에 엄격히 기반해야 합니다. 원문에 없는 외부 주장이나 억지 일반화를 지어내지 마세요.
5. **밀도 있는 턴 호흡**:
   - "맞아요.", "그렇군요." 같은 1어절 빈 턴을 금지합니다. 질문·전환 턴은 1~2문장으로 핵심을 찌르고, 해설 턴은 **3~6문장**으로 원리와 근거를 완결성 있게 짚어 자연스럽게 목소리를 교차하세요.
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
        "위 원문 텍스트만을 엄격한 근거로 삼아, 공허한 상투어나 AI의 임의 추론 없이 "
        "원문의 구체적 수치·작동 메커니즘·트레이드오프·경계 조건을 밀도 있게 담은 한국어 연구 노트(DeepDiveStudyNote), "
        "문헌 성격에 맞는 분석 렌즈(doc_lens: 논문='empirical_or_review', 판례·법학='law_and_cases', 보고서·에세이='policy_or_essay'), "
        "5대 핵심 아카이브 분야 분류(primary_theme_id), 검색용 핵심 태그(tags), "
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
        full_part_prompt = (
            f"{shared_prefix}\n"
            f"=== 지금 작성할 파트 ({batch_idx}/{len(batches)}) ===\n"
            f"아래 {len(sec_ids)}개 섹션을 순서대로 빠짐없이 작성해 JSON(`sections`)으로 반환하세요 "
            f"(파트 합계 약 {int(part_target_chars * 0.85):,}~{int(part_target_chars * 1.15):,}자).\n"
            + "\n".join(spec_lines)
            + tail_block
            + "\n다시 강조: 어색한 상황극·가상 직함 소개·과장된 리액션·불필요한 영어 병기·원문에 없는 AI 추측을 모두 배제하고, "
            "담백한 진행 질문과 충실한 원문 해설로 대화 턴을 작성하세요."
        )

        print(
            f"      [Script] Part {batch_idx}/{len(batches)} ({', '.join(sec_ids)}) 생성 중 "
            f"(목표 ~{part_target_chars:,}자, thinking={chapter_thinking}, "
            f"tier={pool.active_tier_for(selected_model)})..."
        )
        raw_part = _call_json_interaction(
            pool=pool,
            model=selected_model,
            system_instruction=DIALOGUE_SCRIPT_SYSTEM_INSTRUCTION,
            prompt=full_part_prompt,
            schema=PartDialogueOutput.model_json_schema(),
            max_output_tokens=20480,
            thinking_level=chapter_thinking,
            stage=f"대본 Part {batch_idx}/{len(batches)}",
            usage_tracker=usage_tracker,
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
