"""경량 Gemini 모델로 권위 있는 1차 문헌 후보를 선별하고 적응형 모드(1편 심층 vs 2~3편 종합)를 결정합니다."""

from __future__ import annotations

import os
import time

from pydantic import ValidationError

from deep_dive.gemini_pool import GeminiClientPool
from deep_dive.models import CurationDecision, SourceCandidate
from deep_dive.themes import ThemeSlot
from usage_tracker import RunUsageTracker


DEFAULT_CURATOR_MODEL = "gemini-3.5-flash-lite"
DEFAULT_CURATOR_THINKING = "low"  # 후보 35건 메타데이터 스캔·적응형 모드 판단에 적합한 경량 추론


CURATOR_SYSTEM_INSTRUCTION = """당신은 서울대학교 철학·심리학 전공(로스쿨 입학 예정, 주식 초보 투자자) 독자를 위한 'Deep Dive Knowledge Briefing' 수석 학술 편집장입니다.

[독자 프로필 & 핵심 목적]
1. 자기계발·학습 능력(인지심리, 학습·기억, 메타인지, 주의력, 의사결정·편향, 습관·정서 조절)
2. AI·과학 패러다임 변화 이해(프론티어 모델 원리·한계, 안전성/정렬, 에이전트, 기초과학·신소재 돌파구)
3. 건강·장수 의학(수면, 운동생리학·VO2max, 영양·대사·노화, 정신건강·도파민)
4. 경제·투자 감각 및 지정학(거시·통화정책, 투자 철학·행동재무학, 기업 해자, 미중·에너지·인구)
5. 법적 사고력 입문(대한민국 대법원·헌재 주요 판례, AI·플랫폼 규제, 법학·법조 칼럼)

[선별 원칙]
- 단순 단신 뉴스, 보도자료, 지엽적 실험(예: 특정 마우스 유전자 하나만 건드린 초미세 논문)은 제외하세요.
- 패러다임적 통찰, 리뷰/서베이/메타분석, 랜드마크 판례, 거시 보고서, 장문 에세이를 최우선으로 고르세요.
- **적응형 밀도 판단(mode)**:
  - `single_deep_dive`: 한 편 자체가 묵직한 대형 리뷰 논문, 정책 보고서, 랜드마크 판례, 심층 장문 에세이인 경우 1편만 선택.
  - `multi_synthesis`: 개별 길이가 비교적 짧거나 서로 같은 핵심 쟁점을 다른 각도에서 보완하는 논문/글 2~3편을 엮을 때 훨씬 큰 통찰이 나오는 경우 2~3편 선택.
- `candidate_index`는 반드시 입력 목록의 1-based 번호만 사용하세요.
"""


def curate_candidates(
    candidates: list[SourceCandidate],
    theme: ThemeSlot,
    covered_urls: set[str] | None = None,
    model: str | None = None,
    usage_tracker: RunUsageTracker | None = None,
    gemini_pool: GeminiClientPool | None = None,
) -> CurationDecision:
    """수집된 후보 중 이미 다룬 URL을 제외하고 오늘의 문헌 1~3편을 선별합니다."""
    seen = {u.strip().rstrip("/") for u in (covered_urls or set()) if u}
    fresh = [c for c in candidates if c.url.strip().rstrip("/") not in seen]
    if not fresh:
        fresh = candidates
    if not fresh:
        raise RuntimeError(f"테마 '{theme.theme_id}'에서 선별할 후보 문헌이 없습니다.")

    # 컨텍스트 효율을 위해 최대 35건까지만 선별 프롬프트에 투입
    pool = fresh[:35]
    lines: list[str] = []
    for idx, c in enumerate(pool, start=1):
        badges: list[str] = []
        if c.top_percentile_label:
            badges.append(f"분야 {c.top_percentile_label}")
        if c.fwci is not None and c.fwci > 0:
            badges.append(f"FWCI {c.fwci:.1f}")
        if c.influential_citations > 0:
            badges.append(f"실질인용 {c.influential_citations:,}회")
        if c.cited_by_count > 0:
            badges.append(f"총피인용 {c.cited_by_count:,}회")
        if c.pdf_url:
            badges.append("PDF확보")
        if c.pmc_id:
            badges.append("PMC전문OA")
        badge_str = f" [{' | '.join(badges)}]" if badges else ""
        lines.append(
            f"[{idx}] ({c.source_name} | {c.source_kind}{badge_str} | {c.published})\n"
            f"    제목: {c.title}\n"
            f"    힌트: {c.sub_topic_hint}\n"
            f"    요약: {c.abstract[:380]}"
        )

    prompt = (
        f"오늘의 테마: {theme.emoji} {theme.label_kr} ({theme.theme_id})\n"
        f"테마 큐레이션 가이드:\n{theme.focus_prompt}\n\n"
        "최신성 자체보다 **학계·실무에서 검증·주목받은 고퀄리티 원전(높은 FWCI, 분야 상위 1~10%, 높은 실질 인용 수, "
        "대표 리뷰/서베이, 랜드마크 판례·법조 심층 칼럼·에세이)**을 우선적으로 고르세요.\n\n"
        f"아래 {len(pool)}편의 후보 중 가장 지적 가치가 높고 전문(Full-text) 분석 가치가 뛰어난 "
        "1편(single_deep_dive) 또는 2~3편(multi_synthesis)을 골라 JSON으로 응답해 주세요.\n\n"
        + "\n\n".join(lines)
    )

    selected_model = model or os.getenv("DEEP_CURATOR_MODEL", DEFAULT_CURATOR_MODEL)
    raw_thinking = os.getenv("DEEP_CURATOR_THINKING", DEFAULT_CURATOR_THINKING).strip().lower()
    if raw_thinking == "minimal" and (
        selected_model.startswith("gemini-4") or selected_model.startswith("gemini-3.8")
    ):
        raw_thinking = "low"
    thinking_level = raw_thinking
    client_pool = gemini_pool or GeminiClientPool()

    last_exc: Exception | None = None
    for attempt in range(1, 3):
        try:
            interaction, _ = client_pool.create_interaction(
                model=selected_model,
                stage="문헌 선별",
                usage_tracker=usage_tracker,
                system_instruction=CURATOR_SYSTEM_INSTRUCTION,
                input=prompt,
                response_mime_type="application/json",
                response_format=CurationDecision.model_json_schema(),
                generation_config={
                    "thinking_level": thinking_level,
                    "max_output_tokens": 2048,
                },
                store=False,
            )
            raw = interaction.output_text
            if not raw:
                last_exc = RuntimeError("문헌 선별 중 빈 응답을 받았습니다.")
            else:
                decision = CurationDecision.model_validate_json(raw)
                valid_picks = [
                    p for p in decision.picks if 1 <= p.candidate_index <= len(pool)
                ]
                if not valid_picks:
                    last_exc = RuntimeError("선별된 candidate_index가 범위를 벗어났습니다.")
                else:
                    decision.picks = valid_picks
                    return decision
        except (ValidationError, Exception) as exc:
            last_exc = exc

        if attempt < 2:
            print(f"      [WARN] Curation JSON validation failed ({last_exc}). Retrying...")
            time.sleep(3)

    raise RuntimeError(f"문헌 선별 실패: {last_exc}")
