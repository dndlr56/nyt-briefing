"""Deep Dive Knowledge Briefing 파이프라인의 핵심 데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field


@dataclass
class SourceCandidate:
    """수집 단계에서 확보한 1차 문헌 후보(논문·보고서·에세이·판례)."""

    title: str
    url: str
    source_name: str
    theme_id: str
    published: str = ""
    authors: str = ""
    abstract: str = ""
    pdf_url: str = ""
    pmc_id: str = ""
    source_kind: str = "article"  # paper | report | essay | case_law | review
    sub_topic_hint: str = ""
    authority_tier: int = 1  # 1 = top-tier flagship, 2 = authoritative specialist


@dataclass
class FetchedDocument:
    """전문(Full-text) 및 선택적 원문 PDF 바이트까지 확보된 문헌."""

    candidate: SourceCandidate
    full_text: str
    text_source: str  # pdf | pmc_xml | html | manual_pdf | abstract_fallback
    pdf_bytes: bytes | None = None
    pdf_filename: str = ""
    char_count: int = 0
    truncated_for_llm: bool = False

    def __post_init__(self) -> None:
        if self.char_count == 0:
            self.char_count = len(self.full_text)


class CuratedPick(BaseModel):
    candidate_index: int = Field(
        description="입력된 후보 목록의 1-based 인덱스 번호"
    )
    why_selected_kr: str = Field(
        description="이 문헌을 선별한 이유 1~2문장 (권위·지적 깊이·하위 주제 적합성 중심)"
    )


class CurationDecision(BaseModel):
    mode: Literal["single_deep_dive", "multi_synthesis"] = Field(
        description=(
            "대형 문헌·판례·보고서 1편을 완전 해부할 경우 'single_deep_dive', "
            "공통 쟁점을 가진 소형 논문 2~3편을 엮을 경우 'multi_synthesis'"
        )
    )
    unifying_angle_kr: str = Field(
        description="오늘 회차를 관통하는 핵심 질문 또는 문제의식 1~2문장"
    )
    sub_subject_kr: str = Field(
        description="세부 하위 주제 태그 (예: '학습·메타인지', 'AI 정렬·안전성', '행동재무학', '헌법·사법심사')"
    )
    picks: list[CuratedPick] = Field(
        min_length=1,
        max_length=3,
        description="선별된 1~3편의 문헌 (single_deep_dive는 1편, multi_synthesis는 2~3편)",
    )


class KeyTerm(BaseModel):
    term_kr: str = Field(description="한국어 핵심 개념/용어")
    term_en: str = Field(description="정확한 영문 학술·법학 원어")
    explanation_kr: str = Field(description="문과 전공자·초보 투자자도 직관적으로 이해할 수 있는 명쾌한 풀이 1~2문장")


class KeyQuote(BaseModel):
    quote_en: str = Field(description="원문에서 직접 발췌한 핵심 영문(또는 한국어 원문) 문장 1~2문장")
    translation_kr: str = Field(description="자연스러운 한국어 번역 및 맥락 해설")
    source_label: str = Field(description="출처 문헌명 또는 저자/판례명")


class ComparisonRow(BaseModel):
    dimension: str = Field(description="비교 기준 또는 분석 축 (예: 핵심 가설, 방법론, 주요 근거, 한계)")
    detail_kr: str = Field(description="해당 축에 대한 구조화된 핵심 내용")


class ChapterBlueprint(BaseModel):
    chapter_number: int = Field(description="챕터 순번 (1부터 시작)")
    title_kr: str = Field(description="챕터 소제목 (한국어)")
    focus_kr: str = Field(description="이 챕터에서 반드시 다뤄야 할 논증 흐름·데이터·사례 요약 (2~4문장)")
    key_points: list[str] = Field(
        min_length=2,
        max_length=5,
        description="발화 시 빠뜨리지 말아야 할 구체적 수치·메커니즘·개념 2~5개",
    )


class DeepDiveStudyNote(BaseModel):
    episode_title_kr: str = Field(
        description="눈에 확 들어오는 지적이고 간결한 한국어 제목 (날짜 접두사 제외)"
    )
    subtitle_kr: str = Field(
        description="부제 — 독자가 이 글에서 얻을 핵심 통찰 1문장"
    )
    tldr_bullets: list[str] = Field(
        min_length=3,
        max_length=4,
        description="텔레그램 알림 및 상단 요약용 핵심 요약 3~4개 (구체적 사실·논증·시사점 포함)",
    )
    executive_thesis_kr: str = Field(
        description="저자(또는 법원)의 핵심 테제와 문제의식 요약 (3~5문장)"
    )
    background_context_kr: str = Field(
        description="이 연구/보고서/판례가 나온 학술적·시대적 배경과 기존 통념 (3~5문장)"
    )
    methodology_and_argument_kr: str = Field(
        description="핵심 방법론, 실험 설계, 데이터, 또는 법적 추론(Legal Reasoning) 구조 상세 분석 (5~8문장)"
    )
    core_findings_kr: list[str] = Field(
        min_length=3,
        max_length=6,
        description="가장 중요한 발견·결론·판시 사항 3~6개 (수치·메커니즘 명시)",
    )
    comparison_table: list[ComparisonRow] = Field(
        min_length=3,
        max_length=6,
        description="웹 리더 및 Google Doc에 표로 렌더링할 핵심 구조 요약/비교표 3~6행",
    )
    limitations_and_critique_kr: list[str] = Field(
        min_length=2,
        max_length=4,
        description="방법론적 한계, 반론, 철학적·비판적 쟁점 2~4개",
    )
    practical_takeaways_kr: list[str] = Field(
        min_length=2,
        max_length=4,
        description="학습·자기계발 / 투자·경제 감각 / 법적 사고 / 건강 관리 관점의 실질적 시사점 2~4개",
    )
    key_terms: list[KeyTerm] = Field(
        min_length=4,
        max_length=8,
        description="원문 이해에 필수적인 핵심 학술·법학·경제 영문 원어 용어집 4~8개",
    )
    key_quotes: list[KeyQuote] = Field(
        min_length=2,
        max_length=4,
        description="원문에서 반드시 음미할 만한 핵심 원문 인용 2~4개",
    )
    chapters: list[ChapterBlueprint] = Field(
        min_length=4,
        max_length=6,
        description="오디오 딥다이브를 구성할 4~6개 챕터 설계도 (도입→배경→핵심 메커니즘/논증→비판·한계→종합 시사점)",
    )


class ChapterNarrationOutput(BaseModel):
    narration: str = Field(
        description=(
            "마크다운·불릿·URL·태그 없이 귀로 듣기만 해도 논리가 선명히 그려지는 "
            "순수 한국어 강의·팟캐스트 발화 대본"
        )
    )


@dataclass
class DeepDiveScript:
    episode_title: str
    opening: str
    chapters: list[tuple[str, str]]  # [(chapter_title_kr, narration_text), ...]
    closing: str

    def to_segments(self) -> list[tuple[str, str]]:
        segs: list[tuple[str, str]] = []
        if self.opening.strip():
            segs.append(("Opening", self.opening.strip()))
        for idx, (title, narration) in enumerate(self.chapters, start=1):
            if narration.strip():
                segs.append((f"Chapter {idx}: {title}", narration.strip()))
        if self.closing.strip():
            segs.append(("Closing", self.closing.strip()))
        return segs

    @property
    def total_chars(self) -> int:
        return sum(len(text) for _, text in self.to_segments())
