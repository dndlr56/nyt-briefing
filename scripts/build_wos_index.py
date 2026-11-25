"""Web of Science Core Collection CSV(SCIE/SSCI/AHCI/ESCI)를 경량 gzip JSON 인덱스로 컴파일합니다.

사용 예:
    python scripts/build_wos_index.py \
      --input-dir "C:/Users/JWI/Downloads/Web of Science Core Collection(June 15, 2026)" \
      --output data/wos_core_index.json.gz
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from deep_dive.wos_index import (  # noqa: E402
    DEFAULT_WOS_INDEX_PATH,
    EDITION_BITS,
    EDITIONS_ORDER,
    normalize_issn,
    normalize_journal_title,
)

EDITION_FILE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("SCIE", "SCIE"),
    ("SSCI", "SSCI"),
    ("AHCI", "AHCI"),
    ("ESCI", "ESCI"),
)

_DATE_IN_NAME_RE = re.compile(r"\(([^)]+)\)")


def _detect_edition(filename: str) -> str | None:
    upper = filename.upper()
    for code, token in EDITION_FILE_PATTERNS:
        if f"({token})" in upper or upper.startswith(f"{token}.") or f"_{token}." in upper:
            return code
    return None


def build_wos_index(
    input_dir: Path,
    output_path: Path = DEFAULT_WOS_INDEX_PATH,
    as_of: str = "",
) -> dict[str, int | str]:
    """CSV 4종을 읽어 ISSN/eISSN + 정규화 저널명 기반 경량 gzip JSON 인덱스를 생성합니다."""
    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"입력 폴더를 찾을 수 없습니다: {input_dir}")

    csv_files = sorted(input_dir.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"CSV 파일이 없습니다: {input_dir}")

    resolved_as_of = as_of.strip()
    if not resolved_as_of:
        m = _DATE_IN_NAME_RE.search(input_dir.name)
        resolved_as_of = m.group(1).strip() if m else ""

    cat_to_id: dict[str, int] = {}
    categories_list: list[str] = []

    def _cat_id(cat: str) -> int:
        if cat not in cat_to_id:
            cat_to_id[cat] = len(categories_list)
            categories_list.append(cat)
        return cat_to_id[cat]

    issn_bits: dict[str, int] = {}
    issn_cats: dict[str, set[int]] = {}

    title_bits: dict[str, int] = {}
    title_cats: dict[str, set[int]] = {}

    rows_by_edition: dict[str, int] = {}

    for csv_path in csv_files:
        edition = _detect_edition(csv_path.name)
        if not edition:
            print(f"[SKIP] 에디션 미식별 CSV: {csv_path.name}")
            continue

        bit = EDITION_BITS[edition]
        row_count = 0
        with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                row_count += 1
                raw_cats = (row.get("Web of Science Categories") or "").split("|")
                row_cat_ids = [_cat_id(c.strip()) for c in raw_cats if c and c.strip()]

                raw_issns = [row.get("ISSN") or "", row.get("eISSN") or ""]
                for raw_issn in raw_issns:
                    norm_issn = normalize_issn(raw_issn)
                    if not norm_issn:
                        continue
                    issn_bits[norm_issn] = issn_bits.get(norm_issn, 0) | bit
                    issn_cats.setdefault(norm_issn, set()).update(row_cat_ids)

                norm_title = normalize_journal_title(row.get("Journal title") or "")
                if norm_title and len(norm_title) >= 3:
                    title_bits[norm_title] = title_bits.get(norm_title, 0) | bit
                    title_cats.setdefault(norm_title, set()).update(row_cat_ids)

        rows_by_edition[edition] = row_count

    if not issn_bits:
        raise RuntimeError("유효한 WoS ISSN 레코드를 하나도 읽지 못했습니다.")

    payload = {
        "as_of": resolved_as_of,
        "editions_order": list(EDITIONS_ORDER),
        "categories": categories_list,
        "issns": {
            issn: [issn_bits[issn], sorted(issn_cats[issn])]
            for issn in sorted(issn_bits)
        },
        "titles": {
            title: [title_bits[title], sorted(title_cats[title])]
            for title in sorted(title_bits)
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(output_path, "wb", compresslevel=9) as gz:
        gz.write(raw_json)

    gz_size = output_path.stat().st_size
    return {
        "as_of": resolved_as_of,
        "output": str(output_path),
        "raw_bytes": len(raw_json),
        "gz_bytes": gz_size,
        "unique_issns": len(issn_bits),
        "unique_titles": len(title_bits),
        "unique_categories": len(categories_list),
        **{f"rows_{k}": v for k, v in rows_by_edition.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Web of Science Core Collection CSV를 경량 gzip JSON 인덱스로 컴파일"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="SCIE/SSCI/AHCI/ESCI CSV 파일이 들어있는 디렉터리",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_WOS_INDEX_PATH,
        help=f"출력 gzip JSON 경로 (기본: {DEFAULT_WOS_INDEX_PATH})",
    )
    parser.add_argument(
        "--as-of",
        default="",
        help="스냅샷 기준일 문자열 (미지정 시 폴더명 괄호에서 자동 추출)",
    )
    args = parser.parse_args()
    stats = build_wos_index(
        input_dir=args.input_dir,
        output_path=args.output,
        as_of=args.as_of,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
