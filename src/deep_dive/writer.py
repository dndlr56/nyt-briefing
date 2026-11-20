"""2단계 API 생성기: (1) 구조화 비판적 독해 노트 생성 → (2) 챕터 분할 휴버만식 롱폼 오디오 대본 생성."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import time

from google import genai
from pydantic import ValidationError

from deep_dive.models import (
    ChapterBlueprint,
    ChapterNarrationOutput,
    CurationDecision,
    DeepDiveScript,
    DeepDiveStudyNote,
    FetchedDocument,
)
from deep_dive.themes import ThemeSlot
from usage_tracker import RunUsageTracker


KST = timezone(timedelta(hours=9))
DEFAULT_NOTE_MODEL = "gemini-3.8-flash"
DEFAULT_SCRIPT_MODEL = "gemini-4-hydrogen"
DEFAULT_CHARS_PER_MINUTE = 420  # 한국어 낭독 속도 기준 약 410~430자/분


STUDY_NOTE_SYSTEM_INSTRUCTION = """당신은 서울대 철학·심리학 전공자(로스쿨 입학 예정, 주식 초보 투자자)를 위해 탑티어 학술 논문·장문 에세이·정책 보고서·판례를 완벽히 해부하는 수석 리서치 펠로우입니다.

[작성 절대 원칙]
1. **원문 충실성 (No Hallucination)**: 제공된 원문 텍스트에 근거해 서술하세요. 원문에 없는 수치, 실험 집단 크기, 판결 인용구를 절대 지어내지 마세요.
2. **언어 스타일**: 명료하고 품격 있는 한국어 해설을 기본으로 하되, 핵심 학술·법학·경제 용어는 정확한 영문 원어를 병기하세요 (예: 인지 부하(cognitive load), 사법심사(judicial review)).
3. **비판적 깊이**: 단순 요약에 그치지 말고 ① 저자(또는 법원)가 반박하려는 기존 통념, ② 방법론·논증 구조(인과 추론, 식별 전략, 법리 해석), ③ 방법론적 한계 및 철학적·현실적 반론, ④ 독자의 학습·투자·건강·법적 사고에 연결되는 실질적 통찰까지 구조화하세요.
4. **핵심 인용(`key_quotes`)**: 원문에서 실제로 등장한 인상적인 핵심 문장을 2~4개 정확히 발췌하고 한국어로 번역·해설하세요.
5. **챕터 설계도(`chapters`)**: 뒤이어 작성할 Andrew Huberman 스타일 롱폼 팟캐스트 대본을 위해 4~6개의 논리적 챕터(① 문제의식과 훅 → ② 배경과 기존 통념 → ③ 핵심 메커니즘/방법론·논증 1 → ④ 핵심 증거/사례·판시 2 → ⑤ 한계·반론 및 비판적 검토 → ⑥ 실질적 적용과 종합)로 설계하세요.
"""


CHAPTER_SCRIPT_SYSTEM_INSTRUCTION = """당신은 스탠포드 신경과학자 Andrew Huberman 교수의 롱폼 팟캐스트처럼, 어려운 학술 논문·판례·경제 보고서를 **귀로 듣기만 해도 머릿속에 구조가 선명히 그려지도록** 풀어 설명하는 최고의 한국어 1인 지식 팟캐스트 호스트입니다.

[발화 스타일 및 품질 규칙]
1. **순수 발화용 한국어 텍스트만 작성**: 마크다운(`**`, `##`, `-`), 번호 매기기, 괄호 속 URL, 이모지, 무대 지시문(`(잠시 멈춤)`)을 절대 넣지 마세요.
2. **휴버만식 교육적 호흡 (Intuition → Mechanism → Evidence → Caveat)**:
   - 청취자가 직관적 질문에 몰입하게 만든 뒤, 핵심 메커니즘과 연구 설계(또는 법적 추론 단계)를 친절하고 논리적으로 차근차근 밟아 올라가세요.
   - 영문 학술·법학 원어가 나올 때는 발화 흐름을 깨지 않게 자연스럽게 풀어주세요 (예: "학계에서는 이를 메타인지, 영어로는 metacognition이라고 부르는데요, 쉽게 말해 내 머리가 지금 제대로 이해하고 있는지 한 발 떨어져서 점검하는 능력입니다").
3. **구체성과 엄밀성**: 추상적 미사여구로 분량을 늘리지 말고, 원문에 나온 구체적 실험 구조, 수치, 비교 사례, 반론, 철학적·현실적 함의를 깊이 있게 해설하세요.
4. **이전 챕터와의 자연스러운 연결**: 직전 챕터의 끝부분을 받아 부드럽게 이어가되, 앞선 내용을 중복 반복하지 마세요.
5. **목표 글자 수 준수**: 프롬프트에 제시된 목표 글자 수 범위를 충실히 채워 충분한 설명 깊이를 확보하세요.
"""


def _build_documents_context(docs: list[FetchedDocument]) -> str:
    """여러 문헌의 전문을 프롬프트 투입용 블록으로 포맷합니다."""
    blocks: list[str] = []
    # 여러 편일 때는 문서당 상한을 균등 분배해 총 입력 토큰 폭주 방지
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
                    "thinking_level": thinking_level,
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
    """Stage 1: 원문 전문을 분석해 웹 리더·Google Doc용 구조화 독해 노트와 챕터 설계도를 생성합니다."""
    selected_model = model or os.getenv("DEEP_NOTE_MODEL", DEFAULT_NOTE_MODEL)
    client = genai.Client()

    today_kst = datetime.now(KST).strftime("%Y년 %m월 %d일")
    mode_label = (
        "단일 대형 문헌 완전 해부 (Single Deep Dive)"
        if decision.mode == "single_deep_dive"
        else f"복수 문헌 맥락 종합 ({len(docs)}편 Multi-Synthesis)"
    )

    prompt = (
        f"오늘 날짜(KST): {today_kst}\n"
        f"테마: {theme.emoji} {theme.label_kr} | 세부 주제: {decision.sub_subject_kr}\n"
        f"구성 방식: {mode_label}\n"
        f"핵심 문제의식: {decision.unifying_angle_kr}\n\n"
        "아래 원문 텍스트를 정밀하게 독해하여 구조화된 비판적 독해 노트와 4~6개 챕터 설계도를 JSON으로 작성해 주세요.\n\n"
        + _build_documents_context(docs)
    )

    raw_json = _call_interactions_with_retry(
        client=client,
        model=selected_model,
        system_instruction=STUDY_NOTE_SYSTEM_INSTRUCTION,
        prompt=prompt,
        schema=DeepDiveStudyNote.model_json_schema(),
        max_output_tokens=16384,
        thinking_level="low",
        stage="구조화 독해 노트",
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
    20~60분 분량의 휴버만식 심층 오디오 대본을 조립합니다.
    """
    selected_model = model or os.getenv("DEEP_SCRIPT_MODEL", DEFAULT_SCRIPT_MODEL)
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

    client = genai.Client()
    docs_context = _build_documents_context(docs)
    today_title_date = datetime.now(KST).strftime("%Y.%m.%d")
    episode_title = f"[{theme.label_kr}] {note.episode_title_kr} | {today_title_date}"

    chapter_toc = "\n".join(
        f"- Chapter {ch.chapter_number}. {ch.title_kr}: {ch.focus_kr}"
        for ch in note.chapters
    )
    terms_hint = "\n".join(
        f"- {t.term_kr} ({t.term_en}): {t.explanation_kr}" for t in note.key_terms
    )

    # 1) 오프닝 생성
    print(f"      [Script] Opening 생성 중 (목표 ~{opening_target}자)...")
    opening_prompt = (
        f"에피소드 제목: {episode_title}\n"
        f"부제: {note.subtitle_kr}\n"
        f"핵심 테제: {note.executive_thesis_kr}\n"
        f"전체 챕터 구성:\n{chapter_toc}\n\n"
        f"청취자의 호기심을 단번에 붙잡는 지적인 오프닝을 공백 포함 약 {opening_target - 80}~{opening_target + 120}자로 작성하세요. "
        "오늘 다루는 원문의 출처·저자(또는 기관/법원)를 자연스럽게 소개하고, "
        "이 에피소드를 끝까지 들으면 어떤 통찰을 얻게 되는지 로드맵을 예고하세요."
    )
    raw_open = _call_interactions_with_retry(
        client=client,
        model=selected_model,
        system_instruction=CHAPTER_SCRIPT_SYSTEM_INSTRUCTION,
        prompt=opening_prompt,
        schema=ChapterNarrationOutput.model_json_schema(),
        max_output_tokens=4096,
        thinking_level="low",
        stage="대본 Opening",
        usage_tracker=usage_tracker,
    )
    opening_text = ChapterNarrationOutput.model_validate_json(raw_open).narration.strip()

    # 2) 챕터별 순차 생성 (TPM 한도 준수를 위해 호출 간 간격 확보)
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
            f"(목표 {min_c}~{max_c}자)..."
        )
        points_str = "\n".join(f"- {p}" for p in ch.key_points)
        chapter_prompt = (
            f"에피소드: {episode_title}\n"
            f"전체 챕터 목차:\n{chapter_toc}\n\n"
            f"=== 지금 작성할 챕터 ({idx}/{chapter_count}) ===\n"
            f"소제목: {ch.title_kr}\n"
            f"집중할 논증 흐름: {ch.focus_kr}\n"
            f"반드시 포함할 핵심 포인트:\n{points_str}\n\n"
            f"참고 핵심 용어:\n{terms_hint}\n\n"
            f"직전 섹션의 마지막 발화(자연스럽게 이어갈 것, 반복 금지):\n\"...{prev_tail}\"\n\n"
            f"목표 분량: 공백 포함 **약 {min_c}~{max_c}자**.\n"
            "아래 원문 근거를 충실히 활용해, 휴버만 교수처럼 원리와 증거·사례·한계를 명쾌하고 깊이 있게 풀어내는 "
            "순수 발화 대본을 JSON으로 작성해 주세요.\n\n"
            + docs_context
        )
        raw_ch = _call_interactions_with_retry(
            client=client,
            model=selected_model,
            system_instruction=CHAPTER_SCRIPT_SYSTEM_INSTRUCTION,
            prompt=chapter_prompt,
            schema=ChapterNarrationOutput.model_json_schema(),
            max_output_tokens=12288,
            thinking_level="low",
            stage=f"대본 Chapter {idx}",
            usage_tracker=usage_tracker,
        )
        ch_text = ChapterNarrationOutput.model_validate_json(raw_ch).narration.strip()
        rendered_chapters.append((ch.title_kr, ch_text))
        prev_tail = ch_text[-350:]

    # 3) 클로징 생성
    if sleep_sec > 0:
        time.sleep(min(sleep_sec, 10.0))
    print(f"      [Script] Closing 생성 중 (목표 ~{closing_target}자)...")
    takeaways_str = "\n".join(f"- {t}" for t in note.practical_takeaways_kr)
    closing_prompt = (
        f"에피소드: {episode_title}\n"
        f"실질적 시사점:\n{takeaways_str}\n"
        f"직전 챕터의 마지막 발화:\n\"...{prev_tail}\"\n\n"
        f"전체 논의를 깔끔하게 하나로 묶어주고, 청취자가 오늘 당장 곱씹어 볼 핵심 질문 하나를 남기며 "
        f"차분하게 마무리하는 클로징을 공백 포함 약 {closing_target - 80}~{closing_target + 120}자로 작성해 주세요."
    )
    raw_close = _call_interactions_with_retry(
        client=client,
        model=selected_model,
        system_instruction=CHAPTER_SCRIPT_SYSTEM_INSTRUCTION,
        prompt=closing_prompt,
        schema=ChapterNarrationOutput.model_json_schema(),
        max_output_tokens=4096,
        thinking_level="low",
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
