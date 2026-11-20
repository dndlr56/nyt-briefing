"""Deep Dive Knowledge Briefing 파이프라인의 핵심 데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


CoreThemeId = Literal[
    "ai_tech",
    "science_mind",
    "health_medicine",
    "econ_geopolitics",
    "law_cases",
]
SpeakerId = Literal["S1", "S2", "S3"]
DialogueFormatType = Literal[
    "host_and_expert",
    "two_panelists",
    "host_and_two_panelists",
    "three_panelists",
]


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
    source_kind: str = "article"  # paper | report | essay | case_law | review | legal_column
    sub_topic_hint: str = ""
    authority_tier: int = 1  # 1 = top-tier flagship, 2 = authoritative specialist
    doi: str = ""
    cited_by_count: int = 0  # 총 피인용 수 (OpenAlex / Europe PMC / Semantic Scholar)
    influential_citations: int = 0  # 실질적 핵심 인용 수 (Semantic Scholar)
    fwci: float | None = None  # 분야·연도 보정 인용 지수 (OpenAlex FWCI, 1.0=평균)
    top_percentile_label: str = ""  # 분야·연도 정규화 상위 백분위 (예: 'Top 1%', 'Top 10%')


@dataclass
class FetchedDocument:
    """전문(Full-text) 및 선택적 원문 PDF 바이트까지 확보된 문헌."""

    candidate: SourceCandidate
    full_text: str
    text_source: str  # pdf | pmc_xml | html | core_oa | manual_pdf
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
        description="이 문헌을 선별한 이유 1~2문장 (권위·학술적/실무적 중요도 중심)"
    )


class CurationDecision(BaseModel):
    mode: Literal["single_deep_dive", "multi_synthesis"] = Field(
        description=(
            "대형 문헌·판례·보고서 1편을 충실히 번역·요약할 경우 'single_deep_dive', "
            "공통 주제를 다룬 논문 2~3편을 함께 정리할 경우 'multi_synthesis'"
        )
    )
    unifying_angle_kr: str = Field(
        description="선별된 문헌이 다루는 핵심 주제 1~2문장"
    )
    sub_subject_kr: str = Field(
        description="세부 주제 태그 (예: '치매 예방·공중보건', '학습·기억', '대한민국 헌법·판례')"
    )
    picks: list[CuratedPick] = Field(
        min_length=1,
        max_length=3,
        description="선별된 1~3편의 문헌 (single_deep_dive는 1편, multi_synthesis는 2~3편)",
    )


class SummarySection(BaseModel):
    heading_kr: str = Field(
        description="원문의 주요 장·절 또는 핵심 논점을 반영한 자연스러운 한국어 소제목 (불필요한 영문 병기 금지)"
    )
    content_kr: str = Field(
        description=(
            "원문 해당 부분의 논지, 구체적 수치, 근거, 사례를 충실하게 번역·요약한 자연스러운 한국어 본문 "
            "(4~8문장, AI 자의적 추측·확대해석 금지)"
        )
    )


class KeyQuote(BaseModel):
    quote_original: str = Field(
        description="원문에서 직접 발췌한 핵심 문장 1~2문장 (원문 언어 그대로 정확히 인용)"
    )
    translation_kr: str = Field(
        description="자연스럽고 정확한 한국어 번역"
    )
    source_label: str = Field(description="출처 문헌명 또는 저자/판례명")


class SummaryTableRow(BaseModel):
    item: str = Field(description="원문에 등장하는 항목·요인·쟁점명 (자연스러운 한국어)")
    detail_kr: str = Field(description="원문에 근거한 핵심 수치·근거·내용 요약")


class SpeakerProfile(BaseModel):
    speaker_id: SpeakerId = Field(
        description="화자 ID ('S1'=여성 목소리, 'S2'=남성 목소리 1, 'S3'=남성/여성 목소리 2)"
    )
    name_kr: str = Field(
        description="대화 중 부르기 자연스러운 짧은 한국어 이름 (예: S1='지수', S2='현수', S3='인준')"
    )
    role_title_kr: str = Field(
        description="역할 라벨 (예: '사회자', '패널', '역학·예방 담당 패널', '다수의견 해설 패널')"
    )
    perspective_kr: str = Field(
        description="이 화자가 원문에서 주로 맡아 설명하거나 대변할 영역/관점 1문장 (반드시 원문 근거 기반)"
    )


class DialogueCast(BaseModel):
    format_type: DialogueFormatType = Field(
        description=(
            "글의 성격에 맞는 최적 대화 포맷: "
            "'host_and_expert'(사회자1+패널1), 'two_panelists'(패널2), "
            "'host_and_two_panelists'(사회자1+패널2 — 상반된 두 관점/다수·반대의견/다학제), "
            "'three_panelists'(패널3 — 3개 축 대형 보고서·복수 논문 종합)"
        )
    )
    format_reason_kr: str = Field(
        description="이 문헌의 특성상 이 화자 구성을 선택한 이유 1문장"
    )
    speakers: list[SpeakerProfile] = Field(
        min_length=2,
        max_length=3,
        description="실제 참여하는 2명(S1, S2) 또는 3명(S1, S2, S3)의 화자 구성",
    )


class ChapterBlueprint(BaseModel):
    chapter_number: int = Field(description="챕터 순번 (1부터 시작)")
    title_kr: str = Field(description="챕터 소제목 (자연스러운 한국어)")
    focus_kr: str = Field(description="이 챕터에서 원문 내용을 바탕으로 전달할 핵심 흐름 (2~4문장)")
    key_points: list[str] = Field(
        min_length=2,
        max_length=5,
        description="원문에 근거해 빠뜨리지 말아야 할 구체적 수치·사실·논점 2~5개",
    )


class DeepDiveStudyNote(BaseModel):
    primary_theme_id: CoreThemeId = Field(
        description=(
            "이 문헌이 가장 정확히 속하는 5대 핵심 아카이브 분야 중 하나: "
            "'ai_tech'(AI·기술·사회), 'science_mind'(종합과학·뇌인지), "
            "'health_medicine'(의학·건강·장수), 'econ_geopolitics'(경제·금융·지정학), "
            "'law_cases'(대한민국 법학·판례·법조 칼럼)"
        )
    )
    episode_title_kr: str = Field(
        description="원문 주제를 정확하고 깔끔하게 전달하는 자연스러운 한국어 제목 (날짜 접두사·불필요한 영문 병기 제외)"
    )
    subtitle_kr: str = Field(
        description="원문의 핵심 결론 또는 범위를 1문장으로 정리한 부제"
    )
    tldr_bullets: list[str] = Field(
        min_length=3,
        max_length=4,
        description="원문의 가장 중요한 결론·수치·핵심 사실 3~4개 (자연스러운 한국어, 텔레그램 요약 겸용)",
    )
    overview_and_purpose_kr: str = Field(
        description="원문이 제기하는 문제의식, 작성 목적, 배경을 본문에 충실하게 요약 (3~5문장)"
    )
    methodology_and_evidence_kr: str = Field(
        description="원문이 사용한 연구 방법론, 분석 대상·데이터, 또는 법원의 판단 기준·법리 구조를 충실히 정리 (4~7문장)"
    )
    body_sections: list[SummarySection] = Field(
        min_length=3,
        max_length=6,
        description="원문의 주요 장/주제 흐름을 따라 충실하게 번역·요약한 핵심 본문 섹션 3~6개",
    )
    summary_table: list[SummaryTableRow] = Field(
        min_length=3,
        max_length=8,
        description="원문의 주요 요인·수치·쟁점 비교를 한눈에 정리한 요약표 3~8행 (원문 근거 기반)",
    )
    author_stated_limitations_kr: list[str] = Field(
        min_length=1,
        max_length=4,
        description=(
            "원문(저자·보고서·반대의견 등)이 직접 밝힌 방법론적 한계, 불확실성, 주의점 1~4개. "
            "원문에 없는 AI의 임의 비평은 쓰지 말 것."
        ),
    )
    author_conclusions_kr: list[str] = Field(
        min_length=2,
        max_length=5,
        description=(
            "원문이 직접 제시한 최종 결론, 임상·정책·실무적 제언 및 시사점 2~5개. "
            "본문에 근거하지 않은 AI의 임의 추론이나 억지 적용은 금지."
        ),
    )
    key_quotes: list[KeyQuote] = Field(
        min_length=2,
        max_length=3,
        description="원문에서 그대로 발췌한 핵심 문장 2~3개와 자연스러운 한국어 번역",
    )
    dialogue_cast: DialogueCast = Field(
        description="원문 성격에 맞춘 2인 또는 3인 대화형 오디오 캐스팅 설계"
    )
    chapters: list[ChapterBlueprint] = Field(
        min_length=4,
        max_length=6,
        description="오디오 브리핑 제작 시 원문 내용을 순서대로 충실히 전달하기 위한 4~6개 챕터 설계도",
    )


class DialogueTurn(BaseModel):
    speaker_id: SpeakerId = Field(description="발언 화자 ID ('S1', 'S2', 또는 'S3')")
    text: str = Field(
        description=(
            "마크다운·불릿·URL·태그·불필요한 영문 병기 없이 귀로 자연스럽게 들리는 "
            "순수 한국어 대화 발화 (보통 2~5문장)"
        )
    )


class ChapterDialogueOutput(BaseModel):
    turns: list[DialogueTurn] = Field(
        min_length=2,
        description="해당 챕터(또는 오프닝/클로징)를 구성하는 화자 간 대화 턴 목록",
    )


@dataclass
class DeepDiveScript:
    episode_title: str
    cast: DialogueCast
    opening_turns: list[DialogueTurn]
    chapters: list[tuple[str, list[DialogueTurn]]]  # [(chapter_title_kr, turns), ...]
    closing_turns: list[DialogueTurn]

    def speaker_label_map(self) -> dict[str, str]:
        return {
            sp.speaker_id: f"{sp.name_kr} ({sp.role_title_kr})"
            for sp in self.cast.speakers
        }

    def speaker_name_map(self) -> dict[str, str]:
        return {sp.speaker_id: sp.name_kr for sp in self.cast.speakers}

    def all_turns(self) -> list[DialogueTurn]:
        turns: list[DialogueTurn] = list(self.opening_turns)
        for _, ch_turns in self.chapters:
            turns.extend(ch_turns)
        turns.extend(self.closing_turns)
        return turns

    def to_dialogue_items(self) -> list[tuple[str, str, str, bool]]:
        """TTS 합성용 (section_label, speaker_id, text, is_section_end) 시퀀스를 반환합니다."""
        items: list[tuple[str, str, str, bool]] = []

        def _append_section(sec_label: str, turns: list[DialogueTurn]) -> None:
            valid = [t for t in turns if t.text and t.text.strip()]
            for idx, t in enumerate(valid):
                items.append((sec_label, t.speaker_id, t.text.strip(), idx == len(valid) - 1))

        _append_section("Opening", self.opening_turns)
        for idx, (title, ch_turns) in enumerate(self.chapters, start=1):
            _append_section(f"Chapter {idx}: {title}", ch_turns)
        _append_section("Closing", self.closing_turns)
        return items

    @property
    def total_chars(self) -> int:
        return sum(len(t.text.strip()) for t in self.all_turns())

    @property
    def total_turns(self) -> int:
        return len(self.all_turns())
