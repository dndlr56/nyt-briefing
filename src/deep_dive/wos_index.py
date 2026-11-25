"""Web of Science Core Collection (SCIE / SSCI / AHCI / ESCI) 경량 저널 인덱스 로더 및 테마 정합성 점수기.

표준 라이브러리(`gzip`, `json`)만 사용해 `data/wos_core_index.json.gz`를 수십 ms 내에 로드합니다.
GitHub Actions 환경에서도 외부 파일이나 API 키 없이 100% 오프라인으로 동작합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import gzip
import json
from pathlib import Path
import re
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_WOS_INDEX_PATH = REPO_ROOT / "data" / "wos_core_index.json.gz"

EDITIONS_ORDER: tuple[str, ...] = ("SCIE", "SSCI", "AHCI", "ESCI")
EDITION_BITS: dict[str, int] = {
    "SCIE": 1,
    "SSCI": 2,
    "AHCI": 4,
    "ESCI": 8,
}
CORE_EDITIONS: frozenset[str] = frozenset({"SCIE", "SSCI", "AHCI"})

_ISSN_RE = re.compile(r"(\d{4})-?(\d{3}[\dXx])")
_SOURCE_SUFFIX_RE = re.compile(
    r"\s*\((?:OpenAlex OA|Europe PMC OA|PMC OA|Semantic Scholar OA|Open Access|News & Research|Medicine & Neuroscience)\)\s*$",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_issn(raw: str | None) -> str:
    """ISSN / eISSN 문자열을 표준 'XXXX-XXXX' 대문자 형식으로 정규화합니다."""
    if not raw:
        return ""
    m = _ISSN_RE.search(str(raw).strip())
    if not m:
        return ""
    return f"{m.group(1)}-{m.group(2).upper()}"


def normalize_journal_title(raw: str | None) -> str:
    """저널명을 보조 매칭용 정규화 문자열(소문자·영숫자 공백)로 변환합니다."""
    if not raw:
        return ""
    cleaned = _SOURCE_SUFFIX_RE.sub("", str(raw).strip())
    cleaned = cleaned.replace("&", " and ")
    cleaned = _NON_ALNUM_RE.sub(" ", cleaned.lower()).strip()
    return cleaned


def decode_edition_mask(mask: int) -> tuple[str, ...]:
    return tuple(ed for ed in EDITIONS_ORDER if mask & EDITION_BITS[ed])


@dataclass(frozen=True)
class WosMatch:
    editions: tuple[str, ...]
    categories: tuple[str, ...]
    matched_by: str  # "issn" | "title"

    @property
    def is_core(self) -> bool:
        return any(ed in CORE_EDITIONS for ed in self.editions)

    @property
    def is_esci_only(self) -> bool:
        return bool(self.editions) and not self.is_core

    @property
    def badge_label(self) -> str:
        return " · ".join(self.editions)


class WosJournalIndex:
    """메모리 상주 WoS Core Collection ISSN/저널명 조회 인덱스."""

    def __init__(
        self,
        as_of: str = "",
        categories: list[str] | None = None,
        issns: dict[str, list] | None = None,
        titles: dict[str, list] | None = None,
    ) -> None:
        self.as_of = as_of
        self._categories: tuple[str, ...] = tuple(categories or ())
        self._issns: dict[str, list] = issns or {}
        self._titles: dict[str, list] = titles or {}

    @property
    def is_loaded(self) -> bool:
        return bool(self._issns)

    @property
    def issn_count(self) -> int:
        return len(self._issns)

    @property
    def title_count(self) -> int:
        return len(self._titles)

    @classmethod
    def from_gzip_json(cls, path: Path = DEFAULT_WOS_INDEX_PATH) -> "WosJournalIndex":
        if not path.exists():
            return cls()
        with gzip.open(path, "rb") as gz:
            data = json.loads(gz.read().decode("utf-8"))
        return cls(
            as_of=str(data.get("as_of") or ""),
            categories=list(data.get("categories") or []),
            issns=dict(data.get("issns") or {}),
            titles=dict(data.get("titles") or {}),
        )

    def _entry_to_match(self, entry: list, matched_by: str) -> WosMatch:
        mask = int(entry[0])
        cat_ids = entry[1] or []
        editions = decode_edition_mask(mask)
        cats = tuple(
            self._categories[idx]
            for idx in cat_ids
            if isinstance(idx, int) and 0 <= idx < len(self._categories)
        )
        return WosMatch(editions=editions, categories=cats, matched_by=matched_by)

    def lookup(
        self,
        issns: Iterable[str] = (),
        journal_title: str = "",
    ) -> WosMatch | None:
        """ISSN 목록으로 먼저 조회하고, 미매칭 시 정규화 저널명 완전일치로 보조 조회합니다."""
        if not self._issns and not self._titles:
            return None

        combined_mask = 0
        combined_cat_ids: set[int] = set()
        for raw_issn in issns:
            norm = normalize_issn(raw_issn)
            if not norm:
                continue
            entry = self._issns.get(norm)
            if entry is not None:
                combined_mask |= int(entry[0])
                combined_cat_ids.update(int(i) for i in (entry[1] or []))

        if combined_mask:
            return self._entry_to_match(
                [combined_mask, sorted(combined_cat_ids)],
                matched_by="issn",
            )

        norm_title = normalize_journal_title(journal_title)
        if norm_title and len(norm_title) >= 4:
            entry = self._titles.get(norm_title)
            if entry is not None:
                return self._entry_to_match(entry, matched_by="title")

        return None


@lru_cache(maxsize=2)
def get_wos_index(path_str: str | None = None) -> WosJournalIndex:
    """기본 WoS 인덱스를 프로세스당 1회만 로드해 캐시합니다."""
    path = Path(path_str) if path_str else DEFAULT_WOS_INDEX_PATH
    try:
        return WosJournalIndex.from_gzip_json(path)
    except Exception:
        return WosJournalIndex()


def compute_wos_rank_score(
    wos_editions: Iterable[str],
    wos_categories: Iterable[str],
    venue_type: str = "",
    preferred_editions: Iterable[str] = (),
    preferred_categories: Iterable[str] = (),
    excluded_categories: Iterable[str] = (),
) -> int:
    """
    저널 트랙 후보의 WoS 등재 티어 및 테마 카테고리 정합성 점수를 계산합니다.

    - SCIE/SSCI/AHCI 핵심 등재 저널: 기본 +30
    - 테마 선호 에디션 일치: +10
    - 테마 선호 WoS 카테고리 일치: +12
    - ESCI 단독 등재 저널: 기본 +10 (카테고리 일치 시 +6)
    - 비저널 소스(preprint / conference / repository / report / essay / legal_column): 중립 +15
      (CS 탑 학회·arXiv·NBER·BIS·Aeon·Quanta 등이 미등재 저널 페널티를 받지 않도록 보호)
    - WoS 미등재 저널(venue_type == 'journal'이고 미등재): -20
    - 테마 배제 카테고리만 해당(선호 카테고리 0건): -18
    """
    editions_set = {str(e).upper() for e in wos_editions if e}
    cats_lower = [str(c).strip().lower() for c in wos_categories if c and str(c).strip()]
    pref_eds = {str(e).upper() for e in preferred_editions if e}
    pref_cats = [str(c).strip().lower() for c in preferred_categories if c and str(c).strip()]
    excl_cats = [str(c).strip().lower() for c in excluded_categories if c and str(c).strip()]
    vtype = (venue_type or "").strip().lower()

    has_pref_cat = bool(
        pref_cats and any(any(p in cat for p in pref_cats) for cat in cats_lower)
    )
    has_excl_cat = bool(
        excl_cats and any(any(x in cat for x in excl_cats) for cat in cats_lower)
    )

    if editions_set & CORE_EDITIONS:
        score = 30
        if pref_eds and (editions_set & pref_eds):
            score += 10
        if has_pref_cat:
            score += 12
        elif has_excl_cat:
            score -= 18
        return score

    if "ESCI" in editions_set:
        score = 10
        if has_pref_cat:
            score += 6
        elif has_excl_cat:
            score -= 12
        return score

    if vtype == "journal":
        return -20

    # 프리프린트·탑 학회·싱크탱크 보고서·장문 에세이·판례는 중립 보호
    return 15
