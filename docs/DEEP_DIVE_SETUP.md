# 📘 Deep Dive Knowledge Briefing 설정 및 벤치마크 가이드

기존 글로벌 모닝 뉴스(`feed.xml`)와 완전히 분리되어 동작하는 **학술 논문·에세이·보고서·판례 심층 브리핑 파이프라인**입니다.

---

## 1. 구독 및 열람 주소 (배포 후)
- **전용 웹 리더 (정독용 아카이브)**: `https://dndlr56.github.io/nyt-briefing/deep/`
- **AntennaPod 독립 팟캐스트 RSS**: `https://dndlr56.github.io/nyt-briefing/deep/feed.xml`

---

## 2. 요일별 자동 테마 로테이션 (KST 기준)
| 요일 | 테마 ID | 분야 | 대표 1차 소스 (전문 확보 우선) |
| :--- | :--- | :--- | :--- |
| 월 | `ai_tech` | AI·기술·사회 | arXiv(`cs.AI`/`cs.CL`/`cs.CY`), Nature Machine Intelligence, DeepMind Blog, MIT Tech Review |
| 화 | `science_mind` | 종합과학·뇌인지 | Quanta Magazine, Nature/Science OA, Nature Comms, Science Advances, PMC 인지·학습 리뷰 |
| 수 | `health_medicine` | 의학·건강·장수 | PMC OA(수면·운동·대사·장수·정신의학 리뷰), Nature Medicine, eLife |
| 목 | `econ_geopolitics` | 경제·금융·지정학 | NBER, BIS, IMF, Brookings, RAND, CSIS, arXiv(`q-fin`/`econ`) |
| 금 | `law_cases` | 법학·판례 입문 | SCOTUSblog, Harvard Law Review, Lawfare, Verfassungsblog, arXiv AI 규제/저작권 |
| 토·일 | `weekend_special` | 주말 무제한 스페셜 | Aeon, Quanta, Foreign Affairs, Brookings, SCOTUSblog, arXiv 대형 서베이 |

---

## 3. 토큰 소모량 실측 & 튜닝 방법
GitHub Actions에서 **`Actions → Deep Dive Knowledge Briefing → Run workflow`**로 수동 실행하면:
1. 텔레그램 알림과 워크플로우 로그, `output/deep_dive/run_summary_*.json`에 **단계별 실제 입력·출력·추론 토큰 및 유료 환산 비용**이 기록됩니다.
2. 먼저 `target_minutes=30`, `api_tier=free`, `note_model=gemini-3.8-flash`, `script_model=gemini-3.8-flash`, `tts_provider=edge_tts`로 테스트해 토큰과 오디오 품질을 확인한 뒤,
   - 더 긴 휴버만식 강의를 원하시면 `target_minutes=45` 또는 `60`으로 늘리고,
   - 더 깊은 서술을 원하시면 `script_model=gemini-4-hydrogen`으로 비교해 보세요.
3. 특정 논문이나 PDF 링크를 직접 돌려보고 싶다면 `source_url` 입력칸에 URL(예: arXiv PDF 링크)을 넣으면 바로 해부합니다.

---

## 4. Google Drive 3단 아카이빙 설정 (선택 — 최초 1회)
시크릿을 등록하지 않아도 웹 리더·RSS·텔레그램은 정상 작동합니다. Google Drive 자동 백업을 켜려면:

1. Google Cloud Console에서 프로젝트 선택 → **Google Drive API** 및 **Google Sheets API** 사용 설정
2. **OAuth 동의 화면** 구성 (테스트 사용자에 본인 Gmail 추가)
3. **사용자 인증 정보 → OAuth 2.0 클라이언트 ID (데스크톱 앱)** 생성
4. 로컬 PC에서 1회 실행:
   ```bash
   python scripts/get_drive_refresh_token.py --client-id "<ID>" --client-secret "<SECRET>"
   ```
5. 출력된 값들을 GitHub 저장소 `Settings → Secrets and variables → Actions`에 등록:
   - `GOOGLE_DRIVE_CLIENT_ID`
   - `GOOGLE_DRIVE_CLIENT_SECRET`
   - `GOOGLE_DRIVE_REFRESH_TOKEN`
   - *(선택)* `GOOGLE_DRIVE_ROOT_FOLDER_ID` (미입력 시 드라이브 루트에 `Deep-Dive-Briefing` 폴더 자동 생성)

완료되면 매일 실행 시 Google Drive에 아래 구조로 자동 저장됩니다:
```text
Deep-Dive-Briefing/
├── Master_Index (Google Sheet)
└── YYYY-MM/
    └── YYYY-MM-DD_<theme>_<제목>/
        ├── [YYYY-MM-DD] <제목> (Google Doc)
        ├── <원문>.pdf (PDF 확보 시 — NotebookLM 투입 가능)
        └── full_text.txt
```
