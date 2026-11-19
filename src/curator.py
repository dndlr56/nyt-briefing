"""Gemini Interactions API를 사용해 맞춤형 NYT 아침 브리핑을 큐레이션·번역합니다."""

from __future__ import annotations

import os
from typing import List

from google import genai
from pydantic import BaseModel, Field

from nyt_fetcher import Article


class KeyExpression(BaseModel):
    expression: str = Field(description="기사 제목이나 요약에서 뽑은 유용한 영어 표현 또는 경제·시사 용어")
    meaning_kr: str = Field(description="해당 영어 표현의 자연스러운 한국어 뜻과 짧은 맥락 설명")


class CuratedArticle(BaseModel):
    category_kr: str = Field(
        description="한국어 카테고리 라벨 (예: '경제·금융', '테크·AI', '글로벌 시사', '과학·건강')"
    )
    title_kr: str = Field(description="자연스럽고 명확한 한국어 번역 제목")
    title_en: str = Field(description="원문 영어 헤드라인 (수정 없이 그대로)")
    summary_bullets: List[str] = Field(
        min_length=3,
        max_length=3,
        description="핵심 내용을 담은 한국어 요약 3문장 (각 문장은 간결하고 명확하게)",
    )
    key_expressions: List[KeyExpression] = Field(
        min_length=1,
        max_length=2,
        description="영어 감각 유지를 위한 핵심 영어 표현 1~2개",
    )
    url: str = Field(description="입력 기사 목록의 원문 URL (반드시 그대로 복사)")


class DailyBriefing(BaseModel):
    morning_intro_kr: str = Field(
        description="오늘 아침 글로벌 뉴스의 핵심 흐름을 1~2문장으로 짚어주는 인사말"
    )
    articles: List[CuratedArticle] = Field(
        min_length=5,
        max_length=7,
        description="독자 맞춤형으로 엄선된 6개 내외(5~7개)의 주요 기사",
    )


SYSTEM_INSTRUCTION = """당신은 한국인 독자를 위한 뉴욕타임즈(NYT) 전문 모닝 브리핑 에디터입니다.
제공된 최근 NYT 기사 후보 목록에서 독자 프로필에 가장 잘 맞는 6개(최소 5개, 최대 7개) 기사를 엄선하여 한국어로 번역·요약하세요.

[독자 관심사 및 비중]
- 경제·금융·기업 동향·테크/AI: 약 50% (3개 내외) — 거시경제, 시장 흐름, 빅테크·AI 산업 인사이트 우선
- 글로벌 시사·외교·주요 정책: 약 30% (2개 내외) — 국제 정세 및 글로벌·경제 파급력이 큰 미국 핵심 정책
- 과학·기후·건강: 약 20% (1개 내외) — 흥미롭고 실질적인 과학·의료·기후 이슈

[제외할 기사 (노이즈 필터)]
- 과도하게 정파적인 미국 국내 정쟁 기사 (단, 관세·금리·규제·외교 등 글로벌·경제 영향이 큰 정책은 포함)
- 스포츠, 연예, 셀럽 가십, 미국 지역 단신 사건·사고

[작성 규칙]
1. 각 기사는 반드시 원문 영어 헤드라인(title_en)과 자연스러운 한국어 번역 제목(title_kr)을 함께 제공합니다.
2. summary_bullets는 반드시 정확히 3문장으로 작성하며, 제공된 기사 정보에 근거해 과장 없이 명확하게 요약합니다.
3. key_expressions에는 기사 제목이나 본문 요약에서 영어 감각을 유지하는 데 도움 되는 실전 영어 표현(숙어, 시사·경제 용어 등) 1~2개를 뽑아 한국어 뜻과 함께 정리합니다.
4. url은 입력으로 주어진 URL을 절대 변형하지 말고 그대로 사용합니다.
5. 중복 주제 기사는 피하고, 다양한 시각과 주제가 균형 잡히도록 구성합니다.
"""


def curate_briefing(
    articles: list[Article],
    model: str | None = None,
) -> DailyBriefing:
    """수집된 기사 목록을 Gemini에 전달해 맞춤형 브리핑을 생성합니다."""
    if not articles:
        raise ValueError("큐레이션할 NYT 기사가 없습니다.")

    selected_model = model or os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
    client = genai.Client(http_options={"api_version": "v1beta"})

    # 입력 토큰 효율을 위해 상위 60개 후보만 전달
    candidates = articles[:60]
    lines: list[str] = []
    for idx, art in enumerate(candidates, start=1):
        pub_str = art.published_at.strftime("%Y-%m-%d %H:%M UTC")
        lines.append(
            f"[{idx}] ({art.section} | {pub_str})\n"
            f"Title: {art.title}\n"
            f"Summary: {art.description or '(요약 없음)'}\n"
            f"URL: {art.url}\n"
        )

    prompt = (
        "아래는 최근 수집된 New York Times 주요 기사 후보 목록입니다.\n"
        "독자 프로필에 맞춰 6개(5~7개) 기사를 엄선해 오늘의 모닝 브리핑을 JSON으로 작성해 주세요.\n\n"
        + "\n".join(lines)
    )

    interaction = client.interactions.create(
        model=selected_model,
        system_instruction=SYSTEM_INSTRUCTION,
        input=prompt,
        response_mime_type="application/json",
        response_format=DailyBriefing.model_json_schema(),
        store=False,
    )

    raw_json = interaction.output_text
    if not raw_json:
        raise RuntimeError("Gemini API로부터 빈 응답을 받았습니다.")

    return DailyBriefing.model_validate_json(raw_json)
