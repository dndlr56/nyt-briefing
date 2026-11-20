# 📘 Deep Dive Knowledge Briefing 설정 및 벤치마크 가이드

기존 글로벌 모닝 뉴스(`feed.xml`)와 완전히 분리되어 동작하는 **학술 논문·에세이·보고서·판례 심층 브리핑 파이프라인**입니다.

---

## 1. 구독 및 열람 주소 (배포 후)
- **전용 웹 리더 (정독용 아카이브)**: `https://dndlr56.github.io/nyt-briefing/deep/`
- **AntennaPod 독립 팟캐스트 RSS**: `https://dndlr56.github.io/nyt-briefing/deep/feed.xml`

---

## 2. 요일별 자동 테마 로테이션 (매일 오전 07:30 KST, 월~일)
최신 단신보다 **학계에서 검증·고인용된 명작 리뷰·논문·판례**를 우선하기 위해 OpenAlex(피인용 순) + PMC OA + arXiv + 주요 RSS를 주제별 맞춤 기간으로 결합합니다.

| 요일 | 테마 ID | 분야 | 고인용·권위 1차 소스 및 탐색 기간 |
| :--- | :--- | :--- | :--- |
| 월 | `ai_tech` | AI·기술·사회 | OpenAlex 고인용 OA(**최근 2년**, 인용 25~40+회), arXiv 관련도순(**6~9개월**), Nature MI·DeepMind·MIT Tech Review(**6개월**) |
| 화 | `science_mind` | 종합과학·뇌인지 | OpenAlex 고인용 리뷰(**최근 4년**, 인용 50+회), PMC OA 리뷰(**최근 3년**), Quanta·Nature·Science Advances(**6개월**) |
| 수 | `health_medicine` | 의학·건강·장수 | OpenAlex 고인용 의학 리뷰(**최근 4년**, 인용 50~60+회), PMC OA 리뷰·메타분석(**최근 3년**), Nature Medicine·eLife(**6개월**) |
| 목 | `econ_geopolitics` | 경제·금융·지정학 | OpenAlex 행동재무·거시·지정학(**최근 3~4년**, 인용 30~40+회), NBER·BIS·IMF·Brookings·RAND·CSIS(**6개월**), arXiv(`q-fin`/`econ`, **1년**) |
| 금 | `law_cases` | 법학·판례 입문 | OpenAlex AI법·헌법·법경제학(**최근 3~5년**, 인용 25~30+회), Harvard Law Review(**1년**), SCOTUSblog·Lawfare·Verfassungsblog(**6개월**) |
| 토·일 | `weekend_special` | 주말 무제한 스페셜 | OpenAlex 교차분야 랜드마크 리뷰(**최근 5년**, 인용 80+회), Aeon·Foreign Affairs(**1년**), Quanta·Brookings·SCOTUSblog(**6개월**) |

---

## 3. 단계별 모델·추론 수준 & 토큰 실측 가이드
> `gemini-4-hydrogen`의 공식 지원 추론 레벨은 `low`, `medium`, `high`(기본), `xhigh`입니다 (`minimal` 미지원 — 자동 `low` 보정 내장).

| 단계 | 기본 모델 | 기본 `thinking_level` | 설계 의도 |
| :--- | :--- | :--- | :--- |
| 문헌 선별 | `gemini-3.5-flash-lite` | `low` | 후보 35건(피인용 수·권위 포함) 스캔 및 1편 vs 2~3편 모드 판단 |
| Stage 1: 구조화 독해 노트 | `gemini-4-hydrogen` | `high` | 원문 방법론·논증·한계·비교표·챕터 설계 핵심 추론 (1회 집중 투입) |
| Stage 2: 챕터 본문 대본 | `gemini-4-hydrogen` | `low` | 완성된 설계도 기반 풍부한 구어체 전개 (챕터별 추론 토큰 폭증 방지) |
| Stage 2: 오프닝 / 클로징 | `gemini-4-hydrogen` | `low` | Hydrogen 최저 추론 레벨로 간결하고 빠르게 도입·마무리 작성 |

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
