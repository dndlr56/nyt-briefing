# 📘 Deep Dive Knowledge Briefing 설정 및 벤치마크 가이드

기존 글로벌 모닝 뉴스(`feed.xml`)와 완전히 분리되어 동작하는 **학술 논문·에세이·보고서·판례 심층 브리핑 파이프라인**입니다.

---

## 1. 구독 및 열람 주소 (배포 후)
- **전용 웹 리더 (정독용 아카이브)**: `https://dndlr56.github.io/nyt-briefing/deep/`
- **AntennaPod 독립 팟캐스트 RSS**: `https://dndlr56.github.io/nyt-briefing/deep/feed.xml`

---

## 2. 요일별 자동 테마 로테이션 (매일 오전 07:30 KST, 월~일)
최신 단신보다 **학계·실무에서 검증된 고퀄리티 원전**을 우선하기 위해 다중 품질 지표(**OpenAlex FWCI·분야별 상위 1~10%·철회 논문 차단**, **Semantic Scholar 실질 인용(`influentialCitationCount`)**, **Europe PMC 고인용 OA 리뷰**, **CORE v3 기관 리포지토리 전문 백업**)를 결합합니다.

| 요일 | 테마 ID | 분야 | 고퀄리티 1차 소스 및 탐색 기간 |
| :--- | :--- | :--- | :--- |
| 월 | `ai_tech` | AI·기술·사회 | OpenAlex FWCI/상위권(**2년**), Semantic Scholar 실질인용(**2년**), arXiv 관련도순(**6~9개월**), Nature MI·DeepMind·MIT Tech Review |
| 화 | `science_mind` | 종합과학·뇌인지 | OpenAlex FWCI 리뷰(**4년**), Europe PMC 고인용 OA(**4년**), Semantic Scholar 인지심리(**4년**), Quanta·Nature·Science Advances |
| 수 | `health_medicine` | 의학·건강·장수 | Europe PMC 고인용 리뷰·메타분석(**4년**), OpenAlex FWCI 리뷰(**4년**), Nature Medicine·eLife |
| 목 | `econ_geopolitics` | 경제·금융·지정학 | OpenAlex 행동재무·거시·지정학(**3~4년**), Semantic Scholar 경제(**4년**), NBER·BIS·IMF·Brookings·RAND·CSIS, arXiv(`q-fin`/`econ`) |
| 금 | `law_cases` | 대한민국 법학·판례·법조 칼럼 | 대한민국 대법원 판례속보, 법률신문(판결·오피니언), 대한변협 법조신문, 법률저널(로스쿨·연재), OpenAlex 한국 법학 OA 논문(최근 5년) |
| 토·일 | `weekend_special` | 주말 무제한 스페셜 | OpenAlex 교차분야 랜드마크 리뷰(**5년**), Semantic Scholar 명작(**5년**), Aeon·Quanta·Foreign Affairs·Just Security·Brookings |

> **선택 API 키 (미등록해도 정상 동작)**: `SEMANTIC_SCHOLAR_API_KEY`(무료 1 RPS 전용 한도) 및 `CORE_API_KEY`([core.ac.uk](https://core.ac.uk/services/api) 무료 키 — 출판사 PDF가 막힐 때 대학 리포지토리 Green OA 전문 자동 회수)를 GitHub Secrets에 추가하면 탐색·전문 회수 안정성이 더욱 높아집니다.

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
