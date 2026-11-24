"""기존 gh-pages 브랜치의 HTML 아카이브·포스트 전체를 최신 에디토리얼 디자인으로 소급 재렌더링합니다.

MP3 파일, RSS feed.xml GUID, metadata.json 이력은 보존하며,
metadata.json에 참조되지 않는 구형 중복 HTML(초기 한글 파일명 등)만 안전하게 정리합니다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from deep_dive.web_publisher import (  # noqa: E402
    _render_archive_index_html,
    render_article_page_from_parts,
)
from podcast_feed import _render_index_html  # noqa: E402


def upgrade_existing_post_html(
    old_html: str,
    ep_meta: dict,
    deep_feed_url: str,
) -> str:
    """기존 발행된 deep/posts/*.html에서 본문·대본·메타를 추출해 새 웹 리더 템플릿으로 재조립합니다."""
    soup = BeautifulSoup(old_html, "html.parser")

    h1 = soup.find("h1")
    episode_title = h1.get_text(strip=True) if h1 else ep_meta.get("title", "")

    date_el = soup.select_one(".date-meta")
    date_label = (
        date_el.get_text(strip=True)
        if date_el
        else f"{ep_meta.get('date_slug', '')} KST"
    )

    mp3_filename = (ep_meta.get("mp3_filename") or "").strip()
    mp3_rel_path = f"../episodes/{mp3_filename}" if mp3_filename else None
    duration_seconds = int(ep_meta.get("duration_seconds") or 0)

    cast_line = ep_meta.get("dialogue_cast_summary") or ""
    cast_box = soup.select_one(".cast-box")
    if cast_box:
        first_div = cast_box.find("div")
        if first_div:
            extracted = re.sub(r"^진행\s*구성:\s*", "", first_div.get_text(" ", strip=True))
            if extracted:
                cast_line = extracted

    main_el = soup.find("main")
    transcript_html = ""
    body_html = ""

    if main_el is not None:
        details_el = main_el.select_one("details.transcript")
        if details_el is not None:
            details_el.extract()
            details_el["id"] = "sec-transcript"
            details_el["data-toc"] = "전체 대화 대본"
            summary_el = details_el.find("summary")
            if summary_el:
                clean_sum = re.sub(r"^[^\w가-힣]+", "", summary_el.get_text(" ", strip=True))
                clean_sum = clean_sum.replace("펼쳐보기", "").strip()
                summary_el.string = clean_sum

            tbody = details_el.select_one(".transcript-body")
            if tbody is not None and tbody.select_one("#transcript-search") is None:
                filter_tag = soup.new_tag(
                    "input",
                    attrs={
                        "type": "search",
                        "id": "transcript-search",
                        "class": "transcript-filter",
                        "placeholder": "대본 내 키워드 검색",
                        "aria-label": "대본 내 키워드 검색",
                    },
                )
                cbox = tbody.select_one(".cast-box")
                if cbox is not None:
                    cbox.insert_after(filter_tag)
                else:
                    tbody.insert(0, filter_tag)

            # 두 번째 화자 색상 구분 클래스 부여
            seen_speakers: list[str] = []
            for sp in details_el.select(".turn .speaker"):
                label = sp.get_text(strip=True)
                if label and label not in seen_speakers:
                    seen_speakers.append(label)
                if label and seen_speakers and label != seen_speakers[0]:
                    classes = list(sp.get("class") or ["speaker"])
                    if "s2" not in classes:
                        classes.append("s2")
                    sp["class"] = classes

            transcript_html = str(details_el)

        # 섹션별 목차 라벨 보강 및 이모지 정리
        for idx, sec in enumerate(main_el.find_all("section", recursive=False), start=1):
            if not sec.get("id"):
                sec["id"] = f"sec-upgraded-{idx}"
            h2 = sec.find("h2")
            if h2 and not sec.get("data-toc"):
                clean_h2 = re.sub(r"^[⚡📚🎙️📖\s]+", "", h2.get_text(" ", strip=True)).strip()
                h2.string = clean_h2
                sec["data-toc"] = clean_h2

        body_html = "".join(str(child) for child in main_el.children).strip()
    else:
        body_html = ep_meta.get("description_html", "")

    return render_article_page_from_parts(
        episode_title=episode_title,
        date_label=date_label,
        body_html=body_html,
        mp3_rel_path=mp3_rel_path,
        duration_seconds=duration_seconds,
        cast_line=cast_line,
        transcript_html=transcript_html,
        rss_copy_url=deep_feed_url,
    )


def rebuild_pages(pages_dir: Path, base_url: str) -> dict[str, int]:
    clean_root = base_url.rstrip("/")
    deep_base = f"{clean_root}/deep"
    deep_feed_url = f"{deep_base}/feed.xml"

    rebuilt_news = 0
    rebuilt_deep_posts = 0
    removed_orphan_posts = 0

    # 1) 루트 모닝 뉴스 index.html 재렌더링
    news_meta_path = pages_dir / "episodes" / "metadata.json"
    if news_meta_path.exists():
        news_episodes = json.loads(news_meta_path.read_text(encoding="utf-8"))
        root_html = _render_index_html(episodes=news_episodes, base_url=clean_root)
        (pages_dir / "index.html").write_text(root_html, encoding="utf-8")
        rebuilt_news = len(news_episodes)

    # 2) Deep Dive 아카이브 index.html 및 개별 posts/*.html 재렌더링
    deep_dir = pages_dir / "deep"
    deep_meta_path = deep_dir / "episodes" / "metadata.json"
    if deep_meta_path.exists():
        deep_episodes = json.loads(deep_meta_path.read_text(encoding="utf-8"))
        deep_index_html = _render_archive_index_html(
            episodes=deep_episodes, base_url=deep_base
        )
        (deep_dir / "index.html").write_text(deep_index_html, encoding="utf-8")

        posts_dir = deep_dir / "posts"
        active_filenames: set[str] = set()
        for ep in deep_episodes:
            art_fn = (ep.get("article_filename") or "").strip()
            if not art_fn:
                continue
            active_filenames.add(art_fn)
            art_path = posts_dir / art_fn
            if art_path.exists():
                old_html = art_path.read_text(encoding="utf-8")
            else:
                old_html = f"<main>{ep.get('description_html', '')}</main>"
            upgraded = upgrade_existing_post_html(
                old_html=old_html,
                ep_meta=ep,
                deep_feed_url=deep_feed_url,
            )
            art_path.write_text(upgraded, encoding="utf-8")
            rebuilt_deep_posts += 1

        if posts_dir.exists():
            for existing_file in posts_dir.glob("*.html"):
                if existing_file.name not in active_filenames:
                    existing_file.unlink()
                    removed_orphan_posts += 1

    return {
        "news_episodes": rebuilt_news,
        "deep_posts": rebuilt_deep_posts,
        "removed_orphan_posts": removed_orphan_posts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="gh-pages 웹 리더 UI 소급 재렌더링")
    parser.add_argument("--pages-dir", type=Path, required=True, help="gh-pages 루트 디렉터리")
    parser.add_argument(
        "--base-url",
        default="https://dndlr56.github.io/nyt-briefing",
        help="공개 루트 URL",
    )
    args = parser.parse_args()
    stats = rebuild_pages(args.pages_dir, args.base_url)
    print(
        f"REBUILD_OK: news={stats['news_episodes']} episodes, "
        f"deep_posts={stats['deep_posts']} upgraded, "
        f"orphan_html_removed={stats['removed_orphan_posts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
