"""Google Drive 마크다운 중심 아카이빙 (Master_Index Sheet + 요약/대본 Markdown + 원문 PDF/TXT).

외부 무거운 SDK 없이 `google-auth` + `requests`로 Drive v3 / Sheets v4 REST API를 직접 호출합니다.
개인 Google Drive 업로드를 위해 OAuth2 Refresh Token 환경변수를 사용합니다:
  - GOOGLE_DRIVE_CLIENT_ID
  - GOOGLE_DRIVE_CLIENT_SECRET
  - GOOGLE_DRIVE_REFRESH_TOKEN
  - GOOGLE_DRIVE_ROOT_FOLDER_ID (선택: 미지정 시 'Deep-Dive-Briefing' 폴더 자동 생성)
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import uuid

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
import requests

from deep_dive.models import (
    CurationDecision,
    DeepDiveStudyNote,
    FetchedDocument,
    resolve_section_headings,
)
from deep_dive.themes import ThemeSlot


DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
OAUTH_TOKEN_URI = "https://oauth2.googleapis.com/token"
DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]

ROOT_FOLDER_NAME = "Deep-Dive-Briefing"
MASTER_SHEET_NAME = "Master_Index"
MASTER_HEADERS = [
    "날짜",
    "분야",
    "하위 주제",
    "형식",
    "브리핑 제목",
    "원문 출처 / 저자",
    "원문 URL",
    "웹 리더 링크",
    "요약 노트(.md) 링크",
    "Drive 폴더 링크",
    "내 메모 / 별점",
]


@dataclass
class DriveArchiveResult:
    folder_id: str
    folder_url: str
    doc_id: str
    doc_url: str
    sheet_id: str
    sheet_url: str
    uploaded_pdf_count: int


def is_drive_configured() -> bool:
    return all(
        (os.getenv(k) or "").strip()
        for k in (
            "GOOGLE_DRIVE_CLIENT_ID",
            "GOOGLE_DRIVE_CLIENT_SECRET",
            "GOOGLE_DRIVE_REFRESH_TOKEN",
        )
    )


def _get_auth_headers() -> dict[str, str]:
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_DRIVE_REFRESH_TOKEN"].strip(),
        token_uri=OAUTH_TOKEN_URI,
        client_id=os.environ["GOOGLE_DRIVE_CLIENT_ID"].strip(),
        client_secret=os.environ["GOOGLE_DRIVE_CLIENT_SECRET"].strip(),
        scopes=DRIVE_SCOPES,
    )
    creds.refresh(GoogleAuthRequest())
    if not creds.token:
        raise RuntimeError("Google Drive OAuth2 액세스 토큰 갱신에 실패했습니다.")
    return {"Authorization": f"Bearer {creds.token}"}


def _escape_q(val: str) -> str:
    return val.replace("\\", "\\\\").replace("'", "\\'")


def _find_or_create_folder(
    headers: dict[str, str],
    name: str,
    parent_id: str | None = None,
) -> str:
    query_parts = [
        f"name = '{_escape_q(name)}'",
        "mimeType = 'application/vnd.google-apps.folder'",
        "trashed = false",
    ]
    if parent_id:
        query_parts.append(f"'{_escape_q(parent_id)}' in parents")

    resp = requests.get(
        f"{DRIVE_API_BASE}/files",
        headers=headers,
        params={"q": " and ".join(query_parts), "fields": "files(id, name)", "pageSize": 5},
        timeout=25,
    )
    resp.raise_for_status()
    files = resp.json().get("files", [])
    if files:
        return str(files[0]["id"])

    metadata: dict[str, object] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    create_resp = requests.post(
        f"{DRIVE_API_BASE}/files",
        headers={**headers, "Content-Type": "application/json"},
        json=metadata,
        params={"fields": "id"},
        timeout=25,
    )
    create_resp.raise_for_status()
    return str(create_resp.json()["id"])


def _upload_multipart_file(
    headers: dict[str, str],
    metadata: dict[str, object],
    content_bytes: bytes,
    content_mime: str,
) -> str:
    boundary = f"deepdive_{uuid.uuid4().hex}"
    meta_bytes = json.dumps(metadata, ensure_ascii=False).encode("utf-8")

    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
    ).encode("utf-8") + meta_bytes + (
        f"\r\n--{boundary}\r\n"
        f"Content-Type: {content_mime}\r\n\r\n"
    ).encode("utf-8") + content_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    resp = requests.post(
        f"{DRIVE_UPLOAD_BASE}/files",
        headers={
            **headers,
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        params={"uploadType": "multipart", "fields": "id"},
        data=body,
        timeout=90,
    )
    resp.raise_for_status()
    return str(resp.json()["id"])


def _find_or_create_master_sheet(headers: dict[str, str], root_folder_id: str) -> str:
    query = (
        f"name = '{_escape_q(MASTER_SHEET_NAME)}' and "
        "mimeType = 'application/vnd.google-apps.spreadsheet' and "
        f"'{_escape_q(root_folder_id)}' in parents and trashed = false"
    )
    resp = requests.get(
        f"{DRIVE_API_BASE}/files",
        headers=headers,
        params={"q": query, "fields": "files(id, name)", "pageSize": 5},
        timeout=25,
    )
    resp.raise_for_status()
    files = resp.json().get("files", [])
    if files:
        return str(files[0]["id"])

    create_resp = requests.post(
        f"{DRIVE_API_BASE}/files",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "name": MASTER_SHEET_NAME,
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "parents": [root_folder_id],
        },
        params={"fields": "id"},
        timeout=25,
    )
    create_resp.raise_for_status()
    sheet_id = str(create_resp.json()["id"])

    # 헤더 행 기록
    requests.put(
        f"{SHEETS_API_BASE}/{sheet_id}/values/A1:K1",
        headers={**headers, "Content-Type": "application/json"},
        params={"valueInputOption": "RAW"},
        json={"values": [MASTER_HEADERS]},
        timeout=25,
    ).raise_for_status()
    return sheet_id


def _append_master_sheet_row(
    headers: dict[str, str],
    sheet_id: str,
    row_values: list[str],
) -> None:
    resp = requests.post(
        f"{SHEETS_API_BASE}/{sheet_id}/values/A:K:append",
        headers={**headers, "Content-Type": "application/json"},
        params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
        json={"values": [row_values]},
        timeout=25,
    )
    resp.raise_for_status()


def _safe_folder_name(date_slug: str, title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9가-힣 _-]+", "", title).strip()[:45]
    return f"[{date_slug}] {slug or 'briefing'}"


def _safe_filename(text: str, max_len: int = 60) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "", text).strip()
    return cleaned[:max_len] or "briefing"


def _yaml_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


_URL_TOKEN_RE = re.compile(r"https?://[^\s)>]+")


def _escape_md_tildes(text: str) -> str:
    """마크다운·Google Docs 열람 시 '~'가 취소선(~~)으로 오인식되지 않도록 URL을 제외하고 유니코드 물결표('∼')로 치환합니다."""
    if "~" not in (text or ""):
        return text or ""
    parts: list[str] = []
    last = 0
    for m in _URL_TOKEN_RE.finditer(text):
        parts.append(text[last : m.start()].replace("~", "∼"))
        parts.append(m.group(0))
        last = m.end()
    parts.append(text[last:].replace("~", "∼"))
    return "".join(parts)


def render_study_note_markdown(
    date_slug: str,
    theme: ThemeSlot,
    decision: CurationDecision,
    note: DeepDiveStudyNote,
    docs: list[FetchedDocument],
    web_article_url: str = "",
) -> str:
    """알렉산드리아 도서관(Alexandria)·Obsidian·RAG 검색과 100% 호환되는 YAML frontmatter 포함 마크다운 연구 노트를 생성합니다."""
    mode_label = "1편 충실 요약" if decision.mode == "single_deep_dive" else f"{len(docs)}편 종합 요약"
    doc_lens = getattr(note, "doc_lens", "empirical_or_review") or "empirical_or_review"
    headings = resolve_section_headings(doc_lens)
    note_type = "legal" if doc_lens == "law_and_cases" else "paper"
    primary_url = docs[0].candidate.url if docs else ""
    primary_author = (
        docs[0].candidate.authors or docs[0].candidate.source_name
        if docs
        else "Deep Dive"
    )

    raw_tags = list(getattr(note, "tags", []) or [])
    if not raw_tags:
        raw_tags = [theme.theme_id]
        for tok in re.split(r"[·,/() ]+", decision.sub_subject_kr):
            cleaned_tok = tok.strip()
            if len(cleaned_tok) >= 2 and cleaned_tok not in raw_tags:
                raw_tags.append(cleaned_tok)
                if len(raw_tags) >= 4:
                    break

    lines = [
        "---",
        f'title: "{_yaml_escape(note.episode_title_kr)}"',
        f'date: "{date_slug}"',
        'folder: "docs"',
        f'type: "{note_type}"',
        'mode: "digest"',
        f'doc_lens: "{_yaml_escape(doc_lens)}"',
        f'theme_id: "{theme.theme_id}"',
        f'theme: "{_yaml_escape(theme.label_kr)}"',
        f'sub_subject: "{_yaml_escape(decision.sub_subject_kr)}"',
        f'briefing_mode: "{mode_label}"',
        f'author: "{_yaml_escape(primary_author)}"',
    ]
    if primary_url:
        lines.append(f'source_url: "{_yaml_escape(primary_url)}"')
    if web_article_url:
        lines.append(f'web_url: "{_yaml_escape(web_article_url)}"')
    lines.append("tags:")
    for tg in raw_tags[:5]:
        lines.append(f'  - "{_yaml_escape(tg)}"')
    lines.append(f'summary: "{_yaml_escape(note.subtitle_kr)}"')
    lines.append("sources:")
    for d in docs:
        lines.append(f'  - "{_yaml_escape(d.candidate.title)} ({_yaml_escape(d.candidate.source_name)})"')
    lines.extend(
        [
            "---",
            "",
            f"# {_escape_md_tildes(note.episode_title_kr)}",
            "",
            f"> {_escape_md_tildes(note.subtitle_kr)}",
            "",
            f"- **날짜**: {date_slug}",
            f"- **분야**: {theme.emoji} {theme.label_kr} · {_escape_md_tildes(decision.sub_subject_kr)} ({mode_label})",
        ]
    )
    if web_article_url:
        lines.append(f"- **웹 리더**: {web_article_url}")

    lines.extend(["", "## ⚡ 핵심 요약", ""])
    for bullet in note.tldr_bullets:
        lines.append(f"- {_escape_md_tildes(bullet)}")

    lines.extend(["", f"## {headings['sec1']}", "", _escape_md_tildes(note.overview_and_purpose_kr)])
    lines.extend(["", f"## {headings['sec2']}", "", _escape_md_tildes(note.methodology_and_evidence_kr)])

    lines.extend(["", f"## {headings['sec3']}", ""])
    for idx, sec in enumerate(note.body_sections, start=1):
        lines.append(f"### {idx}. {_escape_md_tildes(sec.heading_kr)}")
        lines.append("")
        lines.append(_escape_md_tildes(sec.content_kr))
        lines.append("")

    if note.summary_table:
        lines.extend([f"## {headings['sec4']}", "", "| 항목 | 원문 핵심 내용 |", "| :--- | :--- |"])
        for row in note.summary_table:
            item = _escape_md_tildes(row.item.replace("|", "\\|").replace("\n", " "))
            detail = _escape_md_tildes(row.detail_kr.replace("|", "\\|").replace("\n", " "))
            lines.append(f"| {item} | {detail} |")
        lines.append("")

    if note.key_quotes:
        lines.extend([f"## {headings['sec5']}", ""])
        for q in note.key_quotes:
            lines.append(f"> “{_escape_md_tildes(q.quote_original)}”")
            lines.append(">")
            lines.append(f"> {_escape_md_tildes(q.translation_kr)}")
            lines.append(f"> — *{_escape_md_tildes(q.source_label)}*")
            lines.append("")

    if note.author_stated_limitations_kr:
        lines.extend([f"## {headings['sec6']}", ""])
        for lim in note.author_stated_limitations_kr:
            lines.append(f"- {_escape_md_tildes(lim)}")
        lines.append("")

    if note.author_conclusions_kr:
        lines.extend([f"## {headings['sec7']}", ""])
        for conc in note.author_conclusions_kr:
            lines.append(f"- {_escape_md_tildes(conc)}")
        lines.append("")

    if note.chapters:
        lines.extend(["## 8. 오디오 챕터 구성", ""])
        for ch in note.chapters:
            lines.append(
                f"{ch.chapter_number}. **{_escape_md_tildes(ch.title_kr)}**: {_escape_md_tildes(ch.focus_kr)}"
            )
        lines.append("")

    lines.extend(["## 📚 원문 출처", ""])
    for d in docs:
        pub_part = f", {d.candidate.published}" if d.candidate.published else ""
        pdf_part = f" · [PDF]({d.candidate.pdf_url})" if d.candidate.pdf_url else ""
        lines.append(
            f"- **{_escape_md_tildes(d.candidate.title)}** ({_escape_md_tildes(d.candidate.source_name)}{pub_part}) — "
            f"[원문 보기]({d.candidate.url}){pdf_part}"
        )

    return "\n".join(lines).strip() + "\n"


def archive_to_google_drive(
    date_slug: str,
    theme: ThemeSlot,
    decision: CurationDecision,
    note: DeepDiveStudyNote,
    docs: list[FetchedDocument],
    doc_html: str = "",
    web_article_url: str = "",
    script_markdown: str = "",
) -> DriveArchiveResult:
    """
    마크다운 중심 Google Drive 아카이빙 실행 (불필요한 임시 Google Doc 경유 자동 PDF 생성 제거):
    1) Deep-Dive-Briefing/<5대 핵심 분야>/[YYYY-MM-DD] <title>/ 폴더 생성
    2) 알렉산드리아 호환 요약 노트 마크다운([YYYY-MM-DD] <제목>.md) 저장
    3) 오디오 대본 전문 마크다운([YYYY-MM-DD] <제목>_대본.md) 저장 (대본이 있을 경우)
    4) 원문 PDF(있을 경우) + full_text.txt 업로드
    5) Master_Index Google Sheet에 1행 추가
    """
    del doc_html  # 하위 호환 시그니처 유지 (자동 요약 PDF 생성 제거)
    headers = _get_auth_headers()

    root_id = (os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID") or "").strip()
    if not root_id:
        root_id = _find_or_create_folder(headers, ROOT_FOLDER_NAME)

    theme_folder_name = f"{theme.emoji} {theme.label_kr}".strip()
    category_id = _find_or_create_folder(headers, theme_folder_name, parent_id=root_id)

    day_folder_name = _safe_folder_name(date_slug, note.episode_title_kr)
    day_folder_id = _find_or_create_folder(headers, day_folder_name, parent_id=category_id)
    safe_title = _safe_filename(note.episode_title_kr)

    # 1) 요약 노트 마크다운(.md) 및 오디오 대본 전문(_대본.md) 업로드
    summary_md = render_study_note_markdown(
        date_slug=date_slug,
        theme=theme,
        decision=decision,
        note=note,
        docs=docs,
        web_article_url=web_article_url,
    )
    doc_id = _upload_multipart_file(
        headers=headers,
        metadata={
            "name": f"[{date_slug}] {safe_title}.md",
            "parents": [day_folder_id],
        },
        content_bytes=summary_md.encode("utf-8"),
        content_mime="text/markdown; charset=UTF-8",
    )
    doc_url = f"https://drive.google.com/file/d/{doc_id}/view"

    if script_markdown.strip():
        _upload_multipart_file(
            headers=headers,
            metadata={
                "name": f"[{date_slug}] {safe_title}_대본.md",
                "parents": [day_folder_id],
            },
            content_bytes=script_markdown.encode("utf-8"),
            content_mime="text/markdown; charset=UTF-8",
        )

    # 2) 원문 PDF 및 추출 텍스트(full_text.txt) 저장
    pdf_count = 0
    combined_texts: list[str] = []
    for idx, d in enumerate(docs, start=1):
        combined_texts.append(
            f"=== [{idx}] {d.candidate.title} ({d.candidate.source_name}) ===\n"
            f"URL: {d.candidate.url}\n"
            f"Authors: {d.candidate.authors}\n\n"
            f"{d.full_text}\n"
        )
        if d.pdf_bytes:
            pdf_name = d.pdf_filename or f"original_source_{idx}.pdf"
            _upload_multipart_file(
                headers=headers,
                metadata={"name": pdf_name, "parents": [day_folder_id]},
                content_bytes=d.pdf_bytes,
                content_mime="application/pdf",
            )
            pdf_count += 1

    _upload_multipart_file(
        headers=headers,
        metadata={"name": "full_text.txt", "parents": [day_folder_id]},
        content_bytes="\n\n".join(combined_texts).encode("utf-8"),
        content_mime="text/plain; charset=UTF-8",
    )

    # 3) Master_Index Google Sheet 갱신
    sheet_id = _find_or_create_master_sheet(headers, root_id)
    folder_url = f"https://drive.google.com/drive/folders/{day_folder_id}"
    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"

    sources_summary = " / ".join(
        f"{d.candidate.source_name}" + (f" ({d.candidate.authors[:40]})" if d.candidate.authors else "")
        for d in docs
    )
    primary_urls = "\n".join(d.candidate.url for d in docs)
    mode_label = "1편 충실 요약" if decision.mode == "single_deep_dive" else f"{len(docs)}편 종합 요약"

    _append_master_sheet_row(
        headers=headers,
        sheet_id=sheet_id,
        row_values=[
            date_slug,
            f"{theme.emoji} {theme.label_kr}",
            decision.sub_subject_kr,
            mode_label,
            note.episode_title_kr,
            sources_summary,
            primary_urls,
            web_article_url,
            doc_url,
            folder_url,
            "",
        ],
    )

    return DriveArchiveResult(
        folder_id=day_folder_id,
        folder_url=folder_url,
        doc_id=doc_id,
        doc_url=doc_url,
        sheet_id=sheet_id,
        sheet_url=sheet_url,
        uploaded_pdf_count=pdf_count,
    )
