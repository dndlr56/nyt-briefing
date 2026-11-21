"""무료 티어 우선 실행 + 할당량(429/RPD) 소진 시 유료 프로젝트 자동 전환(Failover) 클라이언트 풀."""

from __future__ import annotations

import os
import time
from typing import Any

from google import genai

from usage_tracker import RunUsageTracker


def _is_quota_or_rate_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "429",
            "resource_exhausted",
            "quota",
            "rate limit",
            "rate_limit",
            "too many requests",
        )
    )


def _is_hard_daily_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "per day",
            "per_day",
            "rpd",
            "daily",
            "limit: 0",
            "free_tier",
            "generaterequestsperday",
        )
    )


def _model_group(model: str) -> str:
    return "tts" if "tts" in (model or "").lower() else "text"


class GeminiClientPool:
    """
    무료 키(GEMINI_FREE_API_KEY)로 우선 호출하다가 일일/분당 할당량이 소진되면
    실행 중간에 유료 키(GEMINI_PAID_API_KEY)로 매끄럽게 전환합니다.
    텍스트 LLM과 TTS 모델은 할당량이 별도이므로 그룹별로 독립 관리합니다.
    """

    def __init__(
        self,
        free_api_key: str | None = None,
        paid_api_key: str | None = None,
        mode: str | None = None,  # "auto" | "free" | "paid"
        api_version: str = "v1beta",
    ) -> None:
        self.api_version = api_version
        raw_mode = (mode or os.getenv("GEMINI_API_TIER") or "auto").strip().lower()
        if raw_mode not in ("auto", "free", "paid"):
            raw_mode = "auto"
        self.mode = raw_mode

        env_free = (os.getenv("GEMINI_FREE_API_KEY") or "").strip()
        env_paid = (os.getenv("GEMINI_PAID_API_KEY") or "").strip()
        env_default = (os.getenv("GEMINI_API_KEY") or "").strip()

        self.free_api_key = (free_api_key if free_api_key is not None else env_free).strip()
        self.paid_api_key = (paid_api_key if paid_api_key is not None else env_paid).strip()

        # 단일 GEMINI_API_KEY만 설정된 기존 환경 호환
        if not self.free_api_key and not self.paid_api_key and env_default:
            if self.mode == "paid":
                self.paid_api_key = env_default
            else:
                self.free_api_key = env_default
        elif not self.paid_api_key and env_default and env_default != self.free_api_key:
            self.paid_api_key = env_default

        self._free_client: genai.Client | None = None
        self._paid_client: genai.Client | None = None
        self._group_tier: dict[str, str] = {}
        self.failover_events: list[str] = []

    @property
    def has_free_key(self) -> bool:
        return bool(self.free_api_key)

    @property
    def has_paid_fallback(self) -> bool:
        return self.mode != "free" and bool(self.paid_api_key)

    def active_tier_for(self, model: str = "") -> str:
        group = _model_group(model)
        if group in self._group_tier:
            return self._group_tier[group]
        if self.mode == "paid":
            tier = "paid" if self.paid_api_key else "free"
        elif self.free_api_key:
            tier = "free"
        elif self.paid_api_key:
            tier = "paid"
        else:
            tier = "paid" if self.mode == "paid" else "free"
        self._group_tier[group] = tier
        return tier

    def is_free_tier_for(self, model: str = "") -> bool:
        return self.active_tier_for(model) == "free"

    def _get_client(self, tier: str) -> genai.Client:
        # SDK 기본값(retry_options: max_retries=4, 즉 5회 시도)은 타임아웃/503 시
        # 한 호출 안에서 5 × timeout(최대 17분+) 동안 침묵 대기하며 풀의 유료 전환을 막습니다.
        # 따라서 SDK 내부 재시도를 끄고(attempts=1) GeminiClientPool이 직접 재시도·전환을 제어합니다.
        http_options: dict[str, Any] = {
            "api_version": self.api_version,
            "timeout": 180_000,  # hop당 180초 (ms)
            "retry_options": {"attempts": 1},
        }
        if tier == "free":
            if self._free_client is None:
                kwargs: dict[str, Any] = {"http_options": http_options}
                if self.free_api_key:
                    kwargs["api_key"] = self.free_api_key
                self._free_client = genai.Client(**kwargs)
            return self._free_client

        if self._paid_client is None:
            kwargs = {"http_options": http_options}
            if self.paid_api_key:
                kwargs["api_key"] = self.paid_api_key
            self._paid_client = genai.Client(**kwargs)
        return self._paid_client

    def switch_group_to_paid(self, model: str, reason: str) -> bool:
        """지정 모델 그룹을 유료 티어 프로젝트로 즉시 전환합니다."""
        if not self.has_paid_fallback:
            return False
        group = _model_group(model)
        if self._group_tier.get(group) == "paid":
            return False
        self._group_tier[group] = "paid"
        msg = f"[{group}] 무료 티어 할당량/지연 감지 -> 유료 프로젝트 자동 전환 ({reason})"
        self.failover_events.append(msg)
        print(f"      [FAILOVER] {msg}")
        return True

    def create_interaction(
        self,
        *,
        model: str,
        stage: str,
        usage_tracker: RunUsageTracker | None = None,
        max_retries: int = 3,
        timeout: float = 180.0,
        **create_kwargs: Any,
    ) -> tuple[Any, str]:
        """
        client.interactions.create(...)를 호출하며:
        - SDK 내부 암묵적 재시도를 비활성화하고 각 호출에 기본 180초 타임아웃을 적용해 hang을 방지합니다.
        - 무료 티어 한도 도달, 타임아웃, 또는 반복 503 시 유료 프로젝트로 자동 전환합니다.
        반환: (interaction, actual_tier)
        """
        last_exc: Exception | None = None
        rpm_waited_on_free = False
        transient_503_on_free = False

        for attempt in range(1, max_retries + 2):
            tier = self.active_tier_for(model)
            client = self._get_client(tier)
            call_kwargs = dict(create_kwargs)
            call_kwargs.setdefault("timeout", timeout)
            started_at = time.monotonic()
            try:
                interaction = client.interactions.create(model=model, **call_kwargs)
                if usage_tracker is not None:
                    usage_tracker.add_interaction(
                        interaction, stage=stage, model=model, tier=tier
                    )
                return interaction, tier
            except Exception as exc:
                last_exc = exc
                elapsed = time.monotonic() - started_at
                err_lower = str(exc).lower()
                exc_name_lower = type(exc).__name__.lower()
                is_quota = _is_quota_or_rate_error(exc)
                is_timeout = (
                    "timeout" in err_lower
                    or "timed out" in err_lower
                    or "interrupted" in err_lower
                    or "timeout" in exc_name_lower
                    or "interrupted" in exc_name_lower
                )
                is_503 = (
                    "503" in err_lower
                    or "unavailable" in err_lower
                    or "overloaded" in err_lower
                )

                if tier == "free" and is_quota and self.has_paid_fallback:
                    # 일일 한도(RPD) 소진이거나 이미 1회 RPM 대기를 거쳤다면 즉시 유료 프로젝트로 전환
                    if _is_hard_daily_quota_error(exc) or rpm_waited_on_free:
                        self.switch_group_to_paid(model, reason=str(exc)[:140])
                        continue
                    # 일시적 분당 제한(RPM) 가능성: 1회만 짧게 대기 후 재시도, 다시 막히면 유료 전환
                    rpm_waited_on_free = True
                    print(
                        f"      [WARN] {stage} 무료 티어 속도 제한(429, {elapsed:.1f}s) -> 22초 대기 후 재시도 "
                        "(재발 시 유료 프로젝트로 자동 전환)..."
                    )
                    time.sleep(22)
                    continue

                if tier == "free" and is_timeout and self.has_paid_fallback:
                    # 이미 180초를 대기했으므로 무료 티어에서 또 3분을 낭비하지 않고 즉시 유료로 전환
                    self.switch_group_to_paid(
                        model,
                        reason=f"무료 티어 타임아웃 ({type(exc).__name__}, {elapsed:.1f}s)",
                    )
                    continue

                if tier == "free" and is_503 and self.has_paid_fallback:
                    if transient_503_on_free:
                        self.switch_group_to_paid(
                            model,
                            reason=f"무료 티어 503/과부하 반복 ({type(exc).__name__}, {elapsed:.1f}s)",
                        )
                        continue
                    transient_503_on_free = True
                    print(
                        f"      [WARN] {stage} 무료 티어 일시 과부하 ({type(exc).__name__}, {elapsed:.1f}s) -> "
                        "5초 후 1회 재시도 (재발 시 유료 전환)..."
                    )
                    time.sleep(5)
                    continue

                if attempt <= max_retries:
                    wait_sec = (25 * attempt) if is_quota else (5 * attempt)
                    print(
                        f"      [WARN] {stage} [{tier}] failed (attempt {attempt}/{max_retries}, {elapsed:.1f}s): "
                        f"{type(exc).__name__}: {exc}. Retrying in {wait_sec}s..."
                    )
                    time.sleep(wait_sec)
                else:
                    break

        raise RuntimeError(f"{stage} 호출 실패: {last_exc}")
