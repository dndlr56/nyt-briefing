# 🎧 글로벌 모닝 & Deep Dive 오디오 브리핑

매일 아침 **글로벌 뉴스 브리핑(07:00 KST)**과 **심층 지식 브리핑(07:30 KST)**을 자동 생성해 **웹 리더**와 **AntennaPod 팟캐스트 RSS**로 발행하고, 선택적으로 **Google Drive** 및 **Telegram**까지 연동하는 개인 자동화 파이프라인입니다.

---

## 📡 구독 및 열람 주소

| 채널 | 주소 | 설명 |
| :--- | :--- | :--- |
| **통합 팟캐스트 RSS** | `https://dndlr56.github.io/nyt-briefing/feed.xml` | 글로벌 모닝 브리핑 + Deep Dive 통합 피드 (AntennaPod 등록용) |
| **Deep Dive 전용 RSS** | `https://dndlr56.github.io/nyt-briefing/deep/feed.xml` | 학술 논문·보고서·판례 심층 브리핑 단독 피드 |
| **Deep Dive 웹 리더** | `https://dndlr56.github.io/nyt-briefing/deep/` | 5대 핵심 분야 탭별 정독용 요약 노트 & 오디오 아카이브 |

---

## 1. 🌅 글로벌 모닝 브리핑 (`src/main.py`)

매일 오전 **07:00 KST** ([`.github/workflows/daily_briefing.yml`](.github/workflows/daily_briefing.yml)) 실행

- **4개 매체 균형 수집 & 전문 확보** ([`src/guardian_fetcher.py`](src/guardian_fetcher.py), [`src/rss_news_fetcher.py`](src/rss_news_fetcher.py)): **The Guardian**(Open Platform API) + **BBC · CNBC · NPR**(공식 RSS + 본문 전문 추출)에서 후보를 수집하고, 매체 간 동일 속보 및 최근 3회차 기출 URL 중복을 제거합니다.
- **2단계 큐레이션 & 1인 앵커 대본** ([`src/curator.py`](src/curator.py), [`src/script_writer.py`](src/script_writer.py)): `gemini-4-hydrogen` 모델로 빅테크·AI, 글로벌 경제·투자, 모빌리티·인프라·에너지, 의학·건강, 핵심 국제 정세 중심으로 **10~12건**을 선별하고 약 15~18분 분량의 해설 대본과 쇼노트를 작성합니다.
- **TTS 합성 & 자동 폴백** ([`src/audio_generator.py`](src/audio_generator.py)): 기본 `gemini_tts` (`gemini-3.8-flash-lite-tts`, 10종의 공식 여성 보이스 매일 자동 순환 `auto_rotate`)를 사용하며, 필요 시 `google_cloud_tts`(Chirp3-HD/Neural2) 또는 `edge_tts`(`ko-KR-HyunsuMultilingualNeural`)로 전환·폴백합니다.
- **수동 실행 모드**: `Actions → NYT Morning Briefing → Run workflow`에서 `podcast_only`, `podcast_and_telegram_text`, `telegram_text_and_audio`, `telegram_audio_only` 및 `api_tier`(`free`/`paid`), TTS 엔진·모델·보이스를 선택할 수 있습니다.

---

## 2. 📘 Deep Dive Knowledge Briefing (`src/deep_dive/main.py`)

매일 오전 **07:30 KST** ([`.github/workflows/deep_dive_briefing.yml`](.github/workflows/deep_dive_briefing.yml)) 실행

- **요일별 테마 로테이션** ([`src/deep_dive/themes.py`](src/deep_dive/themes.py)): 월(`ai_tech` AI·기술·사회) · 화(`science_mind` 종합과학·뇌인지) · 수(`health_medicine` 의학·건강·장수) · 목(`econ_geopolitics` 경제·금융·지정학) · 금(`law_cases` 대한민국 법학·판례·법조 칼럼) · 토/일(`weekend_special` 주말 스페셜 → 내용에 맞는 5대 핵심 탭에 자동 분류).
- **고품질 1차 문헌 수집** ([`src/deep_dive/sources.py`](src/deep_dive/sources.py)): OpenAlex(FWCI·상위 백분위), Semantic Scholar, Europe PMC, CORE v3, arXiv, Unpaywall OA 미러 및 대법원 판례·법률 매체를 결합하며, 수동 실행 시 `source_url` 또는 `search_title`로 특정 논문/기사/PDF를 직접 지정할 수 있습니다.
- **3단계 생성 & 무료→유료 자동 전환** ([`src/deep_dive/curator.py`](src/deep_dive/curator.py), [`src/deep_dive/writer.py`](src/deep_dive/writer.py), [`src/deep_dive/gemini_pool.py`](src/deep_dive/gemini_pool.py)):
  - 문헌 선별(`gemini-3.5-flash-lite`) → Stage 1 원문 충실 요약 노트·분류·캐스팅(`gemini-4-hydrogen`, `thinking=high`) → Stage 2 2인 대화형 장문 대본(`gemini-4-hydrogen`, `thinking=low`, 분량에 따라 25~45분 자동 조절·최대 60분).
  - `api_tier=auto` 기본값으로 `GEMINI_FREE_API_KEY`를 우선 사용하고, 일일 할당량 소진 시 실행 중간에 `GEMINI_API_KEY`(유료)로 자동 전환합니다.
- **2인 네이티브 대화형 TTS**: `gemini-3.8-flash-tts` 기반 2인 멀티스피커 합성을 기본으로 하며, Cloud TTS Chirp3-HD 병렬 합성 및 `edge_tts` 폴백을 지원합니다.
- **Google Drive 아카이빙 *(선택)*** ([`src/deep_dive/drive_archiver.py`](src/deep_dive/drive_archiver.py)): `Deep-Dive-Briefing/<5대 핵심 분야>/[YYYY-MM-DD] <제목>/` 폴더에 알렉산드리아 호환 요약 노트(Markdown), 대화 대본(Markdown), 원문 PDF·`full_text.txt`를 저장하고 `Master_Index` Google Sheet를 자동 업데이트합니다.

> 자세한 설정과 벤치마크 가이드는 [`docs/DEEP_DIVE_SETUP.md`](docs/DEEP_DIVE_SETUP.md)를 참고하세요.

---

## 🔑 필요한 GitHub Secrets

`Settings → Secrets and variables → Actions`에 등록합니다.

### 필수
- `GEMINI_API_KEY`: Gemini API 키 (유료 티어 또는 기본 키)
- `GUARDIAN_API_KEY`: The Guardian Open Platform 무료 API 키

### 선택 (권장 및 확장 기능)
- `GEMINI_FREE_API_KEY`: 무료 티어 Gemini API 키 (등록 시 무료 우선 사용 후 한도 초과 시 `GEMINI_API_KEY`로 자동 폴백)
- `GOOGLE_CLOUD_TTS_CREDENTIALS_JSON`: Google Cloud TTS 서비스 계정 JSON (폴백 또는 Chirp3-HD 사용 시)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: 워크플로우 실패 알림 및 텔레그램 브리핑 전송용
- `PODCAST_OWNER_EMAIL`: RSS `<itunes:owner>` 이메일 메타데이터용
- `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET`, `GOOGLE_DRIVE_REFRESH_TOKEN`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`: Deep Dive Google Drive / Sheets 아카이빙용 ([`scripts/get_drive_refresh_token.py`](scripts/get_drive_refresh_token.py)로 발급)
- `SEMANTIC_SCHOLAR_API_KEY`, `CORE_API_KEY`: Deep Dive 학술 DB 전용 한도 및 Green OA 전문 회수 보강용

---

## 🛠️ 로컬 실행 및 테스트

```bash
# Python 3.12+ 및 ffmpeg 필요
pip install -r requirements.txt
cp .env.example .env  # API 키 입력

# 모닝 브리핑 실행 예시
python src/main.py --with-audio --output-dir output

# Deep Dive 브리핑 실행 예시
python src/deep_dive/main.py --theme auto --output-dir output/deep_dive

# 테스트 실행
pytest tests/test_deep_dive.py
```
