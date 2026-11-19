"""선별된 The Guardian 기사 전문을 바탕으로 10~13분 분량의 1인 라디오 앵커 롱폼 대본을 작성합니다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

from google import genai
from pydantic import BaseModel, Field

from guardian_fetcher import FullArticle


KST = timezone(timedelta(hours=9))


class LongformAudioScript(BaseModel):
    episode_title: str = Field(
        description="텔레그램 오디오 플레이어에 표시할 에피소드 제목 (예: '글로벌 모닝 브리핑 | 2026.11.19')"
    )
    part1_text: str = Field(
        description=(
            "전반부 방송 대본 (오프닝 인사 + 전반부 3개 기사 심층 해설). "
            "공백 포함 1,700자 ~ 2,100자 분량의 순수 발화용 한국어 텍스트"
        )
    )
    part2_text: str = Field(
        description=(
            "후반부 방송 대본 (자연스러운 전환 멘트 + 나머지 기사 심층 해설 + 클로징 인사). "
            "공백 포함 1,700자 ~ 2,100자 분량의 순수 발화용 한국어 텍스트"
        )
    )


SCRIPT_SYSTEM_INSTRUCTION = """당신은 한국의 프리미엄 아침 시사·경제 라디오 팟캐스트를 집필하는 수석 방송 작가입니다.
제공된 The Guardian 기사 **전문(Full Text)**을 깊이 있게 소화한 뒤, **1인 라디오 앵커가 10~13분 동안 여유 있고 차분하게 들려주는 심층 아침 브리핑 대본**을 작성하세요.

[분량 및 2부 분할 구성 — 매우 중요]
- 전체 대본 길이: 공백 포함 **3,400자 ~ 4,100자** (차분한 아나운서 낭독 속도 기준 약 10분 30초 ~ 13분).
- 음성 합성의 안정성을 위해 대본을 **Part 1(전반부)**과 **Part 2(후반부)**로 자연스럽게 나눕니다.
  - **part1_text (약 1,700~2,100자)**:
    - 따뜻한 아침 인사 + 오늘 다룰 핵심 흐름 요약(2~3문장)
    - 전반부 3개 기사(주로 경제·금융·테크/AI 중심)를 기사 전문의 구체적 배경, 수치, 이해관계자 시각, 시사점까지 충실하게 풀어 해설
  - **part2_text (약 1,700~2,100자)**:
    - 자연스러운 전환 멘트(예: "이어서 글로벌 시사와 과학·환경 소식도 짚어보겠습니다.")
    - 후반부 나머지 2~3개 기사를 같은 깊이로 해설
    - 차분하고 따뜻한 클로징 인사(2~3문장)

[기사 전문 활용 & 스토리텔링 원칙]
1. **피상적인 3줄 요약 금지**: 기사 전문에 담긴 핵심 수치, 구체적 사례, 전문가·관계자 인용의 맥락, "왜 지금 이 일이 중요한가"와 "앞으로 어떤 파급력이 있을까"를 귀로 듣기 쉽게 입체적으로 설명하세요.
2. **자연스러운 1인 라디오 구어체**: 청취자가 출근길에 이어폰으로 편하게 듣는 상황을 떠올리며 "~했습니다", "~인데요", "여기서 주목할 대목은~"처럼 부드럽고 신뢰감 있는 존댓말을 사용하세요.
3. **일관된 톤 유지**: 처음부터 끝까지 차분하고 지적인 **한 명의 뉴스 앵커**가 진행한다는 느낌을 유지하세요.

[발화 전용 텍스트(Verbatim) 절대 규칙]
- `part1_text`와 `part2_text`는 Gemini 3.8 TTS 모델이 **글자 그대로** 소리 내어 읽습니다.
- 마크다운 기호(`**`, `*`, `#`, `•`, `-`), URL, 섹션 번호 표기(`1.`), 괄호 속 무대 지시문(`(웃으며)`, `[잠시 멈춤]`)을 **절대 넣지 마세요**.
- 기사에서 다음 기사로 넘어갈 때 짧은 호흡이 필요하면 `<short pause>` 태그만 사용하세요 (파트당 3~4회 이내).
- 영어 헤드라인이나 영어 표현 공부를 억지로 끼워 넣지 마세요. 꼭 필요한 고유명사만 한국어 발음이나 자연스러운 표기로 매끄럽게 녹여내세요.
- 숫자와 퍼센트는 귀로 들었을 때 바로 이해되도록 자연스럽게 풀어 쓰세요(예: "약 3.5퍼센트", "약 120억 달러").
"""


def write_longform_script(
    articles: list[FullArticle],
    model: str | None = None,
) -> LongformAudioScript:
    """선별된 기사 전문 목록으로 10~13분 롱폼 1인 앵커 대본을 생성합니다."""
    if not articles:
        raise ValueError("대본을 작성할 기사가 없습니다.")

    selected_model = model or os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
    client = genai.Client(http_options={"api_version": "v1beta"})

    today_kst = datetime.now(KST).strftime("%Y년 %m월 %d일 (%a)")
    today_title_date = datetime.now(KST).strftime("%Y.%m.%d")

    article_blocks: list[str] = []
    for idx, art in enumerate(articles, start=1):
        byline_str = f" | 기자: {art.byline}" if art.byline else ""
        article_blocks.append(
            f"=== 기사 {idx} [{art.category_kr} / {art.section}{byline_str}] ===\n"
            f"제목: {art.title}\n"
            f"리드 요약: {art.trail_text}\n"
            f"본문 전문:\n{art.body_text}\n"
        )

    prompt = (
        f"오늘 날짜(KST): {today_kst}\n"
        f"에피소드 제목 형식 예시: 글로벌 모닝 브리핑 | {today_title_date}\n\n"
        f"아래 {len(articles)}편의 The Guardian 기사 전문을 바탕으로, "
        "10~13분 분량의 1인 라디오 뉴스 앵커 심층 브리핑 대본(Part 1, Part 2)을 JSON으로 작성해 주세요.\n\n"
        + "\n".join(article_blocks)
    )

    interaction = client.interactions.create(
        model=selected_model,
        system_instruction=SCRIPT_SYSTEM_INSTRUCTION,
        input=prompt,
        response_mime_type="application/json",
        response_format=LongformAudioScript.model_json_schema(),
        store=False,
    )

    raw_json = interaction.output_text
    if not raw_json:
        raise RuntimeError("방송 대본 생성 중 Gemini API로부터 빈 응답을 받았습니다.")

    return LongformAudioScript.model_validate_json(raw_json)


def format_script_preview(script: LongformAudioScript) -> str:
    """대본을 콘솔 출력 및 Artifact 저장용 텍스트로 포맷합니다."""
    p1_len = len(script.part1_text)
    p2_len = len(script.part2_text)
    total_len = p1_len + p2_len
    return (
        f"# {script.episode_title}\n"
        f"(총 약 {total_len}자 — Part 1: {p1_len}자 / Part 2: {p2_len}자)\n\n"
        f"## Part 1\n{script.part1_text.strip()}\n\n"
        f"## Part 2\n{script.part2_text.strip()}\n"
    )
