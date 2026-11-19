# 🎧 글로벌 모닝 오디오 브리핑 (Podcast RSS)

매일 아침 **The Guardian(기사 전문, 메인)**과 **NYT RSS(미국·글로벌 보완)**에서 핵심 글로벌 뉴스 **10~12건**을 선별해 **약 15~18분 분량의 한국어 오디오 브리핑 + 읽기용 쇼노트**를 생성하고, **AntennaPod** 등 팟캐스트 앱으로 자동 수신할 수 있게 RSS 피드(`feed.xml`)를 발행하는 개인 자동화 파이프라인입니다.

## 주요 기능
- **2단계 기사 큐레이션** ([`src/curator.py`](src/curator.py)): 빅테크·AI, 글로벌 경제·투자·기업 실적, 모빌리티·인프라·에너지, 의학·건강 돌파구 중심 선별 (영국·유럽·미국 내부 지엽적 정쟁 및 로컬 연예·가십 제외)
- **기사 전문(Full Text) 기반 해설 대본** ([`src/guardian_fetcher.py`](src/guardian_fetcher.py), [`src/script_writer.py`](src/script_writer.py)): The Guardian Open Platform API로 선별 기사 전문을 읽어 비전공자도 귀로 바로 이해할 수 있는 1인 앵커 해설 대본 작성
- **무료 TTS + 자동 안전망** ([`src/audio_generator.py`](src/audio_generator.py)): 기본 Microsoft Edge TTS(`ko-KR-HyunsuMultilingualNeural`, 무료)로 합성하며, 일시적 서버 장애 시 Google Cloud TTS 무료 티어로 자동 폴백
- **팟캐스트 RSS 자동 발행** ([`src/podcast_feed.py`](src/podcast_feed.py)): GitHub Actions가 매일 `gh-pages` 브랜치에 MP3(96kbps)와 `feed.xml`(기사 요약·원문 링크 쇼노트 포함)을 자동 배포 (최근 30회차 유지)

## AntennaPod 구독 주소
```text
https://dndlr56.github.io/nyt-briefing/feed.xml
```

## 필요한 GitHub Secrets
- `GEMINI_API_KEY`: 기사 선별·요약·대본 작성용 Gemini API 키
- `GUARDIAN_API_KEY`: The Guardian Open Platform 무료 API 키
- `GOOGLE_CLOUD_TTS_CREDENTIALS_JSON` *(선택)*: Edge TTS 장애 시 자동 폴백용 Google Cloud 서비스 계정 JSON
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` *(선택)*: 수동 실행으로 텔레그램 전송 모드를 쓸 때만 사용

## 실행 방식
- **자동 예약**: GitHub Actions ([`.github/workflows/daily_briefing.yml`](.github/workflows/daily_briefing.yml))가 매일 지정 시각에 `podcast_only` 모드로 자동 실행
- **수동 실행**: `Actions → NYT Morning Briefing → Run workflow`에서 모드(`podcast_only`, `podcast_and_telegram_text`, `telegram_text_and_audio`, `telegram_audio_only`) 및 TTS 엔진/목소리 선택 가능
