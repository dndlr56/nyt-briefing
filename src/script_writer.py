"""선별된 10~12개 글로벌 기사를 바탕으로 15~18분 분량의 1인 앵커 해설 브리핑 대본을 작성합니다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import time
from typing import List

from google import genai
from pydantic import BaseModel, Field, ValidationError

from curator import CuratedArticle, DailyBriefing
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
        description="안테나팟 설명란에 표시할 읽기용 핵심 요약 2~3문장 (사실·수치·시사점 중심, 음성 발화에는 포함되지 않음)",
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


SCRIPT_SYSTEM_INSTRUCTION = """당신은 한국의 프리미엄 아침 글로벌 뉴스 라디오 팟캐스트를 집필하는 수석 방송 작가입니다.
제공된 10~12개의 글로벌 기사(The Guardian 전문 + NYT 보완)를 소화한 뒤, 한 번에 **① 안테나팟 설명란용 핵심 요약(`morning_intro_kr`, 각 기사의 `headline_kr`·`summary_bullets`)**과 **② 1인 뉴스 앵커가 약 15~18분 동안 명쾌하고 친절하게 풀어 들려주는 모닝 해설 브리핑 대본(`opening`, 각 기사의 `narration`, `closing`)**을 함께 작성하세요.

[설명란 요약(`summary_bullets`) 규칙]
- 각 기사마다 눈으로 빠르게 훑어 읽기 좋은 간결한 핵심 요약 2~3문장(사실·수치·시사점 중심)을 작성하세요. 영어 표현 공부는 넣지 마세요.

[분량 및 호흡 — 매우 중요]
- 전체 발화 대본 길이: 공백 포함 **약 6,500자 ~ 7,600자** (약 15~18분).
- **opening (180~240자)**: 따뜻한 아침 인사와 오늘 글로벌 뉴스의 큰 흐름 3문장 내외.
- **stories (10~12개, 입력된 기사 순서대로 1:1 매칭, 기사당 narration 560~680자 / 약 1분 20초~1분 40초)**:
  - **쉬운 원리 해설 중심**: 전문 용어를 어렵게 나열하지 말고, 비전공자(문과 출신 초보 투자자)가 귀로만 들어도 *"아, 그래서 이게 중요하구나!"* 하고 고개를 끄덕일 수 있도록 **① 핵심 사실 → ② 왜 이런 일이 벌어졌는지 쉬운 인과관계·배경 맥락 → ③ 이해관계자/반론 시각 → ④ 시장·투자·일상에 주는 시사점** 순서로 자연스럽게 풀어 쓰세요.
  - 전문 용어가 꼭 필요하면 바로 이어서 한 문장으로 쉽게 풀어서 설명하세요(예: "돈을 빌리는 비용, 즉 금리가 올라갈 수밖에 없다는 설명입니다").
  - 각 세그먼트 첫 문장에 자연스러운 전환어를 넣어 흐름이 부드럽게 이어지게 하세요 (예: "먼저 인공지능 업계 소식입니다.", "다음으로 거시경제 소식 살펴보겠습니다.", "마지막으로 의학계에서 들려온 반가운 연구 결과입니다.").
  - NYT RSS 요약 기사처럼 본문이 짧은 경우에는 확인된 사실과 일반적인 거시 맥락만 정확히 연결해 설명하고, 기사에 없는 구체적 수치나 발언을 절대 지어내지 마세요.
- **closing (100~150자)**: 오늘의 브리핑을 깔끔하게 닫는 인사.

[발화 전용 텍스트(Verbatim) 및 TTS 호흡 절대 규칙]
- `opening`, 각 `stories[].narration`, `closing`은 TTS 엔진이 **글자 그대로** 소리 내어 읽습니다.
- **짧은 단문 위주 작성 (매우 중요)**: 눈으로 읽는 긴 문어체 복문을 피하고, **한 문장은 가급적 25~45자 내외**로 짧고 명쾌하게 끊어 쓰세요. 한 문장에 절을 2~3개씩 길게 이어 붙이지 말고, 인과관계나 배경 설명도 **한 문장에 정보 하나씩** 2~3개 단문으로 나눠 쓰세요.
- **호흡용 쉼표(`,`) 적극 활용**: 앵커가 실제로 숨을 고르는 지점(전환어 뒤, 긴 주어 뒤, 조건·배경 구절 뒤, 수치·고유명사 앞뒤)에는 평소 글쓰기보다 **쉼표(`,`)를 넉넉하게 넣어** TTS가 띄어쓰기 없이 붙여서 읽지 않도록 하세요.
- 마크다운 기호(`**`, `*`, `#`, `•`, `-`), URL, 번호 표기(`1.`), 괄호 속 무대 지시문(`(웃으며)`, `[잠시 멈춤]`), `<short pause>` 같은 XML/HTML 태그를 **절대 넣지 마세요**.
- 영어 헤드라인이나 영어 표현 공부는 넣지 마세요. 고유명사는 한국어로 자연스럽게 읽히도록 표기하세요(예: "엔비디아", "앤스로픽", "웨이모").
- 숫자와 퍼센트는 귀로 들었을 때 바로 이해되도록 자연스럽게 풀어 쓰세요(예: "약 3.5퍼센트", "약 120억 달러").
- 처음부터 끝까지 차분하고 지적인 **한 명의 뉴스 앵커** 존댓말 구어체("~했습니다", "~인데요")를 일관되게 유지하세요.
"""


def write_roundup_script(
    articles: list[FullArticle],
    model: str | None = None,
    usage_tracker: RunUsageTracker | None = None,
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
    client = genai.Client(http_options={"api_version": "v1beta"})

    today_kst = datetime.now(KST).strftime("%Y년 %m월 %d일 (%a)")
    today_title_date = datetime.now(KST).strftime("%Y.%m.%d")

    article_blocks: list[str] = []
    for idx, art in enumerate(articles, start=1):
        byline_str = f" | 기자: {art.byline}" if art.byline else ""
        body_label = "본문 전문" if art.source == "The Guardian" else "RSS 요약(보완)"
        article_blocks.append(
            f"=== 기사 {idx} [{art.category_kr} | {art.source} / {art.section}{byline_str}] ===\n"
            f"제목: {art.title}\n"
            f"리드 요약: {art.trail_text}\n"
            f"{body_label}:\n{art.body_text}\n"
        )

    prompt = (
        f"오늘 날짜(KST): {today_kst}\n"
        f"에피소드 제목 형식 예시: 글로벌 모닝 브리핑 | {today_title_date}\n\n"
        f"아래 {len(articles)}편의 글로벌 기사를 바탕으로, "
        "설명란 요약(summary_bullets)과 약 15~18분 분량(총 6,500~7,600자, 기사당 560~680자)의 "
        "1인 앵커 해설 브리핑 대본을 JSON으로 한 번에 작성해 주세요. "
        "입력된 기사 순서대로 정확히 1:1 매칭해 모든 기사를 빠짐없이 다루고, 짧은 단문과 쉼표로 읽기 편하게 써 주세요.\n\n"
        + "\n".join(article_blocks)
    )

    last_exc: Exception | None = None
    for attempt in range(1, 4):
        interaction = client.interactions.create(
            model=selected_model,
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
        if usage_tracker is not None:
            usage_tracker.add_interaction(interaction, stage="요약+오디오 대본", model=selected_model)

        raw_json = interaction.output_text
        if not raw_json:
            last_exc = RuntimeError("방송 대본 생성 중 Gemini API로부터 빈 응답을 받았습니다.")
        else:
            try:
                return RoundupAudioScript.model_validate_json(raw_json)
            except ValidationError as exc:
                last_exc = exc

        if attempt < 3:
            print(f"      [WARN] Script JSON parse failed (attempt {attempt}/3): {last_exc}. Retrying...")
            time.sleep(2 * attempt)

    raise RuntimeError(f"방송 대본 JSON 파싱 3회 실패: {last_exc}")


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
