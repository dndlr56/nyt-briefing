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
    CurationDecision,
    CuratedPick,
    DeepDiveScript,
    DeepDiveStudyNote,
    DialogueCast,
    DialogueTurn,
    FetchedDocument,
    KeyQuote,
    SourceCandidate,
    SpeakerProfile,
    SummarySection,
    SummaryTableRow,
)
from deep_dive.notifier import build_deep_dive_telegram_message
from deep_dive.sources import _looks_paywalled, fetch_rss_candidates
from deep_dive.themes import resolve_theme_slot
from deep_dive.web_publisher import load_covered_urls_from_pages, publish_deep_dive


KST = timezone(timedelta(hours=9))


def _sample_cast() -> DialogueCast:
    return DialogueCast(
        format_type="host_and_expert",
        format_reason_kr="실험 근거와 인지 메커니즘을 문답식으로 명쾌하게 정리하기 위해 2인 구성을 선택했습니다.",
        speakers=[
            SpeakerProfile(
                speaker_id="S1",
                name_kr="지수",
                role_title_kr="사회자",
                perspective_kr="핵심 질문을 던지고 논의 흐름을 정리합니다.",
            ),
            SpeakerProfile(
                speaker_id="S2",
                name_kr="현수",
                role_title_kr="인지심리 패널",
                perspective_kr="원문의 실험 결과와 메커니즘을 충실히 해설합니다.",
            ),
        ],
    )


def _sample_note() -> DeepDiveStudyNote:
    return DeepDiveStudyNote(
        primary_theme_id="science_mind",
        episode_title_kr="메타인지와 인출 연습이 장기 기억을 강화하는 메커니즘",
        subtitle_kr="반복 읽기보다 능동적 인출이 지연 기억 유지에 효과적인 이유를 정리한 리뷰",
        tldr_bullets=[
            "인출 연습은 단순 재독 대비 1주 후 기억 유지율을 유의미하게 높인다.",
            "텍스트의 친숙함을 이해로 오인하는 착각이 비효율적 학습 전략 선택으로 이어진다.",
            "간격 복습과 결합할 때 장기 기억 공고화 효과가 가장 크게 나타났다.",
        ],
        overview_and_purpose_kr="이 리뷰는 수동적 반복 읽기와 능동적 인출 연습의 장기 기억 효과를 비교한 실험 연구들을 종합한다.",
        methodology_and_evidence_kr="다기관 무작위 대조 실험과 뇌영상 메타분석 결과를 종합해 지연 회상률과 전전두엽-해마 연결성을 비교했다.",
        body_sections=[
            SummarySection(
                heading_kr="단순 반복 읽기의 한계와 유창성 착각",
                content_kr="학습자는 본문을 여러 번 읽을 때 느껴지는 친숙함을 실제 이해로 오인하는 경향이 있다. 그러나 일주일 뒤 지연 시험에서는 단순 재독 집단의 회상률이 급격히 하락했다.",
            ),
            SummarySection(
                heading_kr="능동적 인출이 기억 흔적을 재구성하는 과정",
                content_kr="기억에서 정보를 스스로 꺼내는 과정 자체가 단서와 목표 정보 사이의 연결 경로를 강화한다. 특히 오답 직후 즉각적인 피드백이 제공될 때 오류 고착이 방지된다.",
            ),
            SummarySection(
                heading_kr="간격 복습과 난이도 조절의 상호작용",
                content_kr="인출 연습을 시간 간격을 두고 분산 배치할수록 단기 성취는 낮아 보여도 장기 유지율은 일관되게 상승했다.",
            ),
        ],
        summary_table=[
            SummaryTableRow(item="핵심 메커니즘", detail_kr="단순 재노출 대비 능동 인출 경로 강화"),
            SummaryTableRow(item="학습 당시 체감", detail_kr="재독은 수월하지만 인출은 인지적 부하가 큼"),
            SummaryTableRow(item="지연 회상 성과", detail_kr="7일 이상 지연 검사에서 인출 연습이 일관된 우위"),
        ],
        author_stated_limitations_kr=[
            "저자들은 분석 대상 실험 상당수가 단어·단문 과제여서 복합적 실전 문제 해결에 대한 장기 추적 연구가 더 필요하다고 밝혔다.",
        ],
        author_conclusions_kr=[
            "교육 현장에서는 단순 복습보다 저부담 형성 평가와 즉각 피드백을 결합하는 설계가 권장된다.",
            "학습자는 즉각적인 유창성보다 지연 인출 성공 여부를 기준으로 이해도를 점검해야 한다.",
        ],
        key_quotes=[
            KeyQuote(
                quote_original="Testing is not merely a neutral assessment of learning; it is a powerful engine of learning itself.",
                translation_kr="시험은 학습 결과를 단순히 측정하는 중립적 도구가 아니라, 학습 자체를 일으키는 강력한 엔진이다.",
                source_label="Sample Cognitive Science Review (2026)",
            ),
            KeyQuote(
                quote_original="Fluency during study often misleads learners into overestimating long-term retention.",
                translation_kr="공부할 때의 매끄러운 유창감은 학습자가 장기 기억 유지력을 과대평가하게 만든다.",
                source_label="Sample Cognitive Science Review (2026)",
            ),
        ],
        dialogue_cast=_sample_cast(),
        chapters=[
            ChapterBlueprint(chapter_number=1, title_kr="왜 열심히 읽어도 기억이 안 날까", focus_kr="유창성 착각과 수동 학습의 한계", key_points=["친숙함과 이해의 차이", "지연 시험 결과"]),
            ChapterBlueprint(chapter_number=2, title_kr="인출 연습의 신경 메커니즘", focus_kr="해마와 전전두엽의 재활성화", key_points=["인출 경로 강화", "피드백 효과"]),
            ChapterBlueprint(chapter_number=3, title_kr="실험 근거와 저자가 밝힌 한계", focus_kr="메타분석 결과와 연구 한계", key_points=["지연 회상 효과", "복합 과제 검증 필요"]),
            ChapterBlueprint(chapter_number=4, title_kr="원문의 결론과 교육적 제언", focus_kr="저자들이 제시한 실천 원칙", key_points=["형성 평가 활용", "간격 복습 결합"]),
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
            cast=note.dialogue_cast,
            opening_turns=[
                DialogueTurn(speaker_id="S1", text="안녕하세요, 오늘의 딥다이브 브리핑을 시작합니다."),
                DialogueTurn(speaker_id="S2", text="반갑습니다. 오늘은 인출 연습 리뷰 논문을 자세히 살펴보겠습니다."),
            ],
            chapters=[
                (
                    "왜 읽어도 기억이 안 날까",
                    [
                        DialogueTurn(speaker_id="S1", text="반복해서 읽으면 공부가 잘 된 느낌인데 왜 시험에서는 막힐까요?"),
                        DialogueTurn(speaker_id="S2", text="원문에서는 이를 유창성 착각으로 설명합니다. 친숙함과 실제 인출 능력은 다릅니다."),
                    ],
                )
            ],
            closing_turns=[
                DialogueTurn(speaker_id="S1", text="오늘 내용 잘 정리됐습니다. 청취해 주셔서 감사합니다."),
                DialogueTurn(speaker_id="S2", text="다음 에피소드에서 다시 뵙겠습니다."),
            ],
        )

        dialogue_items = script.to_dialogue_items()
        self.assertEqual(len(dialogue_items), 6)
        self.assertTrue(dialogue_items[1][3])  # Opening 마지막 턴
        self.assertTrue(dialogue_items[3][3])  # Chapter 1 마지막 턴
        self.assertTrue(dialogue_items[5][3])  # Closing 마지막 턴

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            fake_mp3 = tmp_dir / "sample.mp3"
            fake_mp3.write_bytes(b"ID3" + b"\x00" * 128)
            deep_dir = tmp_dir / "deep"

            # 먼저 과거 weekend_special로 발행된 동일 원문이 있었다고 가정
            weekend_slot = resolve_theme_slot("weekend_special")
            old_pub = publish_deep_dive(
                deep_dir=deep_dir,
                base_url="https://dndlr56.github.io/nyt-briefing/deep",
                date_slug="2026-11-24",
                theme=weekend_slot,
                decision=decision,
                note=note,
                docs=[doc],
                script=None,
                mp3_path=None,
                duration_seconds=0,
            )
            self.assertTrue(old_pub.article_path.exists())

            # 같은 원문을 핵심 테마(science_mind) + 오디오로 재발행하면 기존 weekend_special 항목이 교체되어야 함
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
            self.assertFalse(old_pub.article_path.exists())
            self.assertTrue((deep_dir / "index.html").exists())

            article_html = pub.article_path.read_text(encoding="utf-8")
            self.assertIn("Retrieval Practice", article_html)
            self.assertIn("지수 (사회자)", article_html)
            self.assertIn("현수 (인지심리 패널)", article_html)
            self.assertIn("2인 대화", article_html)

            index_html = (deep_dir / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("data-theme='weekend_special'", index_html)
            self.assertIn("data-theme='science_mind'", index_html)
            self.assertIn("data-theme='health_medicine'", index_html)
            self.assertIn("<enclosure ", pub.feed_path.read_text(encoding="utf-8"))

            import json
            metadata = json.loads((deep_dir / "episodes" / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(len(metadata), 1)
            self.assertEqual(metadata[0]["theme_id"], "science_mind")

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
            self.assertIn("2인 대화", tg_msg)
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

    def test_korean_law_theme_and_hangul_threshold(self) -> None:
        from deep_dive.sources import _meets_min_length

        law_slot = resolve_theme_slot("law_cases")
        self.assertIn("대한민국", law_slot.label_kr)
        self.assertTrue(all(oq.language == "ko" for oq in law_slot.openalex_queries))
        self.assertGreaterEqual(len(law_slot.html_lists), 3)

        korean_article = "대한민국 대법원 전원합의체 판결의 법리적 의의와 헌법적 쟁점을 상세히 검토한다. " * 30
        self.assertTrue(_meets_min_length(korean_article, min_chars=2400))
        self.assertFalse(_meets_min_length("짧은 한국어 단신입니다.", min_chars=2400))

    def test_dialogue_wav_concat_distinct_paths(self) -> None:
        from audio_generator import _concat_dialogue_parts_to_mp3, _write_silence_wav

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            sample_wav = tmp_dir / "sample.wav"
            _write_silence_wav(sample_wav, 0.1)
            wav_bytes = sample_wav.read_bytes()
            out_mp3 = tmp_dir / "out.mp3"

            invoked_cmds: list[list[str]] = []

            class _OkProc:
                returncode = 0
                stderr = ""

            def _fake_run(cmd: list[str], **kwargs: object) -> _OkProc:
                invoked_cmds.append(list(cmd))
                out_path = Path(cmd[-1])
                out_path.write_bytes(b"FAKE_OUT")
                return _OkProc()

            with patch("audio_generator.subprocess.run", side_effect=_fake_run):
                _concat_dialogue_parts_to_mp3(
                    turn_audios=[(wav_bytes, False), (wav_bytes, True)],
                    output_mp3=out_mp3,
                    input_suffix=".wav",
                )

            # 앞선 2회 정규화 호출에서 입력 경로(-i)와 출력 경로(마지막 인자)가 달라야 함
            self.assertEqual(len(invoked_cmds), 3)
            for norm_cmd in invoked_cmds[:2]:
                in_path = norm_cmd[norm_cmd.index("-i") + 1]
                out_path = norm_cmd[-1]
                self.assertNotEqual(in_path, out_path)

    def test_gemini_client_pool_free_to_paid_failover(self) -> None:
        from types import SimpleNamespace
        from deep_dive.gemini_pool import GeminiClientPool
        from usage_tracker import RunUsageTracker

        pool = GeminiClientPool(
            free_api_key="free-key",
            paid_api_key="paid-key",
            mode="auto",
        )
        tracker = RunUsageTracker(api_tier="free")

        class _FakeInteractions:
            def __init__(self, fail_quota: bool) -> None:
                self.fail_quota = fail_quota
                self.calls = 0

            def create(self, **kwargs: object) -> object:
                self.calls += 1
                if self.fail_quota:
                    raise RuntimeError("429 RESOURCE_EXHAUSTED: GenerateRequestsPerDay free_tier limit reached")
                return SimpleNamespace(
                    output_text="ok",
                    usage={
                        "total_input_tokens": 1000,
                        "total_cached_tokens": 0,
                        "total_output_tokens": 500,
                        "total_thought_tokens": 0,
                        "total_tokens": 1500,
                    },
                )

        fake_free = SimpleNamespace(interactions=_FakeInteractions(fail_quota=True))
        fake_paid = SimpleNamespace(interactions=_FakeInteractions(fail_quota=False))
        pool._free_client = fake_free  # type: ignore[assignment]
        pool._paid_client = fake_paid  # type: ignore[assignment]

        # 1) 텍스트 호출이 무료 일일 한도 초과 시 즉시 유료 프로젝트로 전환
        _, tier1 = pool.create_interaction(
            model="gemini-4-hydrogen",
            stage="Stage 1",
            usage_tracker=tracker,
            input="hello",
        )
        self.assertEqual(tier1, "paid")
        self.assertEqual(pool.active_tier_for("gemini-4-hydrogen"), "paid")
        # TTS 그룹은 별도 할당량이므로 여전히 무료 티어 우선 유지
        self.assertEqual(pool.active_tier_for("gemini-3.8-flash-lite-tts"), "free")
        self.assertEqual(len(pool.failover_events), 1)
        self.assertGreater(tracker.actual_paid_usd(), 0.0)

        # 2) TTS는 무료 키로 성공 기록 시 mixed tier 리포트 검증
        fake_free.interactions.fail_quota = False
        _, tier2 = pool.create_interaction(
            model="gemini-3.8-flash-lite-tts",
            stage="TTS Part 1",
            usage_tracker=tracker,
            input="hello",
        )
        self.assertEqual(tier2, "free")
        self.assertTrue(tracker.is_mixed_tier)
        self.assertIn("free->paid", tracker.format_console_summary())

    def test_writer_batching_and_gemini_tts_grouping(self) -> None:
        from audio_generator import _group_dialogue_turns_for_gemini
        from deep_dive.writer import _build_section_batches, resolve_adaptive_target_minutes

        batches_20m = _build_section_batches(chapter_count=5, target_minutes=20)
        self.assertEqual(len(batches_20m), 2)
        self.assertEqual(batches_20m[0][0], "opening")
        self.assertEqual(batches_20m[-1][-1], "closing")
        self.assertEqual(sum(len(b) for b in batches_20m), 7)

        batches_30m = _build_section_batches(chapter_count=5, target_minutes=30)
        self.assertEqual(len(batches_30m), 3)
        self.assertEqual(sum(len(b) for b in batches_30m), 7)

        batches_50m = _build_section_batches(chapter_count=6, target_minutes=50)
        self.assertEqual(len(batches_50m), 3)
        self.assertEqual(sum(len(b) for b in batches_50m), 8)

        short_doc = FetchedDocument(
            candidate=SourceCandidate(title="Short", url="https://example.org/1", source_name="S", theme_id="ai_tech"),
            full_text="word " * 2000,
            text_source="html",
        )
        long_doc = FetchedDocument(
            candidate=SourceCandidate(title="Long", url="https://example.org/2", source_name="S", theme_id="health_medicine"),
            full_text="word " * 15000,
            text_source="pdf",
        )
        self.assertEqual(resolve_adaptive_target_minutes([short_doc], 0), 25)
        self.assertEqual(resolve_adaptive_target_minutes([short_doc, short_doc], 0), 35)
        self.assertEqual(resolve_adaptive_target_minutes([long_doc], 0), 45)
        self.assertEqual(resolve_adaptive_target_minutes([short_doc], 50), 50)

        # 9,600자 (40턴 x 240자) -> 5~6개 파트로 묶여 무료 티어 10 RPD 이하 유지
        sample_items = [
            (f"Chapter {(i // 8) + 1}", "S1" if i % 2 == 0 else "S2", "가" * 240, (i + 1) % 8 == 0)
            for i in range(40)
        ]
        grouped = _group_dialogue_turns_for_gemini(sample_items)
        self.assertLessEqual(len(grouped), 7)
        self.assertGreaterEqual(len(grouped), 4)
        self.assertEqual(sum(len(turns) for turns, _ in grouped), 40)

    def test_unified_root_feed_merges_news_and_deep_dive(self) -> None:
        import json
        from podcast_feed import rebuild_unified_root_feed

        with tempfile.TemporaryDirectory() as tmp:
            pages_dir = Path(tmp)
            (pages_dir / "episodes").mkdir(parents=True)
            (pages_dir / "deep" / "episodes").mkdir(parents=True)

            news_meta = [
                {
                    "guid": "global-briefing-2026-11-20-070000",
                    "title": "[모닝 브리핑] 글로벌 뉴스 | 2026.11.20",
                    "pub_date_rfc2822": "Fri, 20 Nov 2026 07:00:00 +0900",
                    "published_iso": "2026-11-20T07:00:00+09:00",
                    "mp3_filename": "global_briefing_2026-11-20_070000.mp3",
                    "file_size_bytes": 123456,
                    "duration_seconds": 600,
                    "summary_plain": "뉴스 요약",
                    "description_html": "<p>뉴스</p>",
                }
            ]
            deep_meta = [
                {
                    "guid": "deep-dive-2026-11-20-health_medicine-073000",
                    "title": "[의학·건강·장수] 랜싯 치매 보고서 | 2026.11.20",
                    "pub_date_rfc2822": "Fri, 20 Nov 2026 07:30:00 +0900",
                    "published_iso": "2026-11-20T07:30:00+09:00",
                    "article_filename": "2026-11-20_health_medicine_lancet.html",
                    "mp3_filename": "deep_dive_2026-11-20_health_medicine_073000.mp3",
                    "file_size_bytes": 654321,
                    "duration_seconds": 1500,
                    "summary_plain": "심층 요약",
                    "description_html": "<p>딥다이브</p>",
                }
            ]
            (pages_dir / "episodes" / "metadata.json").write_text(
                json.dumps(news_meta, ensure_ascii=False), encoding="utf-8"
            )
            (pages_dir / "deep" / "episodes" / "metadata.json").write_text(
                json.dumps(deep_meta, ensure_ascii=False), encoding="utf-8"
            )

            feed_path = rebuild_unified_root_feed(
                pages_dir=pages_dir,
                base_url="https://dndlr56.github.io/nyt-briefing",
            )
            xml_text = feed_path.read_text(encoding="utf-8")
            self.assertIn("https://dndlr56.github.io/nyt-briefing/episodes/global_briefing_2026-11-20_070000.mp3", xml_text)
            self.assertIn("https://dndlr56.github.io/nyt-briefing/deep/episodes/deep_dive_2026-11-20_health_medicine_073000.mp3", xml_text)
            self.assertIn("https://dndlr56.github.io/nyt-briefing/deep/posts/2026-11-20_health_medicine_lancet.html", xml_text)
            # 최신순: 07:30 Deep Dive가 07:00 뉴스보다 앞에 위치
            self.assertLess(
                xml_text.index("deep-dive-2026-11-20-health_medicine-073000"),
                xml_text.index("global-briefing-2026-11-20-070000"),
            )

    def test_openalex_mirror_priority_rss_fallback_and_curator_backups(self) -> None:
        from unittest import mock
        from deep_dive.curator import DEFAULT_CURATOR_MODEL
        from deep_dive.models import BackupPick, CuratedPick, CurationDecision
        from deep_dive.sources import _collect_openalex_oa_urls, fetch_rss_candidates
        from deep_dive.themes import RssFeedSpec

        # 1) Curator 기본 모델이 gemini-4-hydrogen인지 및 backup_picks 스키마 검증
        self.assertEqual(DEFAULT_CURATOR_MODEL, "gemini-4-hydrogen")
        decision = CurationDecision(
            mode="single_deep_dive",
            unifying_angle_kr="1순위 핵심 주제",
            sub_subject_kr="인지심리",
            picks=[CuratedPick(candidate_index=1, why_selected_kr="1순위")],
            backup_picks=[
                BackupPick(
                    candidate_index=2,
                    sub_subject_kr="거시경제·정책",
                    unifying_angle_kr="2순위 핵심 주제",
                    why_selected_kr="오픈 리포지토리 백업",
                )
            ],
        )
        self.assertEqual(len(decision.backup_picks), 1)
        self.assertEqual(decision.backup_picks[0].sub_subject_kr, "거시경제·정책")

        # 2) OpenAlex OA PDF 미러 우선순위: cell.com보다 PMC/OSF 오픈 리포지토리 우선 + PMC ID 발견
        work = {
            "ids": {},
            "best_oa_location": {
                "pdf_url": "https://www.cell.com/article/S1364661322002364/pdf",
                "landing_page_url": "https://doi.org/10.1016/j.tics.2022.09.001",
            },
            "locations": [
                {
                    "pdf_url": "https://www.cell.com/article/S1364661322002364/pdf",
                    "landing_page_url": "https://www.cell.com/article/S1364661322002364",
                },
                {
                    "pdf_url": "https://europepmc.org/articles/PMC9876543?pdf=render",
                    "landing_page_url": "https://europepmc.org/articles/PMC9876543",
                },
                {
                    "pdf_url": "https://osf.io/preprints/psyarxiv/abcde/download",
                    "landing_page_url": "https://osf.io/preprints/psyarxiv/abcde",
                },
            ],
            "primary_location": {
                "pdf_url": "https://www.cell.com/article/S1364661322002364/pdf",
                "landing_page_url": "https://doi.org/10.1016/j.tics.2022.09.001",
            },
        }
        primary_pdf, alt_pdfs, landing, pmc_id = _collect_openalex_oa_urls(work)
        self.assertIn("europepmc.org", primary_pdf)
        self.assertEqual(pmc_id, "PMC9876543")
        self.assertTrue(any("osf.io" in u for u in alt_pdfs))
        self.assertTrue(any("cell.com" in u for u in alt_pdfs))
        self.assertEqual(landing, "https://doi.org/10.1016/j.tics.2022.09.001")

        # 3) Brookings처럼 정의되지 않은 HTML 엔티티(&nbsp;, &mdash;)가 섞인 RSS 피드 폴백 파싱 검증
        malformed_rss = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b"<rss version='2.0'><channel><title>Brookings</title>"
            b"<item>"
            b"<title>Global Policy &mdash; Deep Analysis</title>"
            b"<link>https://www.brookings.edu/articles/global-policy-deep-analysis/</link>"
            b"<pubDate>Fri, 20 Nov 2026 09:00:00 +0000</pubDate>"
            b"<description>Macroeconomic&nbsp;shift and industrial policy.</description>"
            b"</item>"
            b"</channel></rss>"
        )
        fake_resp = mock.MagicMock()
        fake_resp.content = malformed_rss
        fake_resp.raise_for_status.return_value = None

        spec = RssFeedSpec(
            name="Brookings Institution",
            url="https://www.brookings.edu/feed/",
            source_kind="report",
            sub_topic_hint="거시정책",
            lookback_days=3650,
            max_items=5,
        )
        with mock.patch("deep_dive.sources.requests.get", return_value=fake_resp):
            cands = fetch_rss_candidates(spec, theme_id="econ_geopolitics")
        self.assertEqual(len(cands), 1)
        self.assertIn("Global Policy", cands[0].title)
        self.assertEqual(
            cands[0].url, "https://www.brookings.edu/articles/global-policy-deep-analysis/"
        )

    def test_markdown_archiving_and_youtube_safe_rss_links(self) -> None:
        from deep_dive.drive_archiver import render_study_note_markdown
        from deep_dive.models import (
            ChapterBlueprint,
            CuratedPick,
            CurationDecision,
            DeepDiveStudyNote,
            DialogueCast,
            SpeakerProfile,
            FetchedDocument,
            KeyQuote,
            SourceCandidate,
            SummarySection,
            SummaryTableRow,
        )
        from deep_dive.themes import THEME_SLOTS
        from deep_dive.web_publisher import _ascii_post_slug, publish_deep_dive

        en_doc = FetchedDocument(
            candidate=SourceCandidate(
                title="Computational ethics: bridging cognitive science and AI",
                url="https://doi.org/10.1016/j.tics.2022.02.009",
                source_name="Trends in Cognitive Sciences",
                theme_id="ai_tech",
                published="2022-05-01",
            ),
            full_text="Full text body " * 200,
            text_source="pdf",
        )
        ko_doc = FetchedDocument(
            candidate=SourceCandidate(
                title="대법원 2026다12345 전원합의체 판결",
                url="https://example.kr/case/12345",
                source_name="대한민국 대법원",
                theme_id="law_cases",
            ),
            full_text="판결문 전문 " * 200,
            text_source="html",
        )
        # 1) 순수 ASCII 슬러그 검증 (한글 파일명 금지)
        en_slug = _ascii_post_slug("2026-11-21", "ai_tech", [en_doc])
        ko_slug = _ascii_post_slug("2026-11-21", "law_cases", [ko_doc])
        self.assertTrue(en_slug.isascii())
        self.assertTrue(ko_slug.isascii())
        self.assertIn("computational-ethics", en_slug)

        # 2) 공개 RSS/웹 리더에 Google Drive 링크 미노출 + YouTube 친화 순수 ASCII URL 포함
        note = DeepDiveStudyNote(
            episode_title_kr="계산 윤리학: 인지과학과 알고리즘의 언어로 여는 AI 윤리",
            subtitle_kr="도덕적 의사결정의 계산 모델",
            primary_theme_id="ai_tech",
            tldr_bullets=["핵심 1", "핵심 2", "핵심 3"],
            overview_and_purpose_kr="개요 설명",
            methodology_and_evidence_kr="방법론 설명",
            body_sections=[
                SummarySection(heading_kr=f"핵심 장 {i}", content_kr=f"내용 설명 {i}")
                for i in range(1, 4)
            ],
            summary_table=[
                SummaryTableRow(item="지표", detail_kr="수치 95%"),
                SummaryTableRow(item="표본", detail_kr="1,000명"),
                SummaryTableRow(item="결론", detail_kr="유의미한 개선"),
            ],
            key_quotes=[
                KeyQuote(
                    quote_original="Ethics can be formalized.",
                    translation_kr="윤리는 형식화될 수 있다.",
                    source_label="Awad et al.",
                ),
                KeyQuote(
                    quote_original="Moral algorithms need human norms.",
                    translation_kr="도덕 알고리즘은 인간 규범을 필요로 한다.",
                    source_label="Awad et al.",
                ),
            ],
            author_stated_limitations_kr=["한계 1", "한계 2"],
            author_conclusions_kr=["결론 1", "결론 2"],
            dialogue_cast=DialogueCast(
                format_type="host_and_expert",
                format_reason_kr="기본 2인 구성",
                speakers=[
                    SpeakerProfile(
                        speaker_id="S1", name_kr="지수", role_title_kr="사회자", gender="female", perspective_kr="진행"
                    ),
                    SpeakerProfile(
                        speaker_id="S2", name_kr="현수", role_title_kr="패널", gender="male", perspective_kr="해설"
                    ),
                ],
            ),
            chapters=[
                ChapterBlueprint(
                    chapter_number=i, title_kr=f"챕터 {i}", focus_kr=f"개요 {i}", key_points=["포인트 1", "포인트 2"]
                )
                for i in range(1, 5)
            ],
        )
        decision = CurationDecision(
            mode="single_deep_dive",
            unifying_angle_kr="계산 윤리학의 구조",
            sub_subject_kr="AI 윤리·인지과학",
            picks=[CuratedPick(candidate_index=1, why_selected_kr="대표 리뷰")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            deep_dir = Path(tmp) / "deep"
            dummy_mp3 = Path(tmp) / "ep.mp3"
            dummy_mp3.write_bytes(b"ID3" + b"\x00" * 128)
            published = publish_deep_dive(
                deep_dir=deep_dir,
                base_url="https://dndlr56.github.io/nyt-briefing/deep",
                date_slug="2026-11-21",
                theme=THEME_SLOTS["ai_tech"],
                decision=decision,
                note=note,
                docs=[en_doc],
                mp3_path=dummy_mp3,
                duration_seconds=1200,
                drive_doc_url="https://drive.google.com/file/d/secret/view",
            )
            self.assertTrue(published.article_url.isascii())
            feed_xml = published.feed_path.read_text(encoding="utf-8")
            article_html = published.article_path.read_text(encoding="utf-8")
            self.assertNotIn("Google Drive", feed_xml)
            self.assertNotIn("drive.google.com", feed_xml)
            self.assertNotIn("Google Drive", article_html)
            self.assertIn(published.article_url, feed_xml)
            self.assertIn("https://dndlr56.github.io/nyt-briefing/deep/", feed_xml)

        # 3) 마크다운 요약 노트 렌더링 검증 (YAML frontmatter + 표 + 인용)
        md = render_study_note_markdown(
            date_slug="2026-11-21",
            theme=THEME_SLOTS["ai_tech"],
            decision=decision,
            note=note,
            docs=[en_doc],
            web_article_url="https://dndlr56.github.io/nyt-briefing/deep/posts/2026-11-21_ai_tech_computational-ethics.html",
        )
        self.assertTrue(md.startswith("---\n"))
        self.assertIn('theme_id: "ai_tech"', md)
        self.assertIn("| 지표 | 수치 95% |", md)
        self.assertIn("Ethics can be formalized.", md)

        from deep_dive.drive_archiver import _escape_md_tildes
        escaped = _escape_md_tildes("약 10~20% 및 2024~2026년 참고 https://example.com/~user/paper")
        self.assertEqual(escaped, "약 10∼20% 및 2024∼2026년 참고 https://example.com/~user/paper")

    def test_curl_cffi_fallback_and_unpaywall_lookup(self) -> None:
        from unittest import mock
        from deep_dive.sources import (
            _download_pdf,
            _extract_html_article_and_pdf_link,
            _fetch_unpaywall_oa_urls,
        )

        # 1) requests가 403으로 실패할 때 _download_pdf가 curl_cffi 폴백으로 PDF 바이트를 회수하는지 검증
        with mock.patch("deep_dive.sources.requests.get", side_effect=RuntimeError("403 Forbidden")):
            with mock.patch(
                "deep_dive.sources._fetch_via_curl_cffi",
                return_value=(200, {"content-type": "application/pdf"}, b"%PDF-1.4 mock content"),
            ):
                pdf_bytes = _download_pdf("https://ora.ox.ac.uk/mock.pdf")
        self.assertIsNotNone(pdf_bytes)
        assert pdf_bytes is not None
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

        # 2) requests가 403으로 실패할 때 _extract_html_article_and_pdf_link가 curl_cffi 폴백으로 본문을 추출하는지 검증
        long_para = " ".join(["Cognitive science benefits from diverse linguistic samples."] * 15)
        mock_html = f"<html><body><article><h1>Title</h1><p>{long_para}</p></article></body></html>".encode("utf-8")
        with mock.patch("deep_dive.sources.requests.get", side_effect=RuntimeError("403 Forbidden")):
            with mock.patch(
                "deep_dive.sources._fetch_via_curl_cffi",
                return_value=(200, {"content-type": "text/html; charset=utf-8"}, mock_html),
            ):
                text, _ = _extract_html_article_and_pdf_link("https://dl.acm.org/doi/10.1145/3634685")
        self.assertIn("diverse linguistic samples", text)

        # 3) Unpaywall API 응답에서 오픈 리포지토리 PDF 우선순위 정렬 및 PMC ID 추출 검증
        fake_upw = mock.MagicMock()
        fake_upw.status_code = 200
        fake_upw.raise_for_status.return_value = None
        fake_upw.json.return_value = {
            "best_oa_location": {
                "url_for_pdf": "https://www.cell.com/article/S1364/pdf",
                "url": "https://www.cell.com/article/S1364",
            },
            "oa_locations": [
                {
                    "url_for_pdf": "https://www.cell.com/article/S1364/pdf",
                },
                {
                    "url_for_pdf": "https://europepmc.org/articles/PMC1122334?pdf=render",
                    "url_for_landing_page": "https://europepmc.org/articles/PMC1122334",
                },
            ],
        }
        with mock.patch("deep_dive.sources.requests.get", return_value=fake_upw):
            pdfs, pmc = _fetch_unpaywall_oa_urls("https://doi.org/10.1016/j.tics.2022.09.001")
        self.assertEqual(pmc, "PMC1122334")
        self.assertIn("europepmc.org", pdfs[0])
        self.assertIn("cell.com", pdfs[1])

    def test_morning_briefing_dedup_and_503_single_call_failover(self) -> None:
        import json
        from types import SimpleNamespace
        from unittest import mock
        from deep_dive.gemini_pool import GeminiClientPool
        from main import _load_recent_covered_urls

        # 1) 모닝 뉴스 브리핑 최근 에피소드 URL 추출 검증
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "episodes").mkdir(parents=True, exist_ok=True)
            (pdir / "episodes" / "metadata.json").write_text(
                json.dumps(
                    [
                        {
                            "title": "Ep 1",
                            "description_html": '<a href="https://www.theguardian.com/technology/2026/nov/21/anthropic-riemann/">원문</a>',
                        }
                    ]
                ),
                encoding="utf-8",
            )
            covered = _load_recent_covered_urls(pdir, max_episodes=3)
            self.assertIn("https://www.theguardian.com/technology/2026/nov/21/anthropic-riemann", covered)

        # 2) Deep Dive 503 과부하 시 3회 백오프 후 이번 호출만 유료 1회 우회, 다음 호출은 다시 무료 유지
        pool = GeminiClientPool(free_api_key="free", paid_api_key="paid", mode="auto")

        class _Fake503:
            def __init__(self, fail_always: bool) -> None:
                self.fail_always = fail_always
                self.calls = 0

            def create(self, **kwargs: object) -> object:
                self.calls += 1
                if self.fail_always:
                    raise RuntimeError("503 UNAVAILABLE: overloaded")
                return SimpleNamespace(output_text="ok", usage=None)

        pool._free_client = SimpleNamespace(interactions=_Fake503(fail_always=True))  # type: ignore[assignment]
        pool._paid_client = SimpleNamespace(interactions=_Fake503(fail_always=False))  # type: ignore[assignment]

        with mock.patch("deep_dive.gemini_pool.time.sleep"):
            _, tier_used = pool.create_interaction(
                model="gemini-4-hydrogen", stage="문헌 선별", input="hi"
            )
        self.assertEqual(tier_used, "paid")
        self.assertEqual(pool._free_client.interactions.calls, 4)  # 초기 1회 + 백오프 3회
        # 다음 호출은 여전히 free 티어 우선 유지
        self.assertEqual(pool.active_tier_for("gemini-4-hydrogen"), "free")

    def test_multi_outlet_rss_and_conservative_cross_dedup(self) -> None:
        from datetime import datetime, timezone
        from email.utils import format_datetime
        from unittest import mock
        from guardian_fetcher import ArticleCandidate
        from main import _deduplicate_cross_outlet_candidates
        from rss_news_fetcher import fetch_html_article_body, fetch_multi_outlet_candidates

        now = datetime.now(timezone.utc)
        cands = [
            ArticleCandidate(
                article_id="g:1",
                section="Economy",
                title="Federal Reserve cuts interest rates by quarter point amid cooling inflation",
                trail_text="",
                url="https://guardian.example/fed",
                published_at=now,
                wordcount=900,
                source="The Guardian",
            ),
            # 명백한 동시간대 동일 속보 (BBC) -> 제거되어야 함
            ArticleCandidate(
                article_id="bbc:1",
                section="Business",
                title="Federal Reserve cuts interest rates by quarter point as inflation cools",
                trail_text="",
                url="https://bbc.example/fed",
                published_at=now,
                wordcount=600,
                source="BBC",
            ),
            # 다른 각도/별개 기사 (CNBC) -> 보존되어야 함
            ArticleCandidate(
                article_id="cnbc:1",
                section="Finance",
                title="Nvidia shares surge after quarterly data center revenue beats Wall Street estimates",
                trail_text="",
                url="https://cnbc.example/nvda",
                published_at=now,
                wordcount=700,
                source="CNBC",
            ),
        ]
        deduped = _deduplicate_cross_outlet_candidates(cands)
        self.assertEqual([c.article_id for c in deduped], ["g:1", "cnbc:1"])

        # RSS 파싱 및 HTML 본문 추출 검증
        pub_rfc = format_datetime(now)
        rss_xml = (
            f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            f"<item><title>AI chip demand surges</title>"
            f"<link>https://www.cnbc.com/2026/11/22/ai-chip.html</link>"
            f"<description>Chipmakers report record orders.</description>"
            f"<pubDate>{pub_rfc}</pubDate></item>"
            f"</channel></rss>"
        ).encode("utf-8")
        fake_rss = mock.MagicMock()
        fake_rss.content = rss_xml
        fake_rss.raise_for_status.return_value = None

        with mock.patch("rss_news_fetcher.requests.get", return_value=fake_rss):
            fetched = fetch_multi_outlet_candidates(hours=24, max_per_outlet={"BBC": 2, "CNBC": 2, "NPR": 2})
        self.assertTrue(any(c.title == "AI chip demand surges" for c in fetched))

        fake_html = mock.MagicMock()
        fake_html.text = (
            "<html><body><article><p>"
            + ("Semiconductor supply chains are shifting rapidly across Asia and the US. " * 3)
            + "</p></article></body></html>"
        )
        fake_html.encoding = "utf-8"
        fake_html.raise_for_status.return_value = None
        with mock.patch("rss_news_fetcher.requests.get", return_value=fake_html):
            body = fetch_html_article_body("https://www.cnbc.com/2026/11/22/ai-chip.html")
        self.assertIn("Semiconductor supply chains", body)

    def test_explicit_context_cache_and_safe_fallback(self) -> None:
        from types import SimpleNamespace
        from deep_dive.gemini_pool import GeminiClientPool

        pool = GeminiClientPool(free_api_key="free", paid_api_key="paid", mode="auto")

        # 1) 캐시 생성이 실패(무료 티어 제한 등)해도 예외 없이 (None, None) 반환
        class _FailCaches:
            def create(self, **kwargs: object) -> object:
                raise RuntimeError("403 PERMISSION_DENIED: caching disabled on free tier")

            def delete(self, **kwargs: object) -> None:
                pass

        pool._free_client = SimpleNamespace(caches=_FailCaches(), interactions=None)  # type: ignore[assignment]
        c_name, c_tier = pool.create_context_cache(
            model="gemini-4-hydrogen", system_instruction="sys", contents="doc"
        )
        self.assertIsNone(c_name)
        self.assertIsNone(c_tier)

        # 2) 캐시 호출이 400/404 오류를 내면 자동으로 fallback_input + fallback_system_instruction으로 재시도 성공
        seen_calls: list[dict[str, object]] = []

        class _CacheFallbackInteractions:
            def create(self, **kwargs: object) -> object:
                seen_calls.append(dict(kwargs))
                if "cached_content" in kwargs:
                    raise RuntimeError("400 INVALID_ARGUMENT: cached_content expired")
                return SimpleNamespace(output_text="ok", usage=None)

        pool._free_client = SimpleNamespace(
            caches=_FailCaches(), interactions=_CacheFallbackInteractions()
        )  # type: ignore[assignment]

        res, tier = pool.create_interaction(
            model="gemini-4-hydrogen",
            stage="대본 Part 1/3",
            input="short_part_prompt",
            system_instruction="sys_inst",
            cached_content="cachedContents/abc123",
            cached_tier="free",
            fallback_input="full_doc_plus_part_prompt",
            fallback_system_instruction="sys_inst",
        )
        self.assertEqual(tier, "free")
        self.assertEqual(res.output_text, "ok")
        self.assertEqual(len(seen_calls), 2)
        self.assertIn("cached_content", seen_calls[0])
        self.assertNotIn("system_instruction", seen_calls[0])
        self.assertNotIn("cached_content", seen_calls[1])
        self.assertEqual(seen_calls[1]["input"], "full_doc_plus_part_prompt")
        self.assertEqual(seen_calls[1]["system_instruction"], "sys_inst")


if __name__ == "__main__":
    unittest.main()
