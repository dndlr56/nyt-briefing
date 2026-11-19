"""큐레이션된 NYT 브리핑을 1인 라디오 뉴스 앵커용 방송 대본으로 각색합니다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import List

from google import genai
from pydantic import BaseModel, Field

from curator import DailyBriefing


KST = timezone(timedelta(hours=9))


class ScriptSegment(BaseModel):
    section_label: str = Field(
        description="세그먼트 구분 라벨 (예: '오프닝', '경제·금융', '테크·AI', '글로벌 시사', '클로징')"
    )
    style: str = Field(
        description=(
            "Gemini 3.8 TTS speech_metadata.style에 전달할 짧은 영어 발화 연출 지시 "
            "(예: 'warm, crisp morning radio news anchor, natural conversational pace')"
        )
    )
    text: str = Field(
        description=(
            "앵커가 실제로 소리 내어 읽을 순수 한국어 방송 대본. "
            "마크다운, URL, 괄호 지시문 없이 자연스러운 구어체로 작성"
        )
    )


class AudioBriefingScript(BaseModel):
    episode_title: str = Field(
        description="텔레그램 오디오 플레이어에 표시할 에피소드 제목 (예: 'NYT 모닝 브리핑 | 2026.11.19')"
    )
    segments: List[ScriptSegment] = Field(
        min_length=4,
        max_length=10,
        description="오프닝 + 기사별 브리핑 + 클로징으로 구성된 방송 대본 세그먼트 목록",
    )


SCRIPT_SYSTEM_INSTRUCTION = """당신은 한국의 출근길 라디오 뉴스 쇼를 담당하는 베테랑 방송 작가입니다.
주어진 뉴욕타임즈(NYT) 큐레이션 기사 목록을 바탕으로, **1인 라디오 뉴스 앵커가 3~5분 안에 깔끔하고 속도감 있게 읽어주는 아침 오디오 브리핑 대본**을 작성하세요.

[분량 및 구성]
- 전체 대본 길이: 공백 포함 **1,200자 ~ 1,500자** (자연스러운 아나운서 속도 기준 약 3분 30초 ~ 4분 30초).
- 세그먼트 구성:
  1. 오프닝 (1개): 오늘 날짜 인사 + 오늘 브리핑에서 다룰 핵심 흐름 한 줄 예고 (2~3문장)
  2. 기사 브리핑 (기사당 1개 또는 관련 기사 묶음): 각 기사의 핵심 사실과 배경·의미를 귀로 들었을 때 바로 이해되도록 3~4문장으로 매끄럽게 풀어 설명
  3. 클로징 (1개): 짧은 마무리 인사와 활기찬 하루 응원 (2문장)

[방송 대본 작성 규칙 — 매우 중요]
1. **귀로 듣는 구어체**: 텍스트 요약을 기계적으로 나열하지 마세요. "첫 번째 소식입니다.", "이어서 테크 업계 소식 짚어보겠습니다.", "마지막으로~" 같은 자연스러운 연결 멘트를 넣으세요.
2. **발화 전용 텍스트(Verbatim)**: `text` 필드는 TTS 모델이 글자 그대로 소리 내어 읽습니다.
   - 마크다운 기호(`**`, `*`, `#`, `•`, `-`), URL, 대괄호/소괄호 무대 지시문(`(웃으며)`, `[잠시 멈춤]`)을 **절대 넣지 마세요**.
   - 꼭 필요한 호흡 전환에만 `<short pause>` 태그를 세그먼트당 1~2회 이내로만 사용하세요.
3. **숫자·외래어·영어 표현 처리**:
   - 기사에 있는 영어 원문 헤드라인을 억지로 전부 읽지 마세요.
   - 꼭 짚어주면 좋은 핵심 영어 표현은 전체 브리핑에서 1~2개만 자연스럽게 녹여 설명하세요.
   - 약어와 숫자는 귀로 들었을 때 헷갈리지 않게 자연스럽게 풀어 쓰세요(예: "약 3.5퍼센트", "미 연준").
4. **세그먼트별 `style` 필드**:
   - 반드시 간결한 영어로 작성하세요 (10단어 이내).
   - 오프닝: `"warm, energetic morning radio news anchor, clear articulation"`
   - 본문(경제/테크/시사): `"crisp, calm, authoritative Korean news anchor, steady natural pace"`
   - 가벼운 과학/건강 소식: `"friendly, engaging radio host, conversational tone"`
   - 클로징: `"warm, upbeat morning sign-off, gentle smile in voice"`
"""


def write_audio_script(
    briefing: DailyBriefing,
    model: str | None = None,
) -> AudioBriefingScript:
    """DailyBriefing을 1인 라디오 뉴스 앵커용 방송 대본으로 각색합니다."""
    selected_model = model or os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
    client = genai.Client(http_options={"api_version": "v1beta"})

    today_kst = datetime.now(KST).strftime("%Y년 %m월 %d일 (%a)")
    today_title_date = datetime.now(KST).strftime("%Y.%m.%d")

    article_lines: list[str] = []
    for idx, art in enumerate(briefing.articles, start=1):
        bullets = "\n".join(f"  - {b}" for b in art.summary_bullets)
        exprs = ", ".join(
            f"{e.expression} ({e.meaning_kr})" for e in art.key_expressions
        )
        article_lines.append(
            f"[{idx}] 카테고리: {art.category_kr}\n"
            f"한국어 제목: {art.title_kr}\n"
            f"원문 제목: {art.title_en}\n"
            f"요약:\n{bullets}\n"
            f"핵심 표현: {exprs}\n"
        )

    prompt = (
        f"오늘 날짜(KST): {today_kst}\n"
        f"에피소드 제목 형식 예시: NYT 모닝 브리핑 | {today_title_date}\n"
        f"오늘의 오프닝 참고 요약: {briefing.morning_intro_kr}\n\n"
        "아래 큐레이션된 기사 목록을 바탕으로 3~5분 분량의 1인 라디오 뉴스 아침 브리핑 대본을 JSON으로 작성해 주세요.\n\n"
        + "\n".join(article_lines)
    )

    interaction = client.interactions.create(
        model=selected_model,
        system_instruction=SCRIPT_SYSTEM_INSTRUCTION,
        input=prompt,
        response_mime_type="application/json",
        response_format=AudioBriefingScript.model_json_schema(),
        store=False,
    )

    raw_json = interaction.output_text
    if not raw_json:
        raise RuntimeError("방송 대본 생성 중 Gemini API로부터 빈 응답을 받았습니다.")

    return AudioBriefingScript.model_validate_json(raw_json)


def format_script_preview(script: AudioBriefingScript) -> str:
    """대본을 콘솔 출력 및 파일 저장용 텍스트로 포맷합니다."""
    total_chars = sum(len(seg.text) for seg in script.segments)
    lines = [
        f"# {script.episode_title}",
        f"(총 {len(script.segments)}개 세그먼트 / 약 {total_chars}자)",
        "",
    ]
    for idx, seg in enumerate(script.segments, start=1):
        lines.append(f"[{idx}. {seg.section_label}] (style: {seg.style})")
        lines.append(seg.text)
        lines.append("")
    return "\n".join(lines).strip()
