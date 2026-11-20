"""Deep Dive Knowledge Briefing 단위 및 통합 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from deep_dive.drive_archiver import is_drive_configured
from deep_dive.models import (
    ChapterBlueprint,
    ComparisonRow,
    CurationDecision,
    CuratedPick,
    DeepDiveScript,
    DeepDiveStudyNote,
    FetchedDocument,
    KeyQuote,
    KeyTerm,
    SourceCandidate,
)
from deep_dive.notifier import build_deep_dive_telegram_message
from deep_dive.sources import _looks_paywalled, fetch_rss_candidates
from deep_dive.themes import resolve_theme_slot
from deep_dive.web_publisher import load_covered_urls_from_pages, publish_deep_dive


KST = timezone(timedelta(hours=9))


def _sample_note() -> DeepDiveStudyNote:
    return DeepDiveStudyNote(
        episode_title_kr="메타인지와 인출 연습이 장기 기억을 재편하는 신경 메커니즘",
        subtitle_kr="단순 반복 읽기가 실패하는 이유와 고효율 학습의 인지신경과학적 근거",
        tldr_bullets=[
            "인출 연습(Retrieval Practice)은 재독 대비 1주 후 기억 유지율을 유의미하게 높인다.",
            "메타인지적 착각(Illusion of Competence)은 친숙함을 이해로 오인하게 만든다.",
            "간격 효과(Spacing Effect)와 결합할 때 해마-신피질 공고화가 극대화된다.",
        ],
        executive_thesis_kr="저자들은 수동적 재독보다 능동적 인출이 기억 공고화에 결정적임을 보인다.",
        background_context_kr="전통적 학습법은 반복 읽기와 형광펜 표시에 의존해 왔다.",
        methodology_and_argument_kr="다기관 무작위 대조 실험과 fMRI 메타분석을 결합해 인출 시 전전두엽-해마 연결성을 측정했다.",
        core_findings_kr=[
            "능동 인출 집단은 7일 지연 검사에서 재독 집단보다 높은 회상률을 기록했다.",
            "오답 직후의 즉각적 피드백이 오류 고착을 방지하고 기억 흔적을 강화한다.",
            "학습자가 스스로 느끼는 주관적 유창성과 실제 장기 기억 성과는 역상관을 보일 수 있다.",
        ],
        comparison_table=[
            ComparisonRow(dimension="핵심 메커니즘", detail_kr="단순 재노출 vs 능동 인출 경로 강화"),
            ComparisonRow(dimension="주관적 체감", detail_kr="재독은 쉽고 편함 vs 인출은 인지적 부하가 큼"),
            ComparisonRow(dimension="장기 유지율", detail_kr="지연 테스트에서 인출 연습이 압도적 우위"),
        ],
        limitations_and_critique_kr=[
            "실험실 단어/지문 과제 중심이어서 복합적 법학 사례 적용에는 추가 검증이 필요하다.",
            "극심한 시간 압박 상황에서의 인출 실패가 학습 동기를 저하시킬 수 있다.",
        ],
        practical_takeaways_kr=[
            "로스쿨 판례 학습 시 요약본 재독 대신 쟁점·판시 구조를 백지에 인출하는 훈련이 효과적이다.",
            "투자 복기 시 매수 당시의 가설을 기록하고 사후에 능동 회상·검증하면 확증편향을 줄일 수 있다.",
        ],
        key_terms=[
            KeyTerm(term_kr="인출 연습", term_en="Retrieval Practice", explanation_kr="기억에서 정보를 능동적으로 꺼내는 과정 자체가 기억을 강화하는 원리."),
            KeyTerm(term_kr="유능감 착각", term_en="Illusion of Competence", explanation_kr="텍스트가 눈에 익은 상태를 완전히 이해했다고 착각하는 메타인지 오류."),
            KeyTerm(term_kr="바람직한 어려움", term_en="Desirable Difficulty", explanation_kr="학습 당시에는 어렵지만 장기 기억을 강화하는 적절한 인지적 저항."),
            KeyTerm(term_kr="기억 공고화", term_en="Memory Consolidation", explanation_kr="단기 기억 흔적이 장기 기억 네트워크로 안정화되는 신경 과정."),
        ],
        key_quotes=[
            KeyQuote(
                quote_en="Testing is not merely a neutral assessment of learning; it is a powerful engine of learning itself.",
                translation_kr="시험(인출)은 학습 결과를 단순히 측정하는 중립적 도구가 아니라, 학습 자체를 일으키는 강력한 엔진이다.",
                source_label="Sample Cognitive Science Review (2026)",
            ),
            KeyQuote(
                quote_en="Fluency during study often misleads learners into overestimating long-term retention.",
                translation_kr="공부할 때의 매끄러운 유창감은 학습자가 장기 기억 유지력을 과대평가하게 만든다.",
                source_label="Sample Cognitive Science Review (2026)",
            ),
        ],
        chapters=[
            ChapterBlueprint(chapter_number=1, title_kr="왜 열심히 읽어도 기억이 안 날까", focus_kr="유능감 착각과 수동 학습의 함정", key_points=["친숙함과 이해의 차이", "형광펜 공부법의 한계"]),
            ChapterBlueprint(chapter_number=2, title_kr="인출 연습의 뇌과학적 메커니즘", focus_kr="해마와 전전두엽의 재활성화", key_points=["바람직한 어려움", "신경 경로 강화"]),
            ChapterBlueprint(chapter_number=3, title_kr="실험 근거와 반론 검토", focus_kr="메타분석 결과와 적용 한계", key_points=["지연 회상 효과", "복합 추론 과제 한계"]),
            ChapterBlueprint(chapter_number=4, title_kr="공부와 투자에 바로 쓰는 전략", focus_kr="백지 인출과 간격 복습 설계", key_points=["판례 구조 인출", "투자 가설 복기"]),
        ],
    )


class DeepDivePipelineTests(unittest.TestCase):
    def test_weekday_theme_resolution(self) -> None:
        # 2026-11-23 is Monday
        expected = [
            (23, "ai_tech"),
            (24, "science_mind"),
            (25, "health_medicine"),
            (26, "econ_geopolitics"),
            (27, "law_cases"),
            (28, "weekend_special"),
            (29, "weekend_special"),
        ]
        for day, expected_id in expected:
            dt = datetime(2026, 11, day, 8, 0, tzinfo=KST)
            slot = resolve_theme_slot("auto", now_kst=dt)
            self.assertEqual(slot.theme_id, expected_id)

        self.assertEqual(resolve_theme_slot("law_cases").theme_id, "law_cases")
        with self.assertRaises(ValueError):
            resolve_theme_slot("unknown_theme")

    def test_paywall_detection(self) -> None:
        self.assertTrue(_looks_paywalled("Short snippet. Subscribe to continue reading."))
        long_text = "Academic full text paragraph. " * 200
        self.assertFalse(_looks_paywalled(long_text))

    def test_rss_parsing(self) -> None:
        sample_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><title>Test</title>
          <item>
            <title>Deep Learning &amp; Reasoning</title>
            <link>https://example.org/paper/1</link>
            <pubDate>Thu, 19 Nov 2026 08:00:00 GMT</pubDate>
            <description><![CDATA[<p>A comprehensive review of reasoning.</p>]]></description>
          </item>
        </channel></rss>"""

        class _FakeResp:
            content = sample_xml
            def raise_for_status(self) -> None:
                return None

        from deep_dive.themes import RssFeedSpec
        spec = RssFeedSpec(name="Test Journal", url="https://example.org/rss")
        with patch("deep_dive.sources.requests.get", return_value=_FakeResp()):
            items = fetch_rss_candidates(spec, theme_id="ai_tech")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Deep Learning & Reasoning")
        self.assertEqual(items[0].url, "https://example.org/paper/1")

    def test_web_publisher_and_notifier(self) -> None:
        theme = resolve_theme_slot("science_mind")
        note = _sample_note()
        decision = CurationDecision(
            mode="single_deep_dive",
            unifying_angle_kr="능동 인출의 기억 공고화 메커니즘",
            sub_subject_kr="학습·메타인지",
            picks=[CuratedPick(candidate_index=1, why_selected_kr="대표 메타분석 리뷰")],
        )
        doc = FetchedDocument(
            candidate=SourceCandidate(
                title="Retrieval Practice Review",
                url="https://pmc.ncbi.nlm.nih.gov/articles/PMC999999/",
                source_name="Trends in Cognitive Sciences (PMC OA)",
                theme_id="science_mind",
                published="2026-11",
                authors="Roediger H, Karpicke J",
            ),
            full_text="Full text body " * 300,
            text_source="pmc_xml",
        )
        script = DeepDiveScript(
            episode_title="[종합과학·뇌인지] 메타인지와 인출 연습 | 2026.11.24",
            opening="안녕하세요, 오늘의 딥다이브입니다.",
            chapters=[("왜 읽어도 기억이 안 날까", "본문 발화 테스트입니다.")],
            closing="오늘 에피소드를 마칩니다.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            fake_mp3 = tmp_dir / "sample.mp3"
            fake_mp3.write_bytes(b"ID3" + b"\x00" * 128)
            deep_dir = tmp_dir / "deep"

            pub = publish_deep_dive(
                deep_dir=deep_dir,
                base_url="https://dndlr56.github.io/nyt-briefing/deep",
                date_slug="2026-11-24",
                theme=theme,
                decision=decision,
                note=note,
                docs=[doc],
                script=script,
                mp3_path=fake_mp3,
                duration_seconds=1840,
            )

            self.assertTrue(pub.feed_path.exists())
            self.assertTrue(pub.article_path.exists())
            self.assertTrue((deep_dir / "index.html").exists())
            self.assertIn("Retrieval Practice", pub.article_path.read_text(encoding="utf-8"))
            self.assertIn("<enclosure ", pub.feed_path.read_text(encoding="utf-8"))

            covered = load_covered_urls_from_pages(deep_dir)
            self.assertIn("https://pmc.ncbi.nlm.nih.gov/articles/PMC999999", covered)

            tg_msg = build_deep_dive_telegram_message(
                date_slug="2026-11-24",
                theme=theme,
                decision=decision,
                note=note,
                docs=[doc],
                published=pub,
                duration_seconds=1840,
            )
            self.assertIn("Deep Dive 브리핑", tg_msg)
            self.assertIn(pub.article_url, tg_msg)

    def test_drive_configured_default_false(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(is_drive_configured())

    def test_lookback_filtering(self) -> None:
        from deep_dive.sources import _filter_by_lookback

        now = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
        fresh = SourceCandidate(
            title="Fresh Paper",
            url="https://example.org/fresh",
            source_name="arXiv",
            theme_id="ai_tech",
            published="2026-11-15",
        )
        old = SourceCandidate(
            title="Old Paper",
            url="https://example.org/old",
            source_name="arXiv",
            theme_id="ai_tech",
            published="2026-09-01",
        )
        filtered = _filter_by_lookback([fresh, old], lookback_days=14, now_utc=now)
        self.assertEqual([c.title for c in filtered], ["Fresh Paper"])

        # 기간 내 결과가 없으면 안전 폴백으로 빈 리스트를 반환하지 않음
        fallback = _filter_by_lookback([old], lookback_days=14, now_utc=now)
        self.assertEqual([c.title for c in fallback], ["Old Paper"])

    def test_thinking_level_sanitization(self) -> None:
        from deep_dive.writer import sanitize_thinking_level

        # gemini-4-hydrogen은 minimal 미지원 -> low로 자동 보정
        self.assertEqual(sanitize_thinking_level("gemini-4-hydrogen", "minimal"), "low")
        self.assertEqual(sanitize_thinking_level("gemini-4-hydrogen", "high"), "high")
        self.assertEqual(sanitize_thinking_level("gemini-4-hydrogen", "xhigh"), "xhigh")
        # flash-lite는 minimal 허용, xhigh는 high로 보정
        self.assertEqual(sanitize_thinking_level("gemini-3.5-flash-lite", "minimal"), "minimal")
        self.assertEqual(sanitize_thinking_level("gemini-3.5-flash-lite", "xhigh"), "high")

    def test_openalex_candidate_parsing(self) -> None:
        from deep_dive.sources import fetch_openalex_candidates
        from deep_dive.themes import OpenAlexQuerySpec

        payload = {
            "results": [
                {
                    "display_name": "Metacognition and Retrieval Practice",
                    "publication_date": "2024-05-10",
                    "cited_by_count": 312,
                    "fwci": 8.45,
                    "is_retracted": False,
                    "doi": "https://doi.org/10.1016/j.tics.2024.01.001",
                    "citation_normalized_percentile": {
                        "value": 0.996,
                        "is_in_top_1_percent": True,
                        "is_in_top_10_percent": True,
                    },
                    "ids": {"pmcid": "https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/"},
                    "primary_location": {
                        "landing_page_url": "https://example.org/meta",
                        "source": {"display_name": "Trends in Cognitive Sciences"},
                    },
                    "best_oa_location": {
                        "pdf_url": "https://example.org/meta.pdf",
                        "landing_page_url": "https://example.org/meta",
                    },
                    "authorships": [{"author": {"display_name": "Jane Doe"}}],
                    "abstract_inverted_index": {"Retrieval": [0], "strengthens": [1], "memory.": [2]},
                }
            ]
        }

        class _FakeJsonResp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return payload

        spec = OpenAlexQuerySpec(
            name="Test OA",
            search="metacognition",
            lookback_years=3,
            min_citations=50,
            prefer_top_10_percent=False,
        )
        with patch("deep_dive.sources.requests.get", return_value=_FakeJsonResp()):
            items = fetch_openalex_candidates(spec, theme_id="science_mind")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].cited_by_count, 312)
        self.assertEqual(items[0].fwci, 8.45)
        self.assertEqual(items[0].top_percentile_label, "Top 1%")
        self.assertEqual(items[0].doi, "10.1016/j.tics.2024.01.001")
        self.assertEqual(items[0].pmc_id, "PMC1234567")
        self.assertEqual(items[0].abstract, "Retrieval strengthens memory.")

    def test_europe_pmc_and_semantic_scholar_and_core_fallback(self) -> None:
        from deep_dive.sources import (
            _fetch_core_fulltext_fallback,
            fetch_europe_pmc_candidates,
            fetch_semantic_scholar_candidates,
        )
        from deep_dive.themes import EuropePmcQuerySpec, SemanticScholarQuerySpec

        class _FakeResp:
            def __init__(self, data: dict) -> None:
                self._data = data

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return self._data

        epmc_payload = {
            "resultList": {
                "result": [
                    {
                        "title": "Sleep Architecture and Memory Consolidation",
                        "pmcid": "PMC7654321",
                        "doi": "10.1038/s41591-024-0001",
                        "journalTitle": "Nature Medicine",
                        "firstPublicationDate": "2024-08-01",
                        "authorString": "Smith A, Lee B",
                        "abstractText": "<p>Comprehensive meta-analysis on slow-wave sleep.</p>",
                        "citedByCount": 185,
                    }
                ]
            }
        }
        epmc_spec = EuropePmcQuerySpec(name="EPMC", query="TITLE:sleep AND OPEN_ACCESS:y")
        with patch("deep_dive.sources.requests.get", return_value=_FakeResp(epmc_payload)):
            epmc_items = fetch_europe_pmc_candidates(epmc_spec, theme_id="health_medicine")
        self.assertEqual(len(epmc_items), 1)
        self.assertEqual(epmc_items[0].pmc_id, "PMC7654321")
        self.assertEqual(epmc_items[0].cited_by_count, 185)

        s2_payload = {
            "data": [
                {
                    "title": "Reasoning Limits in Frontier Models",
                    "url": "https://www.semanticscholar.org/paper/abc",
                    "publicationDate": "2025-02-15",
                    "venue": "NeurIPS",
                    "authors": [{"name": "Alice Kim"}],
                    "abstract": "Detailed empirical study.",
                    "citationCount": 210,
                    "influentialCitationCount": 47,
                    "openAccessPdf": {"url": "https://arxiv.org/pdf/2502.00001.pdf"},
                    "externalIds": {"DOI": "10.48550/arXiv.2502.00001", "ArXiv": "2502.00001"},
                    "tldr": {"text": "Frontier models struggle on compositional generalization."},
                }
            ]
        }
        s2_spec = SemanticScholarQuerySpec(name="S2", query="reasoning frontier models")
        with patch("deep_dive.sources.requests.get", return_value=_FakeResp(s2_payload)):
            s2_items = fetch_semantic_scholar_candidates(s2_spec, theme_id="ai_tech")
        self.assertEqual(len(s2_items), 1)
        self.assertEqual(s2_items[0].influential_citations, 47)
        self.assertIn("[TL;DR]", s2_items[0].abstract)

        core_payload = {
            "results": [
                {
                    "fullText": "Full institutional repository text body. " * 120,
                    "downloadUrl": "",
                }
            ]
        }
        cand = SourceCandidate(
            title="Behavioral Finance Study",
            url="https://example.org/paywalled",
            source_name="Journal of Finance",
            theme_id="econ_geopolitics",
            doi="10.1111/jofi.12345",
        )
        with patch.dict("os.environ", {"CORE_API_KEY": "test-core-key"}), patch(
            "deep_dive.sources.requests.get", return_value=_FakeResp(core_payload)
        ):
            recovered_text, _ = _fetch_core_fulltext_fallback(cand)
        self.assertGreater(len(recovered_text), 2400)


if __name__ == "__main__":
    unittest.main()
