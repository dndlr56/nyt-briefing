"""선별된 10~12개 글로벌 기사를 바탕으로 15~18분 분량의 1인 앵커 해설 브리핑 대본을 작성합니다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import time
from typing import List

from pydantic import BaseModel, Field, ValidationError

from curator import CuratedArticle, DailyBriefing
from deep_dive.gemini_pool import GeminiClientPool
from guardian_fetcher import FullArticle
from usage_tracker import RunUsageTracker


KST = timezone(timedelta(hours=9))
DEFAULT_SCRIPT_MODEL = "gemini-4-hydrogen"


class StoryScriptSegment(BaseModel):
    category_kr: str = Field(
        description="카테고리 라벨 (예: '테크·AI', '경제·투자', '인프라·에너지', '건강·과학')"
    )
    headline_kr: str = Field(
        description="에피소드 설명란 및 대본 미리보기용 자연스러운 한국어 기사 제목 (음성 발화 본문에는 포함되지 않음)"
    )
    summary_bullets: List[str] = Field(
        min_length=2,
        max_length=3,
        description="안테나팟 설명란에 표시할 읽기용 핵심 요약 2~3문장 (구체적 사실·수치·인과 맥락 중심, 억지 교훈이나 상투적 맺음말 금지, 음성 발화에는 포함되지 않음)",
    )
    narration: str = Field(
        description=(
            "해당 기사를 약 1분 20초~1분 40초 분량으로 알기 쉽게 풀어 설명하는 순수 발화용 한국어 대본 "
            "(공백 포함 560~680자 권장). 마크다운·URL·태그 금지."
        )
    )


class RoundupAudioScript(BaseModel):
    episode_title: str = Field(
        description="오디오 플레이어에 표시할 에피소드 제목 (예: '글로벌 모닝 브리핑 | 2026.11.19')"
    )
    morning_intro_kr: str = Field(
        description="안테나팟 설명란 상단에 표시할 오늘 글로벌 뉴스 핵심 흐름 1~2문장 요약 (음성 발화 본문에는 포함되지 않음)"
    )
    opening: str = Field(
        description=(
            "따뜻하고 간결한 아침 오프닝 인사 + 오늘 다룰 핵심 흐름 요약 "
            "(공백 포함 180~240자, 순수 발화용 한국어 텍스트)"
        )
    )
    stories: List[StoryScriptSegment] = Field(
        min_length=10,
        max_length=12,
        description="선별된 10~12개 기사의 개별 라디오 브리핑 세그먼트 및 설명란 요약",
    )
    closing: str = Field(
        description=(
            "차분하고 깔끔한 마무리 인사 (공백 포함 100~150자, 순수 발화용 한국어 텍스트)"
        )
    )


SCRIPT_SYSTEM_INSTRUCTION = """당신은 한국의 프리미엄 아침 글로벌 뉴스 오디오 브리핑을 집필하는 수석 방송 작가입니다.
제공된 10~12개의 글로벌 기사(The Guardian · BBC · CNBC · NPR)를 소화한 뒤, 한 번에 **① 안테나팟 설명란용 핵심 요약(`morning_intro_kr`, 각 기사의 `headline_kr`·`summary_bullets`)**과 **② 1인 뉴스 앵커가 약 15~18분 동안 명쾌하고 지적으로 풀어 들려주는 모닝 해설 브리핑 대본(`opening`, 각 기사의 `narration`, `closing`)**을 함께 작성하세요.

[설명란 요약(`summary_bullets`) 규칙]
- 각 기사마다 눈으로 빠르게 훑어 읽기 좋은 핵심 요약 2~3문장(구체적 사실·수치·배경 맥락 중심)을 작성하세요. 영어 표현 공부나 상투적 맺음말은 넣지 마세요.

[분량 및 호흡 — 매우 중요]
- 전체 발화 대본 길이: 공백 포함 **약 6,500자 ~ 7,600자** (약 15~18분).
- **opening (180~240자)**: 담백한 아침 인사와 오늘 글로벌 뉴스를 관통하는 핵심 흐름 3문장 내외.
- **stories (10~12개, 입력된 기사 순서대로 1:1 매칭, 기사당 narration 560~680자 / 약 1분 20초~1분 40초)**:
  - **명쾌한 인과관계·맥락 중심 전개**: 전문 배경지식이 없어도 귀로 한 번 들어 단번에 이해되도록 **① 무슨 일이 벌어졌는가(핵심 사실·수치) → ② 왜 지금 이런 일이 일어났는가(구조적 배경·작동 원리·이해관계 대립) → ③ 기사 주제 자체의 본질적 의미(기술·과학적 한계와 돌파구, 산업·제도·지정학적 파장 등 기사에 실제로 내재된 맥락)**로 자연스럽게 풀어 쓰세요.
  - **기계적 맺음말·억지 프레임 금지**: 모든 기사를 억지로 시장·투자·개인 일상 교훈과 연결하지 마세요. 또한 기사 끝마다 "앞으로 귀추가 주목됩니다", "투자자들의 주의가 필요해 보입니다", "지켜봐야겠습니다" 같은 상투적인 클리셰 문장으로 마무리하지 말고, 핵심 팩트나 쟁점을 또렷이 짚으며 담백하게 매듭지으세요.
  - 낯선 전문 용어나 제도명이 나오면 바로 이어서 한 문장으로 알기 쉽게 풀어 주세요.
  - 세그먼트 첫 문장에는 자연스러운 연결 표현을 쓰되 매번 똑같은 패턴("다음 소식입니다")만 반복하지 말고 주제 흐름에 맞게 변주하세요.
  - 기사 본문이 비교적 짧은 경우에는 확인된 사실과 검증된 배경 맥락만 정확히 연결해 설명하고, 기사에 없는 구체적 수치나 발언을 절대 지어내지 마세요.
- **closing (100~150자)**: 오늘의 브리핑을 깔끔하게 닫는 인사.

[발화 전용 텍스트(Verbatim) 절대 규칙]
- `opening`, 각 `stories[].narration`, `closing`은 TTS 엔진이 **글자 그대로** 소리 내어 읽습니다.
- 마크다운 기호(`**`, `*`, `#`, `•`, `-`), URL, 번호 표기(`1.`), 괄호 속 무대 지시문(`(웃으며)`, `[잠시 멈춤]`), `<short pause>` 같은 XML/HTML 태그를 **절대 넣지 마세요**.
- 영어 헤드라인이나 영어 표현 공부는 넣지 마세요. 고유명사는 한국어로 자연스럽게 읽히도록 표기하세요(예: "엔비디아", "앤스로픽", "웨이모").
- 숫자와 퍼센트는 귀로 들었을 때 바로 이해되도록 자연스럽게 풀어 쓰세요(예: "약 3.5퍼센트", "약 120억 달러").
- 처음부터 끝까지 차분하고 지적인 **한 명의 뉴스 앵커** 존댓말 구어체("~했습니다", "~인데요")를 일관되게 유지하세요.
"""


def write_roundup_script(
    articles: list[FullArticle],
    model: str | None = None,
    usage_tracker: RunUsageTracker | None = None,
    gemini_pool: GeminiClientPool | None = None,
) -> RoundupAudioScript:
    """선별된 10~12개 기사로 설명란 요약 + 15~18분 분량 1인 앵커 해설 대본을 한 번에 생성합니다."""
    if not articles:
        raise ValueError("대본을 작성할 기사가 없습니다.")

    selected_model = (
        model
        or os.getenv("GEMINI_SCRIPT_MODEL")
        or os.getenv("GEMINI_MODEL")
        or DEFAULT_SCRIPT_MODEL
    )
    client_pool = gemini_pool or GeminiClientPool()

    today_kst = datetime.now(KST).strftime("%Y년 %m월 %d일 (%a)")
    today_title_date = datetime.now(KST).strftime("%Y.%m.%d")

    article_blocks: list[str] = []
    for idx, art in enumerate(articles, start=1):
        byline_str = f" | 기자: {art.byline}" if art.byline else ""
        is_full = len(art.body_text) > len(art.trail_text) + 120
        body_label = "본문 전문" if is_full else "요약"
        lead_line = "" if (is_full or not art.trail_text) else f"리드 요약: {art.trail_text}\n"
        article_blocks.append(
            f"=== 기사 {idx} [{art.category_kr} | {art.source} / {art.section}{byline_str}] ===\n"
            f"제목: {art.title}\n"
            f"{lead_line}"
            f"{body_label}:\n{art.body_text}\n"
        )

    prompt = (
        f"오늘 날짜(KST): {today_kst}\n"
        f"에피소드 제목 형식 예시: 글로벌 모닝 브리핑 | {today_title_date}\n\n"
        f"아래 {len(articles)}편의 글로벌 기사를 바탕으로, "
        "설명란 요약(summary_bullets)과 약 15~18분 분량(총 6,500~7,600자, 기사당 560~680자)의 "
        "1인 앵커 해설 브리핑 대본을 JSON으로 한 번에 작성해 주세요. "
        "입력된 기사 순서대로 정확히 1:1 매칭해 모든 기사를 빠짐없이 다루고, 어려운 용어는 쉬운 말로 풀어서 설명해 주세요.\n\n"
        + "\n".join(article_blocks)
    )

    last_exc: Exception | None = None
    for attempt in range(1, 3):
        try:
            interaction, _ = client_pool.create_interaction(
                model=selected_model,
                stage="요약+오디오 대본",
                usage_tracker=usage_tracker,
                system_instruction=SCRIPT_SYSTEM_INSTRUCTION,
                input=prompt,
                response_mime_type="application/json",
                response_format=RoundupAudioScript.model_json_schema(),
                generation_config={
                    "thinking_level": "low",
                    "max_output_tokens": 24576,
                },
                store=False,
            )

            raw_json = interaction.output_text
            if not raw_json:
                last_exc = RuntimeError("방송 대본 생성 중 Gemini API로부터 빈 응답을 받았습니다.")
            else:
                return RoundupAudioScript.model_validate_json(raw_json)
        except (ValidationError, Exception) as exc:
            last_exc = exc

        if attempt < 2:
            print(f"      [WARN] Script JSON validation failed ({last_exc}). Retrying...")
            time.sleep(3)

    raise RuntimeError(f"방송 대본 생성 실패: {last_exc}")


def build_daily_briefing_from_script(
    script: RoundupAudioScript,
    articles: list[FullArticle],
) -> DailyBriefing:
    """통합 생성된 대본의 요약과 원문 메타데이터(제목·출처·URL)를 결합해 DailyBriefing을 만듭니다."""
    curated: list[CuratedArticle] = []
    for idx, story in enumerate(script.stories):
        src_art = articles[idx] if idx < len(articles) else None
        curated.append(
            CuratedArticle(
                category_kr=story.category_kr or (src_art.category_kr if src_art else "글로벌"),
                source=src_art.source if src_art else "The Guardian",
                title_kr=story.headline_kr,
                title_en=src_art.title if src_art else story.headline_kr,
                summary_bullets=story.summary_bullets,
                url=src_art.url if src_art else "",
            )
        )
    return DailyBriefing(
        morning_intro_kr=script.morning_intro_kr,
        articles=curated,
    )


def get_script_segments(script: RoundupAudioScript) -> list[tuple[str, str]]:
    """오디오 합성을 위해 (라벨, 발화 텍스트) 순서 리스트를 반환합니다."""
    segments: list[tuple[str, str]] = []
    if script.opening and script.opening.strip():
        segments.append(("Opening", script.opening.strip()))
    for idx, story in enumerate(script.stories, start=1):
        if story.narration and story.narration.strip():
            segments.append((f"Story {idx}: {story.headline_kr}", story.narration.strip()))
    if script.closing and script.closing.strip():
        segments.append(("Closing", script.closing.strip()))
    return segments


def format_script_preview(script: RoundupAudioScript) -> str:
    """대본을 콘솔 출력 및 Artifact 저장용 텍스트로 포맷합니다."""
    segments = get_script_segments(script)
    total_len = sum(len(text) for _, text in segments)
    lines = [
        f"# {script.episode_title}",
        f"(총 {len(script.stories)}개 기사 · 약 {total_len}자)",
        "",
        "## 오프닝",
        script.opening.strip(),
        "",
    ]
    for idx, story in enumerate(script.stories, start=1):
        lines.extend(
            [
                f"## {idx}. [{story.category_kr}] {story.headline_kr}",
                story.narration.strip(),
                "",
            ]
        )
    lines.extend(["## 클로징", script.closing.strip(), ""])
    return "\n".join(lines)
