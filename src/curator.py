"""Gemini Interactions API를 사용해 The Guardian 기사를 2단계로 선별·요약합니다."""

from __future__ import annotations

import os
import time
from typing import List

from google import genai
from pydantic import BaseModel, Field, ValidationError

from guardian_fetcher import ArticleCandidate, FullArticle
from usage_tracker import (
    RunUsageTracker,
    get_paid_api_key_if_available,
    is_rate_limit_error,
    is_transient_server_error,
    try_switch_to_paid_key,
)


class SelectedArticleRef(BaseModel):
    candidate_index: int = Field(
        description="입력 후보 목록의 1-based 정수 번호 (예: 1, 2, 3)"
    )
    category_kr: str = Field(
        description="한국어 카테고리 라벨 (예: '테크·AI', '경제·투자', '글로벌 기업', '인프라·에너지', '건강·과학', '글로벌 시사')"
    )


class SelectionResult(BaseModel):
    selected: List[SelectedArticleRef] = Field(
        min_length=10,
        max_length=12,
        description="독자 프로필에 맞춰 엄선된 10~12개 글로벌 기사 목록",
    )


class CompactSummaryItem(BaseModel):
    article_index: int = Field(
        description="입력 기사 목록의 1-based 정수 번호"
    )
    title_kr: str = Field(description="자연스럽고 명확한 한국어 번역 제목")
    summary_bullets: List[str] = Field(
        min_length=2,
        max_length=3,
        description="읽기 편하게 정리한 핵심 요약 2~3문장 (사실·배경·시사점 중심)",
    )


class CompactSummaryResult(BaseModel):
    morning_intro_kr: str = Field(
        description="오늘 아침 글로벌 뉴스의 핵심 흐름을 1~2문장으로 짚어주는 인사말"
    )
    articles: List[CompactSummaryItem] = Field(
        min_length=10,
        max_length=12,
        description="입력 기사 순서대로 정리한 10~12개의 글로벌 주요 기사 요약",
    )


class CuratedArticle(BaseModel):
    category_kr: str = Field(
        description="한국어 카테고리 라벨 (예: '테크·AI', '경제·투자', '글로벌 기업', '인프라·에너지', '건강·과학', '글로벌 시사')"
    )
    source: str = Field(
        description="기사 출처 (예: 'The Guardian' 또는 'BBC')"
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
제공된 **The Guardian · BBC · CNBC · NPR** (4개 매체 모두 전문 확보 가능) 기사 후보 목록에서 **11개(최소 10개, 최대 12개)** 기사를 엄선하세요.

[강력 선호 주제 — 최우선 선정]
1. **빅테크·AI 산업 동향**: 엔비디아, OpenAI, Anthropic, 애플, MS, 구글 등 AI 경쟁, 신기술, 규제, 산업 판도 변화
2. **글로벌 경제·투자·기업 실적**: IPO, 주식시장 흐름, 자산 이동, 거시경제(금리·관세·무역), 글로벌 주요 기업 실적 및 리더십 변화
3. **모빌리티·인프라·에너지**: 자율주행(Waymo 등), 대형 IT/클라우드/항공 인프라 장애, 데이터센터 전력·에너지 전환 등 파급력이 큰 사건
4. **실질적 의학·건강·뇌과학 돌파구**: 알츠하이머·암 등 신약 개발, 뇌과학·인지·건강 관련 주목할 연구 성과
5. **국제 정세 핵심 이슈**: 글로벌 경제·공급망·안보에 직접적 영향을 주는 주요 국제 뉴스 (단, 지엽적 내부 정쟁 제외)

[엄격 제외 대상 — 노이즈 필터]
- **영국·유럽·미국 내부의 지엽적 정쟁**: 정당 간 공방, 이민·행정 스캔들, 지방 의회/총리실 내부 다툼, 정치인 간 소송
- **영국/미국 로컬 연예·방송·노조·문화계 소식** (예: 방송사 내부 인사, 현지 배우/노조 협상 등)
- **평범한 국제기구 사회 통계·캠페인, 역사 가십, 단순 흥미 위주 단신, 스포츠, 리뷰, 퀴즈**
- 한국 국내 뉴스는 다른 채널로 듣고 있으므로 글로벌 이슈에만 집중하세요.

[소스 배분 및 중복 방지 규칙]
1. **매체 간 균형**: 단일 매체가 전체의 과반(6개 이상)을 독식하지 않도록 **The Guardian(유럽·글로벌), BBC(지정학·속보), CNBC(미국 시장·거시·테크), NPR(미국 정책·과학)**을 고르게 배분하세요.
2. **오늘 후보 풀 내 1사건 1기사 원칙**: 서로 다른 매체가 오늘 동시간대에 본질적으로 같은 사건을 보도한 경우, 중복 선정하지 말고 가장 분석이 깊고 팩트가 풍부한 **단 1편**만 고르세요. (단, 다른 사건이나 뚜렷한 별개 논점의 기사는 제외하지 마세요.)
3. `article_id`는 입력 목록에 적힌 값을 **한 글자도 바꾸지 말고 그대로** 복사하세요.
4. 배치 순서: [테크·AI / 경제·투자 / 글로벌 기업]을 앞쪽(1~6번), [인프라·에너지 / 글로벌 시사 / 건강·과학]을 뒤쪽(7~12번)에 배치하세요.
"""


SUMMARY_SYSTEM_INSTRUCTION = """당신은 한국인 독자를 위한 글로벌 뉴스 모닝 브리핑 에디터입니다.
제공된 10~12개 글로벌 기사를 읽고, **눈으로 빠르게 훑어 읽기 좋은 텍스트 브리핑**으로 정리하세요.

[작성 규칙]
1. 각 기사는 한국어 번역 제목(title_kr), 원문 영어 헤드라인(title_en), 출처(source), 원문 URL(url)을 함께 제공합니다.
2. summary_bullets는 아나운서 구어체 대본이 아니라, **읽기 편한 간결한 개조식/서술형 요약 2~3문장**으로 작성하세요. 핵심 사실, 수치, 시사점을 담되 장황하지 않게 쓰세요.
3. 영어 표현 공부나 단어 풀이는 넣지 마세요.
4. url과 source는 입력으로 주어진 값을 절대 변형하지 말고 그대로 사용합니다.
"""


DEFAULT_SELECTION_MODEL = "gemini-4-hydrogen"
DEFAULT_SUMMARY_MODEL = "gemini-4-hydrogen"


def select_articles(
    candidates: list[ArticleCandidate],
    model: str | None = None,
    usage_tracker: RunUsageTracker | None = None,
) -> list[tuple[ArticleCandidate, str]]:
    """1단계: Guardian + BBC + CNBC + NPR 후보 목록에서 10~12개 글로벌 기사를 선별합니다."""
    if not candidates:
        raise ValueError("선별할 기사 후보가 없습니다.")

    selected_model = (
        model
        or os.getenv("GEMINI_SELECTION_MODEL")
        or os.getenv("GEMINI_MODEL")
        or DEFAULT_SELECTION_MODEL
    )
    client = genai.Client(http_options={"api_version": "v1beta", "retry_options": {"attempts": 1}})

    pool = candidates[:85]

    lines: list[str] = []
    for idx, art in enumerate(pool, start=1):
        pub_str = art.published_at.strftime("%Y-%m-%d %H:%M UTC")
        wc_str = f"Words: {art.wordcount} (Full-Text 가능)" if art.source == "The Guardian" else "HTML Full-Text 가능"
        lines.append(
            f"[{idx}] Source: {art.source} | Section: {art.section} | Published: {pub_str} | {wc_str}\n"
            f"Title: {art.title}\n"
            f"Summary: {art.trail_text or '(요약 없음)'}\n"
        )

    prompt = (
        "아래는 최근 수집된 The Guardian · BBC · CNBC · NPR 글로벌 기사 후보 목록입니다.\n"
        "독자 프로필에 맞춰 오늘 아침 15~18분 브리핑으로 다룰 11개(10~12개) 기사를 매체 균형 있게 선별해 "
        "`candidate_index`(1-based 정수 번호)와 `category_kr`을 JSON으로 반환해 주세요.\n\n"
        + "\n".join(lines)
    )

    last_exc: Exception | None = None
    result: SelectionResult | None = None
    free_503_waits = [5, 15, 30]
    free_503_attempts = 0
    for attempt in range(1, 7):
        try:
            interaction = client.interactions.create(
                model=selected_model,
                system_instruction=SELECTION_SYSTEM_INSTRUCTION,
                input=prompt,
                response_mime_type="application/json",
                response_format=SelectionResult.model_json_schema(),
                generation_config={
                    "thinking_level": "low",
                    "max_output_tokens": 4096,
                },
                store=False,
            )
            if usage_tracker is not None:
                usage_tracker.add_interaction(interaction, stage="기사 선별", model=selected_model)

            raw_json = interaction.output_text
            if not raw_json:
                last_exc = RuntimeError("1단계 기사 선별 중 Gemini API로부터 빈 응답을 받았습니다.")
            else:
                result = SelectionResult.model_validate_json(raw_json)
                break
        except (ValidationError, Exception) as exc:
            last_exc = exc

        if attempt < 6:
            if is_rate_limit_error(last_exc):
                if attempt >= 2:
                    paid_key = try_switch_to_paid_key()
                    if paid_key:
                        client = genai.Client(api_key=paid_key, http_options={"api_version": "v1beta", "retry_options": {"attempts": 1}})
                        continue
                wait_sec = 20
            elif is_transient_server_error(last_exc):
                if free_503_attempts < len(free_503_waits):
                    wait_sec = free_503_waits[free_503_attempts]
                    free_503_attempts += 1
                    print(
                        f"      [WARN] Stage 1 무료 티어 503 과부하 ({free_503_attempts}/{len(free_503_waits)}) -> "
                        f"{wait_sec}초 후 재시도..."
                    )
                    time.sleep(wait_sec)
                    continue
                paid_key = get_paid_api_key_if_available()
                if paid_key:
                    os.environ["GEMINI_FALLBACK_USED"] = "1"
                    print("      [FAILOVER-ONCE] Stage 1 무료 티어 503 지속 -> 이번 호출만 유료 1회 우회")
                    client = genai.Client(api_key=paid_key, http_options={"api_version": "v1beta", "retry_options": {"attempts": 1}})
                    continue
                wait_sec = 15
            else:
                wait_sec = 3 * attempt
            print(
                f"      [WARN] Stage 1 failed (attempt {attempt}/5): {last_exc}. "
                f"Retrying in {wait_sec}s..."
            )
            time.sleep(wait_sec)

    if result is None:
        raise RuntimeError(f"1단계 기사 선별 실패: {last_exc}")

    selected_pairs: list[tuple[ArticleCandidate, str]] = []
    seen_indices: set[int] = set()

    for ref in result.selected:
        if 1 <= ref.candidate_index <= len(pool) and ref.candidate_index not in seen_indices:
            seen_indices.add(ref.candidate_index)
            selected_pairs.append((pool[ref.candidate_index - 1], ref.category_kr))

    if not selected_pairs:
        raise RuntimeError("선별된 candidate_index가 후보 목록 범위를 벗어났습니다.")

    return selected_pairs


def summarize_full_articles(
    articles: list[FullArticle],
    model: str | None = None,
    usage_tracker: RunUsageTracker | None = None,
) -> DailyBriefing:
    """2단계(텍스트 전용 모드): 선별된 10~12개 기사를 읽기 편한 요약으로 정리합니다."""
    if not articles:
        raise ValueError("요약할 기사가 없습니다.")

    selected_model = (
        model
        or os.getenv("GEMINI_SUMMARY_MODEL")
        or os.getenv("GEMINI_MODEL")
        or DEFAULT_SUMMARY_MODEL
    )
    client = genai.Client(http_options={"api_version": "v1beta", "retry_options": {"attempts": 1}})

    blocks: list[str] = []
    for idx, art in enumerate(articles, start=1):
        body_label = "Full Text" if len(art.body_text) > len(art.trail_text) + 120 else "Summary"
        blocks.append(
            f"[{idx}] 카테고리: {art.category_kr} | 출처: {art.source} ({art.section})\n"
            f"Title: {art.title}\n"
            f"{body_label}:\n{art.body_text}\n"
        )

    prompt = (
        f"아래는 1단계에서 엄선된 {len(articles)}개의 글로벌 기사입니다.\n"
        "각 기사를 눈으로 읽기 편한 한국어 브리핑 요약(2~3문장)으로 정리해 `article_index`(1-based)와 함께 JSON으로 작성해 주세요.\n\n"
        + "\n".join(blocks)
    )

    last_exc: Exception | None = None
    for attempt in range(1, 5):
        try:
            interaction = client.interactions.create(
                model=selected_model,
                system_instruction=SUMMARY_SYSTEM_INSTRUCTION,
                input=prompt,
                response_mime_type="application/json",
                response_format=CompactSummaryResult.model_json_schema(),
                generation_config={
                    "thinking_level": "low",
                    "max_output_tokens": 12288,
                },
                store=False,
            )
            if usage_tracker is not None:
                usage_tracker.add_interaction(interaction, stage="텍스트 요약", model=selected_model)

            raw_json = interaction.output_text
            if not raw_json:
                last_exc = RuntimeError("2단계 기사 요약 중 Gemini API로부터 빈 응답을 받았습니다.")
            else:
                compact = CompactSummaryResult.model_validate_json(raw_json)
                curated_items: list[CuratedArticle] = []
                seen_idx: set[int] = set()
                for item in compact.articles:
                    if 1 <= item.article_index <= len(articles) and item.article_index not in seen_idx:
                        seen_idx.add(item.article_index)
                        src_art = articles[item.article_index - 1]
                        curated_items.append(
                            CuratedArticle(
                                category_kr=src_art.category_kr,
                                source=src_art.source,
                                title_kr=item.title_kr,
                                title_en=src_art.title,
                                summary_bullets=item.summary_bullets,
                                url=src_art.url,
                            )
                        )
                return DailyBriefing(
                    morning_intro_kr=compact.morning_intro_kr,
                    articles=curated_items,
                )
        except (ValidationError, Exception) as exc:
            last_exc = exc

        if attempt < 4:
            if is_rate_limit_error(last_exc):
                if attempt >= 2:
                    paid_key = try_switch_to_paid_key()
                    if paid_key:
                        client = genai.Client(api_key=paid_key, http_options={"api_version": "v1beta", "retry_options": {"attempts": 1}})
                        continue
                wait_sec = 20
            else:
                wait_sec = 3 * attempt
            print(
                f"      [WARN] Stage 2 summary failed (attempt {attempt}/4): {last_exc}. "
                f"Retrying in {wait_sec}s..."
            )
            time.sleep(wait_sec)

    raise RuntimeError(f"2단계 기사 요약 실패: {last_exc}")
