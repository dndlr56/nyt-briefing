"""NYT 모닝 브리핑 파이프라인 실행 진입점."""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from curator import curate_briefing
from nyt_fetcher import fetch_recent_articles
from telegram_sender import build_messages, send_telegram_messages


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="뉴욕타임즈 주요 기사를 번역·큐레이션해 텔레그램으로 전송합니다."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="텔레그램으로 전송하지 않고 생성된 메시지를 콘솔에만 출력합니다.",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=36,
        help="수집할 최근 기사 시간 범위 (기본값: 36시간)",
    )
    args = parser.parse_args()

    print(f"[1/3] Fetching NYT RSS articles (last {args.hours}h)...")
    articles = fetch_recent_articles(hours=args.hours)
    print(f"      Collected {len(articles)} unique articles.")

    if not articles:
        print("[ERROR] No articles fetched from NYT RSS feeds.", file=sys.stderr)
        return 1

    print("[2/3] Curating & translating briefing with Gemini...")
    briefing = curate_briefing(articles)
    messages = build_messages(briefing)
    print(f"      Prepared {len(messages)} Telegram message(s).")

    if args.dry_run:
        print("\n========== DRY RUN OUTPUT ==========")
        for idx, msg in enumerate(messages, start=1):
            print(f"\n--- Part {idx}/{len(messages)} ---\n{msg}")
        print("\n====================================")
        return 0

    print("[3/3] Sending briefing to Telegram...")
    send_telegram_messages(messages)
    print("[DONE] Morning briefing delivered successfully!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
