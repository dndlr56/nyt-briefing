"""Gemini API 토큰 사용량 및 예상 비용, TTS 사용량을 집계해 포맷합니다."""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import os
from typing import Any


# 모델별 100만 토큰당 유료 단가 (USD): (입력, 캐시 입력, 출력/추론)
MODEL_PRICING_PER_1M: dict[str, tuple[float, float, float]] = {
    "gemini-4-hydrogen": (1.50, 0.075, 7.50),
    "gemini-4-argon": (2.00, 0.10, 10.00),
    "gemini-4-carbon": (2.00, 0.10, 10.00),
    "gemini-3.8-flash": (0.75, 0.075, 3.75),
    "gemini-3.6-flash": (0.75, 0.075, 3.75),
    "gemini-3.5-flash-lite": (0.30, 0.03, 2.50),
}
DEFAULT_PRICING_PER_1M: tuple[float, float, float] = (1.50, 0.075, 7.50)
KRW_PER_USD = 1400


def _read_int(obj: Any, *keys: str) -> int:
    """객체 속성 또는 dict에서 첫 번째로 존재하는 정수 값을 안전하게 읽습니다."""
    if obj is None:
        return 0
    for key in keys:
        val = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
        if val is not None:
            try:
                return max(0, int(val))
            except (TypeError, ValueError):
                continue
    return 0


@dataclass
class GeminiCallUsage:
    stage: str
    model: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    total_tokens: int = 0

    @property
    def non_cached_input_tokens(self) -> int:
        return max(0, self.input_tokens - self.cached_input_tokens)

    @property
    def billable_output_tokens(self) -> int:
        """출력 토큰과 추론(thinking) 토큰을 중복 합산하지 않고 과금 기준으로 계산합니다."""
        if self.thought_tokens > 0 and (
            self.total_tokens == 0
            or (self.input_tokens + self.output_tokens) < self.total_tokens
        ):
            return self.output_tokens + self.thought_tokens
        return self.output_tokens

    @property
    def combined_total_tokens(self) -> int:
        if self.total_tokens > 0:
            return self.total_tokens
        return self.input_tokens + self.billable_output_tokens

    def estimate_usd(self) -> float:
        in_rate, cache_rate, out_rate = MODEL_PRICING_PER_1M.get(
            self.model, DEFAULT_PRICING_PER_1M
        )
        return (
            (self.non_cached_input_tokens / 1_000_000) * in_rate
            + (self.cached_input_tokens / 1_000_000) * cache_rate
            + (self.billable_output_tokens / 1_000_000) * out_rate
        )


def extract_interaction_usage(
    interaction: Any,
    stage: str,
    model: str,
) -> GeminiCallUsage:
    """Gemini Interactions API 응답에서 usage 토큰 통계를 안전하게 추출합니다."""
    usage = getattr(interaction, "usage", None)
    return GeminiCallUsage(
        stage=stage,
        model=model,
        input_tokens=_read_int(usage, "total_input_tokens", "input_tokens", "prompt_token_count"),
        cached_input_tokens=_read_int(
            usage, "total_cached_tokens", "cached_input_tokens", "cached_content_token_count"
        ),
        output_tokens=_read_int(
            usage, "total_output_tokens", "output_tokens", "candidates_token_count"
        ),
        thought_tokens=_read_int(
            usage, "total_thought_tokens", "thought_tokens", "thoughts_token_count"
        ),
        total_tokens=_read_int(usage, "total_tokens", "total_token_count"),
    )


def is_rate_limit_error(exc: Exception) -> bool:
    """Gemini API 429 / 할당량(quota) 초과 오류인지 판별합니다."""
    msg = str(exc).lower()
    return (
        "429" in msg
        or "resource_exhausted" in msg
        or "quota" in msg
        or "rate limit" in msg
        or "too many requests" in msg
    )


def try_switch_to_paid_key() -> str | None:
    """무료 티어 한도 소진 시 유료 API 키(GEMINI_PAID_API_KEY)로 전환하고 새 키를 반환합니다."""
    current_tier = (os.getenv("GEMINI_API_TIER") or "paid").strip().lower()
    paid_key = (os.getenv("GEMINI_PAID_API_KEY") or "").strip()
    current_key = (os.getenv("GEMINI_API_KEY") or "").strip()

    if current_tier != "free" or not paid_key or paid_key == current_key:
        return None

    os.environ["GEMINI_API_KEY"] = paid_key
    os.environ["GEMINI_API_TIER"] = "paid"
    os.environ["GEMINI_FALLBACK_USED"] = "1"
    print(
        "      [WARN] Gemini Free Tier quota exhausted. "
        "Switching to Paid API key (GEMINI_PAID_API_KEY)..."
    )
    return paid_key


@dataclass
class RunUsageTracker:
    calls: list[GeminiCallUsage] = field(default_factory=list)
    tts_provider: str | None = None
    tts_voice: str | None = None
    tts_model: str | None = None
    tts_chars: int = 0
    api_tier: str = field(
        default_factory=lambda: (os.getenv("GEMINI_API_TIER") or "paid").strip().lower()
    )
    fallback_to_paid: bool = field(
        default_factory=lambda: os.getenv("GEMINI_FALLBACK_USED") == "1"
    )

    def refresh_tier_from_env(self) -> None:
        self.api_tier = (os.getenv("GEMINI_API_TIER") or self.api_tier).strip().lower()
        if os.getenv("GEMINI_FALLBACK_USED") == "1":
            self.fallback_to_paid = True

    def add_interaction(self, interaction: Any, stage: str, model: str) -> GeminiCallUsage:
        self.refresh_tier_from_env()
        record = extract_interaction_usage(interaction, stage=stage, model=model)
        self.calls.append(record)
        return record

    def set_tts(
        self,
        provider: str,
        voice: str,
        chars: int,
        model: str | None = None,
    ) -> None:
        self.refresh_tier_from_env()
        self.tts_provider = provider
        self.tts_voice = voice
        self.tts_model = model
        self.tts_chars = max(0, int(chars))

    @property
    def is_free_tier(self) -> bool:
        self.refresh_tier_from_env()
        return self.api_tier == "free"

    @property
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def total_cached_tokens(self) -> int:
        return sum(c.cached_input_tokens for c in self.calls)

    @property
    def total_billable_output_tokens(self) -> int:
        return sum(c.billable_output_tokens for c in self.calls)

    @property
    def total_thought_tokens(self) -> int:
        return sum(c.thought_tokens for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return sum(c.combined_total_tokens for c in self.calls)

    @property
    def models_used(self) -> list[str]:
        seen: list[str] = []
        for c in self.calls:
            if c.model not in seen:
                seen.append(c.model)
        return seen

    def estimate_usd(self) -> float:
        return sum(c.estimate_usd() for c in self.calls)

    def _tts_cost_label(self) -> str:
        if self.tts_provider == "edge_tts":
            return "무료"
        if self.tts_provider == "google_cloud_tts":
            return "월 100만자 무료 티어 내 $0"
        if self.tts_provider == "gemini_tts" and self.is_free_tier:
            return "무료 티어 $0"
        if self.tts_provider == "gemini_tts" and self.fallback_to_paid:
            return "무료 한도 초과 → 유료 티어 자동 전환"
        return "유료 과금"

    def format_console_summary(self) -> str:
        self.refresh_tier_from_env()
        usd = self.estimate_usd()
        krw = round(usd * KRW_PER_USD)
        models = ", ".join(self.models_used) or "unknown"
        if self.is_free_tier:
            tier_label = "free"
            cost_str = f"FREE TIER $0 (paid equiv. ${usd:.4f} (~{krw:,} KRW))"
        elif self.fallback_to_paid:
            tier_label = "free->paid"
            cost_str = f"PAID FALLBACK est. ${usd:.4f} (~{krw:,} KRW)"
        else:
            tier_label = self.api_tier
            cost_str = f"est. ${usd:.4f} (~{krw:,} KRW)"

        lines = [
            f"      [Usage] Gemini ({models}, tier={tier_label}): "
            f"input={self.total_input_tokens:,} (cached={self.total_cached_tokens:,}), "
            f"output={self.total_billable_output_tokens:,} (thought={self.total_thought_tokens:,}), "
            f"total={self.total_tokens:,} tokens | {cost_str}"
        ]
        for c in self.calls:
            lines.append(
                f"              - {c.stage}: in={c.input_tokens:,}, "
                f"out={c.billable_output_tokens:,} (thought={c.thought_tokens:,}), "
                f"est. ${c.estimate_usd():.4f}"
            )
        if self.tts_provider:
            model_info = f" / {self.tts_model}" if self.tts_model else ""
            lines.append(
                f"      [Usage] TTS: {self.tts_provider}{model_info} ({self.tts_voice}), "
                f"{self.tts_chars:,} chars ({self._tts_cost_label()})"
            )
        return "\n".join(lines)

    def format_show_notes_html(self) -> str:
        """AntennaPod 에피소드 설명란 하단에 붙일 토큰·비용 요약 HTML을 생성합니다."""
        self.refresh_tier_from_env()
        if not self.calls and not self.tts_provider:
            return ""

        usd = self.estimate_usd()
        krw = round(usd * KRW_PER_USD)
        models_str = html.escape(", ".join(self.models_used) or "Gemini")

        items: list[str] = []
        if self.calls:
            in_extra = (
                f" (캐시 {self.total_cached_tokens:,} 포함)"
                if self.total_cached_tokens > 0
                else ""
            )
            out_extra = (
                f" (추론 {self.total_thought_tokens:,} 포함)"
                if self.total_thought_tokens > 0
                else ""
            )
            items.append(
                f"• Gemini API (<code>{models_str}</code>): "
                f"입력 {self.total_input_tokens:,}{in_extra} + "
                f"출력 {self.total_billable_output_tokens:,}{out_extra} = "
                f"총 {self.total_tokens:,} 토큰"
            )
            if self.is_free_tier:
                items.append(
                    f"• 예상 API 비용: 무료 티어 $0 (유료 환산 약 ${usd:.4f} · 약 {krw:,}원)"
                )
            elif self.fallback_to_paid:
                items.append(
                    f"• 예상 API 비용: 무료 한도 초과로 유료 전환 · 약 ${usd:.4f} (약 {krw:,}원 기준)"
                )
            else:
                items.append(
                    f"• 예상 API 비용: 약 ${usd:.4f} (약 {krw:,}원 · 유료 단가/환율 {KRW_PER_USD:,}원 기준)"
                )

        if self.tts_provider:
            prov = html.escape(self.tts_provider)
            voice = html.escape(self.tts_voice or "")
            model_part = (
                f" / <code>{html.escape(self.tts_model)}</code>"
                if self.tts_model and self.tts_provider == "gemini_tts"
                else ""
            )
            items.append(
                f"• 음성 합성(TTS): {prov}{model_part} (<code>{voice}</code>) · "
                f"{self.tts_chars:,}자 ({self._tts_cost_label()})"
            )

        body = "<br/>".join(items)
        return (
            "<hr/>"
            f"<p><small><strong>📊 이번 회차 생성 리포트</strong><br/>{body}</small></p>"
        )
