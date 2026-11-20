"""요일별 테마 로테이션 및 권위 있는 1차 소스 매트릭스 설정 (고인용·고퀄리티 중심 기간 설계)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class RssFeedSpec:
    name: str
    url: str
    source_kind: str = "article"  # paper | report | essay | case_law | review
    sub_topic_hint: str = ""
    authority_tier: int = 1
    max_items: int = 8
    lookback_days: int = 180  # 최신 1~2주 단신보다 최근 6개월 내 묵직한 장문·보고서 확보


@dataclass(frozen=True)
class ArxivQuerySpec:
    name: str
    query: str
    sub_topic_hint: str = ""
    max_results: int = 10
    lookback_days: int = 180  # AI·법정책 서베이/포지션 논문 최근 6개월 탐색
    sort_by: str = "relevance"  # relevance | submittedDate


@dataclass(frozen=True)
class PmcQuerySpec:
    name: str
    query: str
    sub_topic_hint: str = ""
    max_results: int = 8
    lookback_days: int = 1095  # 최근 3년 내 탑티어 저널 리뷰·메타분석 우선
    sort_by: str = "relevance"  # relevance | pub date


@dataclass(frozen=True)
class OpenAlexQuerySpec:
    """OpenAlex 무료 API로 학계 피인용 수가 높은 오픈액세스(OA) 명작 논문·리뷰를 탐색합니다."""

    name: str
    search: str
    sub_topic_hint: str = ""
    lookback_years: int = 3
    min_citations: int = 20
    work_type: str = ""  # 예: "review" 또는 빈 문자열(전체)
    max_results: int = 10


@dataclass(frozen=True)
class ThemeSlot:
    theme_id: str
    label_kr: str
    emoji: str
    weekdays: tuple[int, ...]  # 0=Mon ... 6=Sun
    focus_prompt: str
    openalex_queries: tuple[OpenAlexQuerySpec, ...] = field(default_factory=tuple)
    rss_feeds: tuple[RssFeedSpec, ...] = field(default_factory=tuple)
    arxiv_queries: tuple[ArxivQuerySpec, ...] = field(default_factory=tuple)
    pmc_queries: tuple[PmcQuerySpec, ...] = field(default_factory=tuple)


THEME_SLOTS: dict[str, ThemeSlot] = {
    "ai_tech": ThemeSlot(
        theme_id="ai_tech",
        label_kr="AI·기술·사회",
        emoji="🤖",
        weekdays=(0,),  # 월요일
        focus_prompt=(
            "단순 신제품 출시 소식이 아니라 학계·산업계에서 깊게 주목받은 "
            "① 프론티어 모델의 추론 원리와 구조적 한계, "
            "② AI 안전성·정렬(Alignment)·해석가능성(Mechanistic Interpretability), "
            "③ AI 에이전트·로보틱스 아키텍처, ④ AI가 노동시장·법조계·지식산업을 재편하는 "
            "고인용 실증 연구·서베이·포지션 논문을 최우선으로 선별하세요."
        ),
        openalex_queries=(
            OpenAlexQuerySpec(
                name="OpenAlex High-Impact AI Reasoning & Alignment OA",
                search="large language model reasoning alignment mechanistic interpretability survey",
                sub_topic_hint="프론티어 모델 원리·안전성·정렬 (고인용)",
                lookback_years=2,
                min_citations=40,
                max_results=10,
            ),
            OpenAlexQuerySpec(
                name="OpenAlex High-Impact AI Labor & Agents OA",
                search="artificial intelligence autonomous agents labor market productivity legal profession",
                sub_topic_hint="AI 에이전트·노동시장·전문직 재편 (고인용)",
                lookback_years=2,
                min_citations=25,
                max_results=8,
            ),
        ),
        arxiv_queries=(
            ArxivQuerySpec(
                name="arXiv cs.AI/cs.CL (Surveys & Reasoning)",
                query='(cat:cs.AI OR cat:cs.CL) AND (ti:survey OR ti:reasoning OR ti:agent OR ti:alignment OR ti:interpretability)',
                sub_topic_hint="프론티어 모델·에이전트·정렬",
                max_results=10,
                lookback_days=180,
                sort_by="relevance",
            ),
            ArxivQuerySpec(
                name="arXiv cs.CY (AI, Law & Labor)",
                query='cat:cs.CY AND (ti:AI OR ti:"language model" OR ti:labor OR ti:law OR ti:copyright OR ti:governance)',
                sub_topic_hint="AI와 노동·법·거버넌스",
                max_results=6,
                lookback_days=270,
                sort_by="relevance",
            ),
        ),
        rss_feeds=(
            RssFeedSpec(
                name="Nature Machine Intelligence",
                url="https://www.nature.com/natmachintell.rss",
                source_kind="paper",
                sub_topic_hint="AI 기초연구·응용",
                authority_tier=1,
            ),
            RssFeedSpec(
                name="Google DeepMind Blog",
                url="https://deepmind.google/blog/rss.xml",
                source_kind="report",
                sub_topic_hint="프론티어 AI 리서치",
                authority_tier=1,
            ),
            RssFeedSpec(
                name="MIT Technology Review",
                url="https://www.technologyreview.com/feed/",
                source_kind="essay",
                sub_topic_hint="기술과 사회",
                authority_tier=2,
            ),
        ),
    ),
    "science_mind": ThemeSlot(
        theme_id="science_mind",
        label_kr="종합과학·뇌인지",
        emoji="🧠",
        weekdays=(1,),  # 화요일
        focus_prompt=(
            "① 기초 물리·수학·양자·우주, 생명·유전학·합성생물학, 에너지·신소재 분야의 패러다임 전환급 돌파구, "
            "② 학습·기억·주의력·메타인지, 판단·의사결정·인지편향, 동기·습관·정서 조절, "
            "③ 신경과학·의식·자유의지(철학·법적 책임론과의 접점)를 다루는 학계 고인용 리뷰·명작 해설을 우선 선별하세요."
        ),
        openalex_queries=(
            OpenAlexQuerySpec(
                name="OpenAlex Cognitive Science & Learning Reviews OA",
                search="metacognition retrieval practice working memory cognitive bias decision making neuroscience",
                sub_topic_hint="학습·기억·메타인지·의사결정 (고인용)",
                lookback_years=4,
                min_citations=50,
                work_type="review",
                max_results=10,
            ),
            OpenAlexQuerySpec(
                name="OpenAlex Consciousness, Habits & Fundamental Science OA",
                search="consciousness free will neuroscience habit formation quantum biology nuclear fusion battery",
                sub_topic_hint="의식·습관·기초과학 돌파구 (고인용)",
                lookback_years=4,
                min_citations=50,
                work_type="review",
                max_results=8,
            ),
        ),
        pmc_queries=(
            PmcQuerySpec(
                name="PMC Cognitive & Learning Science OA",
                query=(
                    '("open access"[filter]) AND '
                    '("learning"[Title] OR "memory"[Title] OR "metacognition"[Title] '
                    'OR "decision making"[Title] OR "cognitive bias"[Title] '
                    'OR "consciousness"[Title] OR "habit"[Title] OR "attention"[Title]) AND '
                    '("Review"[Publication Type] OR "Nature"[Journal] OR "Nat Neurosci"[Journal] '
                    'OR "Trends Cogn Sci"[Journal] OR "Psychol Bull"[Journal] OR "PNAS"[Journal] OR "eLife"[Journal])'
                ),
                sub_topic_hint="인지심리·학습·의사결정·뇌과학",
                max_results=8,
                lookback_days=1095,
                sort_by="relevance",
            ),
        ),
        rss_feeds=(
            RssFeedSpec(
                name="Quanta Magazine",
                url="https://api.quantamagazine.org/feed/",
                source_kind="essay",
                sub_topic_hint="물리·수학·생물·컴퓨터과학 심층 해설",
                authority_tier=1,
                max_items=8,
            ),
            RssFeedSpec(
                name="Nature (News & Research)",
                url="https://www.nature.com/nature.rss",
                source_kind="paper",
                sub_topic_hint="종합 과학 최전선",
                authority_tier=1,
                max_items=6,
            ),
            RssFeedSpec(
                name="Science Advances (Open Access)",
                url="https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=sciadv",
                source_kind="paper",
                sub_topic_hint="오픈액세스 종합 과학",
                authority_tier=1,
                max_items=6,
            ),
            RssFeedSpec(
                name="Nature Communications",
                url="https://www.nature.com/ncomms.rss",
                source_kind="paper",
                sub_topic_hint="오픈액세스 종합 과학",
                authority_tier=1,
                max_items=6,
            ),
        ),
    ),
    "health_medicine": ThemeSlot(
        theme_id="health_medicine",
        label_kr="의학·건강·장수",
        emoji="🫀",
        weekdays=(2,),  # 수요일
        focus_prompt=(
            "Andrew Huberman 팟캐스트처럼 탄탄한 대규모 임상·기초 근거에 기반해 실생활에 적용할 수 있는 "
            "① 수면·일주기 리듬·뇌 피로 회복, ② 운동생리학·심폐지구력(VO2max)·근감소 예방, "
            "③ 영양·대사건강·혈당·노화/장수(Longevity) 의학, ④ 정신의학·불안/우울·디지털/도파민 중독 및 "
            "신경가소성 관련 최근 3~4년 내 고인용 오픈액세스 리뷰·메타분석을 최우선 선별하세요."
        ),
        openalex_queries=(
            OpenAlexQuerySpec(
                name="OpenAlex Sleep, Exercise & Longevity Reviews OA",
                search="sleep circadian rhythm cardiorespiratory fitness VO2max longevity aging metabolic health",
                sub_topic_hint="수면·운동·대사·장수의학 (고인용)",
                lookback_years=4,
                min_citations=60,
                work_type="review",
                max_results=10,
            ),
            OpenAlexQuerySpec(
                name="OpenAlex Mental Health & Neuroplasticity Reviews OA",
                search="depression anxiety dopamine stress burnout digital media neuroplasticity meta-analysis",
                sub_topic_hint="정신의학·스트레스·도파민 (고인용)",
                lookback_years=4,
                min_citations=50,
                work_type="review",
                max_results=8,
            ),
        ),
        pmc_queries=(
            PmcQuerySpec(
                name="PMC Sleep, Exercise & Longevity OA",
                query=(
                    '("open access"[filter]) AND '
                    '("sleep"[Title] OR "circadian"[Title] OR "exercise"[Title] OR "VO2max"[Title] '
                    'OR "longevity"[Title] OR "aging"[Title] OR "metabolic"[Title] OR "GLP-1"[Title]) AND '
                    '("Review"[Publication Type] OR "Meta-Analysis"[Publication Type] '
                    'OR "N Engl J Med"[Journal] OR "Lancet"[Journal] OR "JAMA"[Journal] '
                    'OR "Nat Med"[Journal] OR "Cell Metab"[Journal] OR "eLife"[Journal])'
                ),
                sub_topic_hint="수면·운동·대사·장수의학",
                max_results=8,
                lookback_days=1095,
                sort_by="relevance",
            ),
            PmcQuerySpec(
                name="PMC Mental Health & Neuroplasticity OA",
                query=(
                    '("open access"[filter]) AND '
                    '("anxiety"[Title] OR "depression"[Title] OR "dopamine"[Title] '
                    'OR "stress"[Title] OR "burnout"[Title] OR "neuroplasticity"[Title] OR "smartphone"[Title]) AND '
                    '("Review"[Publication Type] OR "Meta-Analysis"[Publication Type] '
                    'OR "Lancet Psychiatry"[Journal] OR "JAMA Psychiatry"[Journal] OR "Nat Med"[Journal])'
                ),
                sub_topic_hint="정신의학·스트레스·도파민",
                max_results=6,
                lookback_days=1095,
                sort_by="relevance",
            ),
        ),
        rss_feeds=(
            RssFeedSpec(
                name="Nature Medicine",
                url="https://www.nature.com/nm.rss",
                source_kind="paper",
                sub_topic_hint="임상·중개의학",
                authority_tier=1,
            ),
            RssFeedSpec(
                name="eLife (Medicine & Neuroscience)",
                url="https://elifesciences.org/rss/recent.xml",
                source_kind="paper",
                sub_topic_hint="오픈액세스 의생명과학",
                authority_tier=1,
            ),
        ),
    ),
    "econ_geopolitics": ThemeSlot(
        theme_id="econ_geopolitics",
        label_kr="경제·금융·지정학",
        emoji="🌐",
        weekdays=(3,),  # 목요일
        focus_prompt=(
            "초보 투자자이자 거시적 식견을 기르려는 지식인의 관점에서 "
            "① 거시경제·금리·통화정책 사이클(NBER·BIS·IMF), ② 투자 철학·시장 사이클·자산배분, "
            "③ 행동재무학·개미 투자자 편향 실증 연구, ④ 산업 구조·기업 해자·AI 생산성 경제학, "
            "⑤ 미중 패권·반도체/AI 공급망·에너지·인구구조·국제질서 재편의 권위 있는 고인용 연구·보고서를 선별하세요."
        ),
        openalex_queries=(
            OpenAlexQuerySpec(
                name="OpenAlex Behavioral Finance & Macro Cycles OA",
                search="behavioral finance retail investor bias monetary policy inflation asset pricing cycle",
                sub_topic_hint="행동재무학·거시경제·투자 원리 (고인용)",
                lookback_years=4,
                min_citations=40,
                max_results=10,
            ),
            OpenAlexQuerySpec(
                name="OpenAlex Geoeconomics, Supply Chains & Demographics OA",
                search="geoeconomics semiconductor supply chain energy transition demographic aging productivity",
                sub_topic_hint="지정학·공급망·에너지·인구구조 (고인용)",
                lookback_years=3,
                min_citations=30,
                max_results=8,
            ),
        ),
        rss_feeds=(
            RssFeedSpec(
                name="NBER Working Papers",
                url="https://back.nber.org/rss/new.xml",
                source_kind="paper",
                sub_topic_hint="거시·금융·노동·산업 경제학",
                authority_tier=1,
                max_items=8,
                lookback_days=180,
            ),
            RssFeedSpec(
                name="BIS Research & Speeches",
                url="https://www.bis.org/doclist/all_rss.rss",
                source_kind="report",
                sub_topic_hint="글로벌 중앙은행·통화·금융안정",
                authority_tier=1,
                max_items=6,
                lookback_days=180,
            ),
            RssFeedSpec(
                name="IMF Blog",
                url="https://www.imf.org/en/Blogs/rss",
                source_kind="report",
                sub_topic_hint="거시경제·글로벌 정책",
                authority_tier=1,
                max_items=6,
                lookback_days=180,
            ),
            RssFeedSpec(
                name="Brookings Institution",
                url="https://www.brookings.edu/feed/",
                source_kind="report",
                sub_topic_hint="공공정책·경제·인구·지정학",
                authority_tier=1,
                max_items=6,
                lookback_days=180,
            ),
            RssFeedSpec(
                name="RAND Corporation",
                url="https://www.rand.org/pubs/new.xml",
                source_kind="report",
                sub_topic_hint="안보·기술·에너지·국제질서",
                authority_tier=1,
                max_items=6,
                lookback_days=180,
            ),
            RssFeedSpec(
                name="CSIS Analysis",
                url="https://www.csis.org/analysis/feed",
                source_kind="report",
                sub_topic_hint="미중 패권·공급망·경제안보",
                authority_tier=1,
                max_items=6,
                lookback_days=180,
            ),
        ),
        arxiv_queries=(
            ArxivQuerySpec(
                name="arXiv q-fin / econ (Behavioral Finance & Macro)",
                query='(cat:q-fin.GN OR cat:q-fin.EC OR cat:econ.GN) AND (ti:behavioral OR ti:investor OR ti:monetary OR ti:inflation OR ti:AI OR ti:productivity)',
                sub_topic_hint="행동재무학·AI 경제학",
                max_results=6,
                lookback_days=365,
                sort_by="relevance",
            ),
        ),
    ),
    "law_cases": ThemeSlot(
        theme_id="law_cases",
        label_kr="법학·판례 입문",
        emoji="⚖️",
        weekdays=(4,),  # 금요일
        focus_prompt=(
            "로스쿨 입학 예정자를 위한 '법적 추론(Legal Reasoning) 입문형' 트랙입니다. "
            "지엽적인 최신 절차 뉴스보다는 ① 사회·헌법적 파장이 큰 랜드마크 판례(미국 연방대법원 SCOTUS 등) 해설, "
            "② 학계에서 폭넓게 인용되는 AI·빅테크·반독점·저작권/IP 규제 법학 논문, "
            "③ 헌법·기본권·사법심사·법과 경제학(Law & Economics)의 원리가 선명하게 드러나는 명작을 최우선 선별하세요."
        ),
        openalex_queries=(
            OpenAlexQuerySpec(
                name="OpenAlex AI Law, Copyright & Antitrust OA",
                search="artificial intelligence copyright antitrust regulation legal liability generative AI law",
                sub_topic_hint="AI 규제·저작권·반독점 법학 (고인용)",
                lookback_years=3,
                min_citations=25,
                max_results=10,
            ),
            OpenAlexQuerySpec(
                name="OpenAlex Constitutional Law & Law and Economics OA",
                search="constitutional interpretation judicial review law and economics legal reasoning supreme court",
                sub_topic_hint="헌법·사법심사·법과 경제학 (고인용)",
                lookback_years=5,
                min_citations=30,
                max_results=8,
            ),
        ),
        rss_feeds=(
            RssFeedSpec(
                name="SCOTUSblog",
                url="https://www.scotusblog.com/feed/",
                source_kind="case_law",
                sub_topic_hint="미국 연방대법원 주요 판례 해설",
                authority_tier=1,
                max_items=8,
                lookback_days=180,
            ),
            RssFeedSpec(
                name="Harvard Law Review",
                url="https://harvardlawreview.org/feed/",
                source_kind="paper",
                sub_topic_hint="헌법·공법·기업법·법이론",
                authority_tier=1,
                max_items=8,
                lookback_days=365,
            ),
            RssFeedSpec(
                name="Lawfare (Tech, Constitutional & National Security Law)",
                url="https://www.lawfaremedia.org/rss.xml",
                source_kind="essay",
                sub_topic_hint="헌법·AI 규제·플랫폼·국가안보법",
                authority_tier=1,
                max_items=8,
                lookback_days=180,
            ),
            RssFeedSpec(
                name="Verfassungsblog (Constitutional Law)",
                url="https://verfassungsblog.de/feed/",
                source_kind="essay",
                sub_topic_hint="비교헌법·기본권·사법심사",
                authority_tier=2,
                max_items=6,
                lookback_days=180,
            ),
        ),
        arxiv_queries=(
            ArxivQuerySpec(
                name="arXiv cs.CY (AI Law, Copyright & Regulation)",
                query='cat:cs.CY AND (ti:law OR ti:legal OR ti:regulation OR ti:copyright OR ti:antitrust OR ti:liability OR ti:constitutional)',
                sub_topic_hint="AI 규제·저작권·법적 책임",
                max_results=6,
                lookback_days=365,
                sort_by="relevance",
            ),
        ),
    ),
    "weekend_special": ThemeSlot(
        theme_id="weekend_special",
        label_kr="주말 무제한 스페셜",
        emoji="📚",
        weekdays=(5, 6),  # 토·일요일
        focus_prompt=(
            "시효에 얽매이지 않고 분야를 넘나들며 가장 묵직하고 지적인 울림을 주는 "
            "고인용 교차 분야 리뷰(과학×철학, AI×경제, 법×심리), 명작 장문 에세이, 또는 랜드마크 보고서 1편을 선별하세요."
        ),
        openalex_queries=(
            OpenAlexQuerySpec(
                name="OpenAlex Interdisciplinary Landmark Reviews OA",
                search="rationality epistemology AI society institutional economics cognitive neuroscience review",
                sub_topic_hint="교차 분야 랜드마크 리뷰 (고인용)",
                lookback_years=5,
                min_citations=80,
                work_type="review",
                max_results=10,
            ),
        ),
        rss_feeds=(
            RssFeedSpec(
                name="Aeon Essays",
                url="https://aeon.co/feed.rss",
                source_kind="essay",
                sub_topic_hint="철학·과학·인문 장문 에세이",
                authority_tier=1,
                max_items=8,
                lookback_days=365,
            ),
            RssFeedSpec(
                name="Quanta Magazine",
                url="https://api.quantamagazine.org/feed/",
                source_kind="essay",
                sub_topic_hint="기초과학·수학 심층 장문",
                authority_tier=1,
                max_items=6,
                lookback_days=180,
            ),
            RssFeedSpec(
                name="Foreign Affairs",
                url="https://www.foreignaffairs.com/rss.xml",
                source_kind="essay",
                sub_topic_hint="글로벌 질서·지정학 장문",
                authority_tier=1,
                max_items=6,
                lookback_days=365,
            ),
            RssFeedSpec(
                name="Brookings Institution",
                url="https://www.brookings.edu/feed/",
                source_kind="report",
                sub_topic_hint="정책·경제 대형 보고서",
                authority_tier=1,
                max_items=5,
                lookback_days=180,
            ),
            RssFeedSpec(
                name="SCOTUSblog",
                url="https://www.scotusblog.com/feed/",
                source_kind="case_law",
                sub_topic_hint="랜드마크 판례",
                authority_tier=1,
                max_items=5,
                lookback_days=180,
            ),
        ),
        arxiv_queries=(
            ArxivQuerySpec(
                name="arXiv Landmark Surveys",
                query='(cat:cs.AI OR cat:cs.CY OR cat:q-bio.NC) AND (ti:survey OR ti:perspective OR ti:review)',
                sub_topic_hint="교차 분야 대형 서베이",
                max_results=6,
                lookback_days=365,
                sort_by="relevance",
            ),
        ),
    ),
}


def resolve_theme_slot(theme_arg: str = "auto", now_kst: datetime | None = None) -> ThemeSlot:
    """CLI 인자 또는 오늘(KST) 요일에 맞는 ThemeSlot을 반환합니다."""
    cleaned = (theme_arg or "auto").strip().lower()
    if cleaned != "auto":
        if cleaned not in THEME_SLOTS:
            valid = ", ".join(THEME_SLOTS.keys())
            raise ValueError(f"알 수 없는 테마입니다: {theme_arg!r} (가능한 값: auto, {valid})")
        return THEME_SLOTS[cleaned]

    current = now_kst or datetime.now(KST)
    weekday = current.weekday()  # 0=Mon .. 6=Sun
    for slot in THEME_SLOTS.values():
        if weekday in slot.weekdays:
            return slot
    return THEME_SLOTS["weekend_special"]
