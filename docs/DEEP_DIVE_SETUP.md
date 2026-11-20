# 📘 Deep Dive Knowledge Briefing 설정 및 벤치마크 가이드

기존 글로벌 모닝 뉴스(`feed.xml`)와 완전히 분리되어 동작하는 **학술 논문·에세이·보고서·판례 심층 브리핑 파이프라인**입니다.

---

## 1. 구독 및 열람 주소 (배포 후)
- **전용 웹 리더 (정독용 아카이브)**: `https://dndlr56.github.io/nyt-briefing/deep/`
- **AntennaPod 독립 팟캐스트 RSS**: `https://dndlr56.github.io/nyt-briefing/deep/feed.xml`

---

## 2. 요일별 자동 테마 로테이션 (매일 오전 07:30 KST, 월~일)
| 요일 | 테마 ID | 분야 | 대표 1차 소스 및 탐색 기간 (Lookback) |
| :--- | :--- | :--- | :--- |
| 월 | `ai_tech` | AI·기술·사회 | arXiv(`cs.AI`/`cs.CL`/`cs.CY`, **14일**), Nature Machine Intelligence·DeepMind·MIT Tech Review(**30일**) |
| 화 | `science_mind` | 종합과학·뇌인지 | Quanta·Nature·Science Advances·Nature Comms(**30일**), PMC 인지·학습 리뷰(**90일**) |
| 수 | `health_medicine` | 의학·건강·장수 | PMC OA 의학·수면·운동·장수·정신의학 리뷰(**90일**), Nature Medicine·eLife(**30일**) |
| 목 | `econ_geopolitics` | 경제·금융·지정학 | NBER·BIS·IMF·Brookings·RAND·CSIS(**30일**), arXiv `q-fin`/`econ`(**14일**) |
| 금 | `law_cases` | 법학·판례 입문 | SCOTUSblog·Lawfare(**30일**), Verfassungsblog(**45일**), Harvard Law Review(**60일**), arXiv AI 법(**14일**) |
| 토·일 | `weekend_special` | 주말 무제한 스페셜 | Aeon(**45일**), Quanta·Brookings·SCOTUSblog(**30일**), Foreign Affairs(**60일**), arXiv 서베이(**14일**) |

---

## 3. 단계별 모델·추론 수준 & 토큰 실측 가이드
| 단계 | 기본 모델 | 기본 `thinking_level` | 설계 의도 |
| :--- | :--- | :--- | :--- |
| 문헌 선별 | `gemini-3.5-flash-lite` | `low` | 후보 35건 메타데이터 스캔·1편 vs 2~3편 모드 판단 |
| Stage 1: 구조화 독해 노트 | `gemini-4-hydrogen` | `medium` | 원문 전체 방법론·논증·한계·비교표·챕터 설계 핵심 추론 |
| Stage 2: 챕터 본문 대본 | `gemini-4-hydrogen` | `low` | 설계도 기반 풍부한 구어체 전개 (추론 토큰 폭증 방지) |
| Stage 2: 오프닝 / 클로징 | `gemini-4-hydrogen` | `minimal` | 완성된 노트 기반 간결한 도입·마무리 |

GitHub Actions에서 **`Actions → Deep Dive Knowledge Briefing → Run workflow`**로 수동 실행하면 텔레그램 알림과 `output/deep_dive/run_summary_*.json`에 단계별 실제 토큰·비용이 기록됩니다.
- 먼저 `target_minutes=30`, `api_tier=free`, `tts_provider=edge_tts`로 실측한 뒤 분량(`20`~`60`분)을 확정해 보세요.
- 특정 논문이나 PDF 링크를 직접 해부하려면 `source_url` 입력칸에 URL을 넣으면 됩니다.

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
