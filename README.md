# 🗞 NYT 모닝 브리핑 텔레그램 봇

매일 아침 **한국시간(KST) 오전 8시 30분**에 뉴욕타임즈(NYT) 주요 기사를 수집·큐레이션·번역하여 텔레그램으로 보내주는 독립형 자동화 서비스입니다.
GitHub Actions에서 실행되므로 **PC가 꺼져 있어도 매일 아침 자동으로 받아볼 수 있습니다.**

## 큐레이션 프로필
- **구성 (하루 6개 내외)**:
  - 경제·금융·테크/AI (약 50%)
  - 글로벌 시사·주요 정책 (약 30%)
  - 과학·기후·건강 (약 20%)
- **제외 주제**: 과도하게 정파적인 미국 내 정쟁(글로벌·경제 영향이 큰 정책은 포함), 스포츠·연예·셀럽 가십
- **형식**: 한국어 번역 제목 + 원문 영어 헤드라인 병기 + 3줄 핵심 요약 + 핵심 영어 표현 1~2개 + 원문 링크

---

## 5분 설정 가이드 (처음 하시는 분용)

### 1단계: 텔레그램 봇 만들기 (`TELEGRAM_BOT_TOKEN`)
1. 텔레그램 앱에서 상단 검색창에 `@BotFather`를 검색해 **파란 체크 표시가 있는 공식 BotFather** 채팅방에 들어갑니다.
2. `/newbot`을 입력해 보냅니다.
3. 봇의 **표시 이름**을 입력합니다. (예: `My NYT Briefing`)
4. 봇의 **사용자명(username)**을 입력합니다. 반드시 `bot`으로 끝나야 합니다. (예: `my_nyt_morning_bot`)
5. 완료되면 BotFather가 `Use this token to access the HTTP API:` 아래에 긴 문자열을 줍니다. 이것이 **`TELEGRAM_BOT_TOKEN`**입니다.

### 2단계: 내 채팅 ID 확인하기 (`TELEGRAM_CHAT_ID`)
1. 방금 만든 내 봇의 링크(`t.me/내봇아이디`)를 눌러 채팅방에 들어간 뒤, **`시작(Start)`** 버튼을 누르고 아무 메시지(예: `안녕`)나 한 번 보냅니다.
2. 웹 브라우저 주소창에 아래 주소를 붙여넣고 `<봇토큰>` 자리에 1단계에서 받은 토큰을 넣어 접속합니다:
   ```
   https://api.telegram.org/bot<봇토큰>/getUpdates
   ```
3. 화면에 나오는 JSON 텍스트에서 `"chat":{"id":123456789,...}` 부분을 찾습니다. 그 숫자(`123456789`)가 **`TELEGRAM_CHAT_ID`**입니다.

### 3단계: Gemini API 키 발급 (`GEMINI_API_KEY`)
1. [Google AI Studio](https://aistudio.google.com/apikey)에 Google 계정으로 로그인합니다.
2. **Create API key** 버튼을 눌러 키를 발급받습니다. 이것이 **`GEMINI_API_KEY`**입니다.

### 4단계: GitHub에 올리고 자동 실행 켜기
1. GitHub에서 새 저장소(Repository)를 만듭니다. (**Private** 추천)
2. 이 폴더의 파일들을 해당 저장소에 업로드(push)합니다.
3. GitHub 저장소 상단의 **Settings → Secrets and variables → Actions → New repository secret**에서 아래 3개를 등록합니다:
   - `GEMINI_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. 상단 **Actions** 탭으로 이동해 **NYT Morning Briefing** → **Run workflow**를 눌러 즉시 테스트합니다.
5. 1분 내외로 텔레그램에 첫 브리핑이 도착하면 성공입니다! 이후 매일 아침 8시 30분(KST)에 자동 발송됩니다.

> **참고**: GitHub Actions의 예약 실행(`cron`)은 GitHub 서버 혼잡도에 따라 지정 시각보다 약 5~15분 정도 늦게 시작될 수 있습니다.

---

## 로컬에서 미리 테스트하기 (선택)
```bash
pip install -r requirements.txt
cp .env.example .env
# .env 파일에 키 3개 입력 후

# 텔레그램 전송 없이 터미널로 결과만 미리 보기
python src/main.py --dry-run

# 실제 텔레그램으로 전송 테스트
python src/main.py
```
