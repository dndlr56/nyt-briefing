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
        description="한국어 카테고리 라벨 (예: '테크·AI', '경제·투자', '글로벌 기업', '인프라·에너지', '건강·과학', '글로벌 시사')"
    )
    selection_reason_kr: str = Field(
        description="이 기사를 선정한 핵심 이유 1문장"
    )


class SelectionResult(BaseModel):
    selected: List[SelectedArticleRef] = Field(
        min_length=10,
        max_length=12,
        description="독자 프로필에 맞춰 엄선된 10~12개 글로벌 기사 목록",
    )


class CuratedArticle(BaseModel):
    category_kr: str = Field(
        description="한국어 카테고리 라벨 (예: '테크·AI', '경제·투자', '글로벌 기업', '인프라·에너지', '건강·과학', '글로벌 시사')"
    )
    source: str = Field(
        description="기사 출처 (예: 'The Guardian' 또는 'NYT')"
    )
    title_kr: str = Field(description="자연스럽고 명확한 한국어 번역 제목")
    title_en: str = Field(description="원문 영어 헤드라인 (수정 없이 그대로)")
    summary_bullets: List[str] = Field(
        min_length=2,
        max_length=3,
        description="읽기 편하게 정리한 핵심 요약 2~3문장 (사실·배경·시사점 중심)",
    )
    url: str = Field(description="입력 기사 목록의 원문 URL (반드시 그대로 복사)")


class DailyBriefing(BaseModel):
    morning_intro_kr: str = Field(
        description="오늘 아침 글로벌 뉴스의 핵심 흐름을 1~2문장으로 짚어주는 인사말"
    )
    articles: List[CuratedArticle] = Field(
        min_length=10,
        max_length=12,
        description="읽기용으로 깔끔하게 정리한 10~12개의 글로벌 주요 기사",
    )


SELECTION_SYSTEM_INSTRUCTION = """당신은 한국인 독자(초보 주식 투자자, 로스쿨 진학 예정, 글로벌 트렌드·경제·테크·건강 관심)를 위한 **글로벌 모닝 뉴스 브리핑** 수석 에디터입니다.
제공된 The Guardian(전문 제공, 메인)과 NYT(미국·글로벌 보완) 기사 후보 목록에서 **11개(최소 10개, 최대 12개)** 기사를 엄선하세요.

[강력 선호 주제 — 최우선 선정]
1. **빅테크·AI 산업 동향**: 엔비디아, OpenAI, Anthropic, 애플, MS, 구글 등 AI 경쟁, 신기술, 규제, 산업 판도 변화
2. **글로벌 경제·투자·기업 실적**: IPO, 주식시장 흐름, 자산 이동, 거시경제(금리·관세·무역), 글로벌 주요 기업 실적 및 리더십 변화
3. **모빌리티·인프라·에너지**: 자율주행(Waymo 등), 대형 IT/클라우드/항공 인프라 장애, 데이터센터 전력·에너지 전환 등 파급력이 큰 사건
4. **실질적 의학·건강·뇌과학 돌파구**: 알츠하이머·암 등 신약 개발, 뇌과학·인지·건강 관련 주목할 연구 성과
5. **국제 정세 핵심 이슈**: 글로벌 경제·공급망·안보에 직접적 영향을 주는 주요 국제 뉴스 (단, 지엽적 내부 정쟁 제외)

[엄격 제외 대상 — 노이즈 필터]
- **영국·유럽·미국 내부의 지엽적 정쟁**: 정당 간 공방, 이민·행정 스캔들, 지방 의회/총리실 내부 다툼, 정치인 간 소송
- **영국 로컬 연예·방송·노조·문화계 소식** (예: BBC 내부 인사, 현지 배우/방송 노조 협상 등)
- **평범한 국제기구 사회 통계·캠페인, 역사 가십, 단순 흥미 위주 단신, 스포츠, 리뷰, 퀴즈**
- 한국 국내 뉴스는 다른 채널로 듣고 있으므로 글로벌 이슈에만 집중하세요.

[소스 배분 및 정렬 규칙]
1. 기사 전문(Full Text)을 읽을 수 있는 **The Guardian 기사를 메인(최소 8~10개)**으로 우선 선정하세요.
2. 미국 빅테크·월가·글로벌 핵심 이슈 중 Guardian 목록에 빠졌거나 NYT가 훨씬 중요하게 다룬 주제가 있다면 **NYT 기사로 최대 2~3개 보완**하세요.
3. 서로 다른 매체가 같은 사건을 다룬 경우 중복 선정하지 말고 더 충실한 기사(전문이 있는 Guardian 우선) 1건만 고르세요.
4. `article_id`는 입력 목록에 적힌 값을 **한 글자도 바꾸지 말고 그대로** 복사하세요.
5. 배치 순서: [테크·AI / 경제·투자 / 글로벌 기업]을 앞쪽(1~6번), [인프라·에너지 / 글로벌 시사 / 건강·과학]을 뒤쪽(7~12번)에 배치하세요.
"""


SUMMARY_SYSTEM_INSTRUCTION = """당신은 한국인 독자를 위한 글로벌 뉴스 모닝 브리핑 에디터입니다.
제공된 10~12개 글로벌 기사를 읽고, **눈으로 빠르게 훑어 읽기 좋은 텍스트 브리핑**으로 정리하세요.

[작성 규칙]
1. 각 기사는 한국어 번역 제목(title_kr), 원문 영어 헤드라인(title_en), 출처(source), 원문 URL(url)을 함께 제공합니다.
2. summary_bullets는 아나운서 구어체 대본이 아니라, **읽기 편한 간결한 개조식/서술형 요약 2~3문장**으로 작성하세요. 핵심 사실, 수치, 시사점을 담되 장황하지 않게 쓰세요.
3. 영어 표현 공부나 단어 풀이는 넣지 마세요.
4. url과 source는 입력으로 주어진 값을 절대 변형하지 말고 그대로 사용합니다.
"""


def select_articles(
    candidates: list[ArticleCandidate],
    model: str | None = None,
) -> list[tuple[ArticleCandidate, str]]:
    """1단계: Guardian(메인) + NYT(보완) 후보 목록에서 10~12개 글로벌 기사를 선별합니다."""
    if not candidates:
        raise ValueError("선별할 기사 후보가 없습니다.")

    selected_model = model or os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
    client = genai.Client(http_options={"api_version": "v1beta"})

    pool = candidates[:85]
    by_id = {c.article_id: c for c in pool}

    lines: list[str] = []
    for idx, art in enumerate(pool, start=1):
        pub_str = art.published_at.strftime("%Y-%m-%d %H:%M UTC")
        wc_str = f"Words: {art.wordcount} (Full-Text 가능)" if art.source == "The Guardian" else "NYT RSS 보완"
        lines.append(
            f"[{idx}] article_id: {art.article_id}\n"
            f"Source: {art.source} | Section: {art.section} | Published: {pub_str} | {wc_str}\n"
            f"Title: {art.title}\n"
            f"Summary: {art.trail_text or '(요약 없음)'}\n"
        )

    prompt = (
        "아래는 최근 수집된 The Guardian(메인) 및 NYT(보완) 글로벌 기사 후보 목록입니다.\n"
        "독자 프로필에 맞춰 오늘 아침 15~18분 브리핑으로 다룰 11개(10~12개) 기사를 선별해 JSON으로 반환해 주세요.\n\n"
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
    """2단계(읽기용 텍스트 브리핑): 선별된 8~10개 기사를 읽기 편한 요약으로 정리합니다."""
    if not articles:
        raise ValueError("요약할 기사가 없습니다.")

    selected_model = model or os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
    client = genai.Client(http_options={"api_version": "v1beta"})

    blocks: list[str] = []
    for idx, art in enumerate(articles, start=1):
        body_label = "Full Text" if art.source == "The Guardian" else "RSS Summary"
        blocks.append(
            f"[{idx}] 카테고리: {art.category_kr} | 출처: {art.source} ({art.section})\n"
            f"Title: {art.title}\n"
            f"URL: {art.url}\n"
            f"{body_label}:\n{art.body_text}\n"
        )

    prompt = (
        f"아래는 1단계에서 엄선된 {len(articles)}개의 글로벌 기사입니다.\n"
        "각 기사를 눈으로 읽기 편한 한국어 브리핑 요약(2~3문장)으로 정리해 JSON으로 작성해 주세요.\n\n"
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
