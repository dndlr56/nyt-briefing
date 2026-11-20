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


if __name__ == "__main__":
    unittest.main()
