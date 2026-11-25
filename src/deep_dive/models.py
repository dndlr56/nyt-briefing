"""Deep Dive Knowledge Briefing 파이프라인의 핵심 데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, field_validator


CoreThemeId = Literal[
    "ai_tech",
    "science_mind",
    "health_medicine",
    "econ_geopolitics",
    "humanities_society",
]
DocumentLensType = Literal[
    "empirical_or_review",
    "law_and_cases",
    "policy_or_essay",
]
SpeakerId = Literal["S1", "S2", "S3"]
DialogueFormatType = Literal[
    "host_and_expert",
    "two_panelists",
    "host_and_two_panelists",
    "three_panelists",
]


SECTION_HEADINGS_BY_LENS: dict[str, dict[str, str]] = {
    "empirical_or_review": {
        "sec1": "1. 핵심 문제의식 및 테제",
        "sec2": "2. 연구 설계 및 검증 구조",
        "sec3": "3. 본문 핵심 분석 및 메커니즘",
        "sec4": "4. 주요 수치·내용 요약표",
        "sec5": "5. 원문 핵심 문장 발췌",
        "sec6": "6. 원문이 밝힌 한계 및 경계 조건",
        "sec7": "7. 결론 및 연구·실천적 함의",
    },
    "law_and_cases": {
        "sec1": "1. 사안의 배경 및 핵심 쟁점",
        "sec2": "2. 관련 법리 및 판단 기준",
        "sec3": "3. 핵심 논거 및 사안 포섭",
        "sec4": "4. 쟁점·판단 구조 요약표",
        "sec5": "5. 원문 핵심 판시·문장 발췌",
        "sec6": "6. 반대의견·한계 및 구별 포인트",
        "sec7": "7. 판시 결론 및 실무·법리적 함의",
    },
    "policy_or_essay": {
        "sec1": "1. 문제 제기 및 핵심 테제",
        "sec2": "2. 분석 프레임워크 및 근거 구조",
        "sec3": "3. 본문 핵심 논증",
        "sec4": "4. 주요 지표·쟁점 요약표",
        "sec5": "5. 원문 핵심 문장 발췌",
        "sec6": "6. 트레이드오프·반론 및 한계",
        "sec7": "7. 결론 및 정책·전략적 제언",
    },
}


def resolve_section_headings(doc_lens: str | None) -> dict[str, str]:
    """문헌 분석 렌즈(doc_lens)에 맞는 섹션 제목 사전을 반환합니다."""
    return SECTION_HEADINGS_BY_LENS.get(
        (doc_lens or "").strip(), SECTION_HEADINGS_BY_LENS["empirical_or_review"]
    )


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
    alternate_pdf_urls: list[str] = field(default_factory=list)
    pmc_id: str = ""
    source_kind: str = "article"  # paper | report | essay | case_law | review | legal_column
    sub_topic_hint: str = ""
    authority_tier: int = 1  # 1 = top-tier flagship, 2 = authoritative specialist
    doi: str = ""
    cited_by_count: int = 0  # 총 피인용 수 (OpenAlex / Europe PMC / Semantic Scholar)
    influential_citations: int = 0  # 실질적 핵심 인용 수 (Semantic Scholar)
    fwci: float | None = None  # 분야·연도 보정 인용 지수 (OpenAlex FWCI, 1.0=평균)
    top_percentile_label: str = ""  # 분야·연도 정규화 상위 백분위 (예: 'Top 1%', 'Top 10%')
    issns: list[str] = field(default_factory=list)  # 정규화 ISSN / eISSN 목록
    venue_type: str = ""  # journal | conference | preprint | repository | report | essay | legal_column | case_law
    wos_editions: list[str] = field(default_factory=list)  # WoS 등재 색인 (예: ['SCIE', 'SSCI'], ['AHCI'], ['ESCI'])
    wos_categories: list[str] = field(default_factory=list)  # Web of Science Categories
    journal_if_approx: float | None = None  # OpenAlex 2년 평균 피인용(2yr_mean_citedness, IF 근사치)

    @property
    def wos_badge(self) -> str:
        return " · ".join(self.wos_editions)

    @property
    def is_wos_core(self) -> bool:
        return any(ed in ("SCIE", "SSCI", "AHCI") for ed in self.wos_editions)

    @property
    def is_wos_indexed(self) -> bool:
        return bool(self.wos_editions)


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


class BackupPick(BaseModel):
    candidate_index: int = Field(
        description="1순위 선별 문헌이 유료 장벽·403 차단 등으로 전문 확보에 실패할 때 대체할 백업 후보의 1-based 인덱스 번호"
    )
    sub_subject_kr: str = Field(
        description="이 백업 문헌 자체의 세부 주제 태그 (예: '인지심리·의사결정', '거시경제·통화정책')"
    )
    unifying_angle_kr: str = Field(
        description="이 백업 문헌이 다루는 핵심 주제 1~2문장"
    )
    why_selected_kr: str = Field(
        description="이 백업 문헌을 대안으로 추천하는 이유 1문장"
    )


class CurationDecision(BaseModel):
    mode: Literal["single_deep_dive", "multi_synthesis"] = Field(
        description=(
            "대형 문헌·판례·보고서 1편을 충실히 번역·요약할 경우 'single_deep_dive', "
            "공통 주제를 다룬 논문 2~3편을 함께 정리할 경우 'multi_synthesis'"
        )
    )
    unifying_angle_kr: str = Field(
        description="선별된 1순위 문헌이 다루는 핵심 주제 1~2문장"
    )
    sub_subject_kr: str = Field(
        description="세부 주제 태그 (예: '치매 예방·공중보건', '학습·기억', '대한민국 헌법·판례')"
    )
    picks: list[CuratedPick] = Field(
        min_length=1,
        max_length=3,
        description="선별된 1순위 문헌 1~3편 (single_deep_dive는 1편, multi_synthesis는 2~3편)",
    )
    backup_picks: list[BackupPick] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "1순위 문헌이 403 차단·유료 장벽으로 실패할 경우 순서대로 대체할 2~3순위 백업 문헌 목록 "
            "(picks와 중복되지 않는 인덱스, 가급적 PMC/오픈액세스/에세이 등 접근성이 높은 고품질 문헌 우선)"
        ),
    )


class SummarySection(BaseModel):
    heading_kr: str = Field(
        description="원문의 핵심 논점이나 작동 메커니즘이 바로 드러나는 구체적 한국어 소제목 (예: '단순 반복 읽기의 한계와 유창성 착각' — '배경 설명' 같은 추상적 제목이나 불필요한 영문 병기 금지)"
    )
    content_kr: str = Field(
        description=(
            "원문 해당 부분의 핵심 주장, 작동 메커니즘(왜·어떻게), 구체적 수치·표본·사례, 트레이드오프를 충실히 정리한 자연스러운 한국어 본문. "
            "총 5~9문장으로 작성하되, 가독성을 위해 논리 흐름이 전환되는 지점에서 줄바꿈(\\n\\n)으로 2개 문단으로 나누어 작성할 것 (통문단 벽치기 및 AI 임의 추론 금지)"
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
        description="화자 ID ('S1'=여성 목소리, 'S2'=남성 목소리 1, 'S3'=남성 목소리 2)"
    )
    name_kr: str = Field(
        description="짧은 한국어 이름 (반드시 성별 일치: S1 여성='지수', S2 남성='현수', S3 남성='인준')"
    )
    role_title_kr: str = Field(
        description="담백한 역할 라벨 (기본: S1='진행', S2='해설' — 과장된 상황극/가상 직함 금지)"
    )
    perspective_kr: str = Field(
        description="이 화자가 원문 정리에서 맡는 간단한 역할 1문장 (반드시 원문 기반, 상황극 페르소나 금지)"
    )


class DialogueCast(BaseModel):
    format_type: DialogueFormatType = Field(
        description=(
            "기본값은 반드시 'host_and_expert'(사회자 1명 S1 + 패널 1명 S2)로 설정하세요. "
            "원문이 상반된 두 견해·다수의견 대 반대의견(판례)·서로 다른 두 이론/논문을 뚜렷하게 대조하는 경우에만 "
            "'two_panelists'(패널2) 또는 'host_and_two_panelists'(사회자1+패널2)를 예외적으로 선택하세요. "
            "'three_panelists'는 극히 예외적인 경우 외에는 선택하지 마세요."
        )
    )
    format_reason_kr: str = Field(
        description="이 화자 구성을 선택한 이유 1문장"
    )
    speakers: list[SpeakerProfile] = Field(
        min_length=2,
        max_length=3,
        description="기본 2명(S1, S2). 상반된 두 관점 대조 등 꼭 필요한 예외 상황에서만 3명(S1, S2, S3)",
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
            "'humanities_society'(인문·철학·역사·제도)"
        )
    )

    @field_validator("primary_theme_id", mode="before")
    @classmethod
    def _normalize_legacy_theme_id(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() == "law_cases":
            return "humanities_society"
        return value
    doc_lens: DocumentLensType = Field(
        default="empirical_or_review",
        description=(
            "문헌 성격에 맞춘 분석 렌즈: "
            "'empirical_or_review'(학술 논문·리뷰·메타분석), "
            "'law_and_cases'(판례·법학 논문·법조 칼럼), "
            "'policy_or_essay'(정책·경제 보고서·심층 에세이)"
        ),
    )
    episode_title_kr: str = Field(
        description="원문 주제를 정확하고 깔끔하게 전달하는 자연스러운 한국어 제목 (날짜 접두사·불필요한 영문 병기 제외)"
    )
    subtitle_kr: str = Field(
        description="원문의 핵심 결론 또는 범위를 1문장으로 정리한 부제"
    )
    tags: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="아카이브 검색·알렉산드리아 도서관 연동용 핵심 키워드 태그 2~5개 (예: ['ai-alignment', 'rlhf', '거버넌스'])",
    )
    tldr_bullets: list[str] = Field(
        min_length=3,
        max_length=4,
        description="원문의 핵심 문제의식, 검증 방법/근거, 구체적 수치·결론, 핵심 함의를 압축한 3~4개 요약 문장 (공허한 수식어 배제, 텔레그램 요약 겸용)",
    )
    overview_and_purpose_kr: str = Field(
        description=(
            "문헌이 겨냥하는 기존 통념/한계(또는 사건 배경·쟁점)와 저자/법원의 핵심 테제를 바로 제시하는 도입부 (4~6문장, 필요 시 \\n\\n으로 2문단 분리, '이 논문은 ~을 다룬다' 식 서두 금지)"
        )
    )
    methodology_and_evidence_kr: str = Field(
        description=(
            "문헌 성격(doc_lens)에 맞춰 작성: "
            "[논문·리뷰] 표본·데이터셋·실험/식별 설계 및 왜 이 근거를 신뢰할 수 있는지 / "
            "[판례·법리] 관련 조문·선례 지형과 법원의 심사 기준·논거 구조 / "
            "[보고서·에세이] 저자의 분석 프레임워크와 핵심 실증·역사적 논거 구조 (4~7문장, 필요 시 \\n\\n으로 2문단 분리)"
        )
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
            "원문(저자·보고서·반대의견 등)이 직접 밝힌 방법론적 한계, 트레이드오프, 예외/경계 조건(어떤 전제가 달라지면 결론이 바뀔 수 있는지), 또는 판례상 구별 포인트 1~4개. "
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
        description="원문 성격에 맞춘 담백한 대화형 오디오 캐스팅 설계 (기본: 사회자 1 + 패널 1)"
    )
    chapters: list[ChapterBlueprint] = Field(
        min_length=4,
        max_length=6,
        description="오디오 브리핑 제작 시 원문 내용을 순서대로 충실히 전달하기 위한 4~6개 챕터 설계도",
    )


class DialogueTurn(BaseModel):
    speaker_id: SpeakerId = Field(description="발언 화자 ID ('S1', 'S2', 또는 예외적 3인일 때 'S3')")
    text: str = Field(
        description=(
            "마크다운·불릿·URL·태그·불필요한 영문 병기·과장된 상황극 없이 귀로 자연스럽게 들리는 "
            "담백한 한국어 대화 발화 (보통 2~5문장)"
        )
    )


class ChapterDialogueOutput(BaseModel):
    turns: list[DialogueTurn] = Field(
        min_length=2,
        description="해당 챕터(또는 오프닝/클로징)를 구성하는 화자 간 대화 턴 목록",
    )


class SectionDialogueOutput(BaseModel):
    section_id: str = Field(
        description="요청된 섹션 식별자 (예: 'opening', 'ch_1', 'ch_2', 'closing')"
    )
    turns: list[DialogueTurn] = Field(
        min_length=2,
        description="해당 섹션의 화자 간 대화 턴 목록",
    )


class PartDialogueOutput(BaseModel):
    sections: list[SectionDialogueOutput] = Field(
        min_length=1,
        description="요청된 순서대로 작성된 각 섹션별 대화 턴 목록",
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
