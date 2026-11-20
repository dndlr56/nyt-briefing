"""Google Drive 3단 아카이빙 (Master_Index Sheet + 일별 Google Doc + 원문 PDF/TXT).

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

from deep_dive.models import CurationDecision, DeepDiveStudyNote, FetchedDocument
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
    "Google Doc 링크",
    "Drive 폴더(PDF) 링크",
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


def _safe_folder_name(date_slug: str, theme_id: str, title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9가-힣 _-]+", "", title).strip()[:45]
    return f"{date_slug}_{theme_id}_{slug or 'briefing'}"


def archive_to_google_drive(
    date_slug: str,
    theme: ThemeSlot,
    decision: CurationDecision,
    note: DeepDiveStudyNote,
    docs: list[FetchedDocument],
    doc_html: str,
    web_article_url: str = "",
) -> DriveArchiveResult:
    """
    3단 Google Drive 아카이빙 실행:
    1) Deep-Dive-Briefing/YYYY-MM/YYYY-MM-DD_<theme>_<title>/ 폴더 생성
    2) 구조화 브리핑 노트 Google Doc 업로드 (HTML → Google Docs 자동 변환)
    3) 원문 PDF(있을 경우) + full_text.txt 업로드
    4) Master_Index Google Sheet에 1행 추가
    """
    headers = _get_auth_headers()

    root_id = (os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID") or "").strip()
    if not root_id:
        root_id = _find_or_create_folder(headers, ROOT_FOLDER_NAME)

    month_name = date_slug[:7]  # YYYY-MM
    month_id = _find_or_create_folder(headers, month_name, parent_id=root_id)

    day_folder_name = _safe_folder_name(date_slug, theme.theme_id, note.episode_title_kr)
    day_folder_id = _find_or_create_folder(headers, day_folder_name, parent_id=month_id)

    # 1) 브리핑 노트 Google Doc 생성
    wrapped_html = (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='UTF-8'></head>"
        f"<body><h1>{note.episode_title_kr}</h1>{doc_html}</body></html>"
    )
    doc_id = _upload_multipart_file(
        headers=headers,
        metadata={
            "name": f"[{date_slug}] {note.episode_title_kr}",
            "mimeType": "application/vnd.google-apps.document",
            "parents": [day_folder_id],
        },
        content_bytes=wrapped_html.encode("utf-8"),
        content_mime="text/html; charset=UTF-8",
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
    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"

    sources_summary = " / ".join(
        f"{d.candidate.source_name}" + (f" ({d.candidate.authors[:40]})" if d.candidate.authors else "")
        for d in docs
    )
    primary_urls = "\n".join(d.candidate.url for d in docs)
    mode_label = "1편 심층" if decision.mode == "single_deep_dive" else f"{len(docs)}편 종합"

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
