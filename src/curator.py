"""Gemini Interactions API를 사용해 The Guardian 기사를 2단계로 선별·요약합니다."""

from __future__ import annotations

import os
from typing import List

from google import genai
from pydantic import BaseModel, Field

from guardian_fetcher import ArticleCandidate, FullArticle


class SelectedArticleRef(BaseModel):
    article_id: str = Field(
        description="입력 후보 목록의 정확한 article_id (절대 변형하지 말고 그대로 복사)"
    )
    category_kr: str = Field(
        description="한국어 카테고리 라벨 (예: '경제·금융', '테크·AI', '글로벌 시사', '과학·기후')"
    )
    selection_reason_kr: str = Field(
        description="이 기사를 선정한 핵심 이유 1문장"
    )


class SelectionResult(BaseModel):
    selected: List[SelectedArticleRef] = Field(
        min_length=5,
        max_length=6,
        description="독자 프로필에 맞춰 엄선된 5~6개 기사 목록",
    )


class CuratedArticle(BaseModel):
    category_kr: str = Field(
        description="한국어 카테고리 라벨 (예: '경제·금융', '테크·AI', '글로벌 시사', '과학·기후')"
    )
    title_kr: str = Field(description="자연스럽고 명확한 한국어 번역 제목")
    title_en: str = Field(description="원문 영어 헤드라인 (수정 없이 그대로)")
    summary_bullets: List[str] = Field(
        min_length=3,
        max_length=4,
        description="기사 전문에 근거한 핵심 내용 요약 3~4문장 (구체적 배경과 시사점 포함)",
    )
    url: str = Field(description="입력 기사 목록의 원문 URL (반드시 그대로 복사)")


class DailyBriefing(BaseModel):
    morning_intro_kr: str = Field(
        description="오늘 아침 글로벌 뉴스의 핵심 흐름을 1~2문장으로 짚어주는 인사말"
    )
    articles: List[CuratedArticle] = Field(
        min_length=5,
        max_length=6,
        description="기사 전문을 바탕으로 요약한 5~6개의 주요 기사",
    )


SELECTION_SYSTEM_INSTRUCTION = """당신은 한국인 독자를 위한 글로벌 시사·경제 모닝 브리핑 수석 에디터입니다.
제공된 The Guardian 최근 기사 후보 목록에서 독자 프로필에 가장 잘 맞고 깊이 있게 다룰 가치가 있는 **6개(최소 5개, 최대 6개)** 기사를 엄선하세요.

[독자 관심사 및 비중]
- 경제·금융·기업 동향·테크/AI: 약 50% (3개 내외) — 거시경제, 시장 흐름, 빅테크·AI 산업 변화와 시사점 우선
- 글로벌 시사·외교·주요 정책: 약 30% (2개 내외) — 국제 정세 및 글로벌·경제 파급력이 큰 핵심 정책
- 과학·기후·건강: 약 20% (1개 내외) — 실질적이고 흥미로운 과학·의료·환경 이슈

[제외할 기사 (노이즈 필터)]
- 영국/미국 내 지엽적인 정쟁이나 지역 사건·사고 (글로벌·경제적 파급력이 큰 정책은 포함)
- 스포츠, 연예, 셀럽 가십, 단순 리뷰·퀴즈·단신

[선별 규칙]
1. article_id는 입력 목록에 적힌 값을 **한 글자도 바꾸지 말고 그대로** 복사하세요.
2. 중복 주제를 피하고, 분량(wordcount)이 충분해 심층 오디오 브리핑으로 풀어내기 좋은 기사를 우선 선정하세요.
3. 경제·테크 기사를 앞쪽(1~3번), 글로벌 시사·과학 기사를 뒤쪽(4~6번)에 배치하면 자연스럽습니다.
"""


SUMMARY_SYSTEM_INSTRUCTION = """당신은 한국인 독자를 위한 글로벌 뉴스 모닝 브리핑 에디터입니다.
제공된 The Guardian 기사 **전문(Full Text)**을 읽고, 각 기사의 핵심 사실·배경·시사점을 한국어 3~4문장으로 명확하게 요약하세요.

[작성 규칙]
1. 각 기사는 한국어 번역 제목(title_kr)과 원문 영어 헤드라인(title_en)을 함께 제공합니다.
2. summary_bullets는 기사 전문의 구체적 사실·수치·맥락을 반영해 3~4문장으로 작성합니다.
3. 영어 표현 공부나 단어 풀이는 넣지 마세요.
4. url은 입력으로 주어진 URL을 절대 변형하지 말고 그대로 사용합니다.
"""


def select_articles(
    candidates: list[ArticleCandidate],
    model: str | None = None,
) -> list[tuple[ArticleCandidate, str]]:
    """1단계: 제목과 짧은 요약만으로 심층 브리핑할 5~6개 기사를 선별합니다."""
    if not candidates:
        raise ValueError("선별할 Guardian 기사 후보가 없습니다.")

    selected_model = model or os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
    client = genai.Client(http_options={"api_version": "v1beta"})

    pool = candidates[:60]
    by_id = {c.article_id: c for c in pool}

    lines: list[str] = []
    for idx, art in enumerate(pool, start=1):
        pub_str = art.published_at.strftime("%Y-%m-%d %H:%M UTC")
        lines.append(
            f"[{idx}] article_id: {art.article_id}\n"
            f"Section: {art.section} | Published: {pub_str} | Words: {art.wordcount}\n"
            f"Title: {art.title}\n"
            f"Summary: {art.trail_text or '(요약 없음)'}\n"
        )

    prompt = (
        "아래는 최근 수집된 The Guardian 주요 기사 후보 목록입니다.\n"
        "독자 프로필에 맞춰 심층 브리핑으로 다룰 6개(5~6개) 기사를 선별해 JSON으로 반환해 주세요.\n\n"
        + "\n".join(lines)
    )

    interaction = client.interactions.create(
        model=selected_model,
        system_instruction=SELECTION_SYSTEM_INSTRUCTION,
        input=prompt,
        response_mime_type="application/json",
        response_format=SelectionResult.model_json_schema(),
        store=False,
    )

    raw_json = interaction.output_text
    if not raw_json:
        raise RuntimeError("1단계 기사 선별 중 Gemini API로부터 빈 응답을 받았습니다.")

    result = SelectionResult.model_validate_json(raw_json)
    selected_pairs: list[tuple[ArticleCandidate, str]] = []
    seen: set[str] = set()

    for ref in result.selected:
        candidate = by_id.get(ref.article_id)
        if candidate and candidate.article_id not in seen:
            seen.add(candidate.article_id)
            selected_pairs.append((candidate, ref.category_kr))

    if not selected_pairs:
        raise RuntimeError("선별된 article_id가 후보 목록과 일치하지 않습니다.")

    return selected_pairs


def summarize_full_articles(
    articles: list[FullArticle],
    model: str | None = None,
) -> DailyBriefing:
    """2단계(텍스트 브리핑용): 선별된 기사 전문을 바탕으로 한국어 텍스트 요약을 생성합니다."""
    if not articles:
        raise ValueError("요약할 기사 전문이 없습니다.")

    selected_model = model or os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
    client = genai.Client(http_options={"api_version": "v1beta"})

    blocks: list[str] = []
    for idx, art in enumerate(articles, start=1):
        blocks.append(
            f"[{idx}] 카테고리: {art.category_kr} ({art.section})\n"
            f"Title: {art.title}\n"
            f"URL: {art.url}\n"
            f"Full Text:\n{art.body_text}\n"
        )

    prompt = (
        "아래는 1단계에서 엄선된 The Guardian 기사 전문입니다.\n"
        "각 기사의 핵심 내용을 한국어로 명확하게 요약해 JSON으로 작성해 주세요.\n\n"
        + "\n".join(blocks)
    )

    interaction = client.interactions.create(
        model=selected_model,
        system_instruction=SUMMARY_SYSTEM_INSTRUCTION,
        input=prompt,
        response_mime_type="application/json",
        response_format=DailyBriefing.model_json_schema(),
        store=False,
    )

    raw_json = interaction.output_text
    if not raw_json:
        raise RuntimeError("2단계 기사 요약 중 Gemini API로부터 빈 응답을 받았습니다.")

    return DailyBriefing.model_validate_json(raw_json)
