"""개인 Google Drive 자동 아카이빙용 OAuth2 Refresh Token 1회 발급 도우미.

사용법 (로컬 PC에서 실행):
  1. Google Cloud Console → API 및 서비스 → Drive API & Sheets API 활성화
  2. 사용자 인증 정보 → OAuth 2.0 클라이언트 ID 생성 (유형: 데스크톱 앱)
  3. 아래 명령 실행:
     python scripts/get_drive_refresh_token.py --client-id <ID> --client-secret <SECRET>
  4. 브라우저 인증 후 출력되는 GOOGLE_DRIVE_REFRESH_TOKEN을 GitHub Secrets에 등록
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.parse
import webbrowser

import requests


SCOPES = (
    "https://www.googleapis.com/auth/drive.file "
    "https://www.googleapis.com/auth/spreadsheets"
)
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"


def main() -> int:
    parser = argparse.ArgumentParser(description="Google Drive OAuth2 Refresh Token 발급")
    parser.add_argument("--client-id", required=True, help="OAuth 2.0 Client ID")
    parser.add_argument("--client-secret", required=True, help="OAuth 2.0 Client Secret")
    args = parser.parse_args()

    auth_code_holder: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if "code" in qs:
                auth_code_holder["code"] = qs["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    "<h2>✅ 인증 완료! 터미널로 돌아가 Refresh Token을 확인하세요.</h2>".encode("utf-8")
                )
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Authorization code missing.")

        def log_message(self, format: str, *msg_args: object) -> None:
            return

    auth_url = AUTH_URI + "?" + urllib.parse.urlencode(
        {
            "client_id": args.client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPES,
            "access_type": "offline",
            "prompt": "consent",
        }
    )

    print("1) 브라우저에서 Google 계정 로그인 및 Drive/Sheets 권한을 승인해 주세요:")
    print(f"   {auth_url}\n")
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", REDIRECT_PORT), CallbackHandler)
    while "code" not in auth_code_holder:
        server.handle_request()
    server.server_close()

    resp = requests.post(
        TOKEN_URI,
        data={
            "code": auth_code_holder["code"],
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    refresh_token = payload.get("refresh_token")

    if not refresh_token:
        print("❌ refresh_token이 응답에 포함되지 않았습니다. 앱 승인 기록을 제거 후 다시 시도해 주세요.")
        return 1

    print("\n✅ GitHub Secrets에 아래 3가지를 등록하세요:")
    print(f"GOOGLE_DRIVE_CLIENT_ID={args.client_id}")
    print(f"GOOGLE_DRIVE_CLIENT_SECRET={args.client_secret}")
    print(f"GOOGLE_DRIVE_REFRESH_TOKEN={refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
