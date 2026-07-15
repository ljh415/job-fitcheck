# 처음 시작하기 (완전 초보자용 가이드)

프로그래밍을 몰라도 따라 할 수 있도록 순서대로 설명합니다. 총 15~20분 정도 걸립니다.

컴퓨터를 새로 사거나 재설정할 일은 없고, **Docker Desktop** 하나만 새로 설치하면 됩니다.

<!-- 이 문서에 이미지를 추가할 때 참고할 캡처 목록입니다. 아래 파일명 그대로 assets/guide/ 폴더에 넣으면 각 단계에 자동으로 표시됩니다. -->
> **📸 필요한 스크린샷 목록** (캡처해서 `assets/guide/` 폴더에 아래 파일명으로 넣어주세요)
> 1. `01-docker-download.png` — Docker 공식 다운로드 페이지에서 Mac/Windows 버전 선택 화면
> 2. `02-docker-running.png` — Docker Desktop 실행 후 "Engine running" 표시 화면
> 3. `03-github-download-zip.png` — GitHub 저장소의 `Code` → `Download ZIP` 버튼 위치
> 4. `04-gemini-api-key.png` — Google AI Studio에서 API 키 생성된 화면
> 5. `05-anthropic-signup.png` (선택) — Anthropic Console 가입/로그인 화면
> 6. `06-anthropic-api-key.png` (선택) — API Keys 메뉴에서 `Create Key` 클릭 후 키 생성된 화면
> 7. `07-setup-script.png` — `setup.command`/`setup.bat` 더블클릭 후 API 키를 입력받는 터미널 창
> 8. `08-start-script.png` — `start.command`/`start.bat` 실행 후 로그가 흐르는 터미널 창
> 9. `09-login-screen.png` — 브라우저에서 `http://localhost:8000` 접속 후 나오는 로그인 화면

---

## 1단계. Docker Desktop 설치

이 앱은 "Docker"라는 프로그램 위에서 실행됩니다. Docker는 컴퓨터 환경 차이(윈도우냐 맥이냐, 파이썬 버전이 뭐냐 등)를 신경 쓰지 않고 앱을 실행할 수 있게 해주는 도구입니다.

1. [https://www.docker.com/products/docker-desktop/](https://www.docker.com/products/docker-desktop/) 접속
2. 본인 컴퓨터에 맞는 버전 다운로드
   - Mac: "Mac with Apple Silicon"(M1/M2/M3/M4 칩) 또는 "Mac with Intel Chip" — Apple 메뉴 → 이 Mac에 관하여에서 확인 가능
   - Windows: "Windows" 버튼 클릭

   ![Docker 다운로드 페이지](assets/guide/01-docker-download.png)

3. 다운로드된 설치 파일 실행 → 안내대로 계속 진행 (Windows에서 "WSL2를 설치해야 합니다" 같은 안내가 나오면 그냥 안내를 따라 진행하고 재시작하면 됩니다)
4. 설치 중 **Docker 계정으로 로그인하라는 화면**이 나올 수 있습니다. 이메일로 무료 가입하면 되고, 화면에 "로그인 없이 계속하기(Continue without signing in)" 같은 건너뛰기 옵션이 보이면 그걸 눌러도 됩니다 — 둘 다 이후 사용에는 지장 없습니다.
5. 설치가 끝나면 **Docker Desktop 앱을 직접 실행**하세요 (Mac: Launchpad나 Applications 폴더에서 "Docker" 클릭 / Windows: 시작 메뉴에서 "Docker Desktop" 클릭)
6. Docker Desktop 창이 열리고 잠시 기다리면, 창 안에 초록색 점과 함께 **"Engine running"**이라는 문구가 보입니다. 이게 보이면 준비 완료입니다.

   ![Docker Desktop Engine running 화면](assets/guide/02-docker-running.png)

> Docker Desktop 창을 닫아도 실행 상태는 유지됩니다. 다만 "Engine running"이 안 보이거나 빨간색으로 표시돼 있으면 이후 단계가 전부 실패하니, 이 화면을 다시 열어 확인하세요.

---

## 2단계. 프로젝트 다운로드

GitHub 저장소 페이지에서:

1. 초록색 **`Code`** 버튼 클릭
2. **`Download ZIP`** 클릭

   ![GitHub Download ZIP 버튼](assets/guide/03-github-download-zip.png)

3. 다운로드된 zip 파일의 압축을 풀기 (더블클릭하면 자동으로 풀리는 경우가 많습니다)
4. 압축 푼 폴더를 원하는 위치(예: 바탕화면)로 옮겨두기

> `git`을 이미 써보셨다면 `git clone` 명령어로 받으셔도 됩니다. 모르면 위 방법으로 충분합니다.

---

## 3단계. AI API 키 발급받기

이 앱은 AI로 채용공고를 분석합니다. 기본값은 **Google Gemini**로 설정돼 있는데, 무료로 바로 발급받아 쓸 수 있어서 처음 시작할 때 제일 간편합니다.

### Gemini API 키 발급 (기본, 무료)

1. [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey) 접속 → 구글 계정으로 로그인
2. **Create API Key** 클릭
3. 생성된 키(`AIza`로 시작하는 문자열)를 복사

   ![Google AI Studio API 키 생성 화면](assets/guide/04-gemini-api-key.png)

> 무료 티어는 모델당 하루 20회 호출 제한이 있습니다(README의 "Gemini 무료 티어" 참고). 가볍게 체험만 해보실 거면 이 키 하나로 충분하니 4단계로 넘어가셔도 됩니다.

### (추천) Claude API 키도 함께 준비하기

Gemini보다 분석 품질이 더 정확한 편입니다. 나중에 설정 화면에서 Gemini → Claude로 언제든 전환할 수 있으니, 여유가 되면 같이 발급받아두는 걸 추천합니다. 사용한 만큼 비용이 청구됩니다 (공고 1건 분석에 대략 **$0.1 내외** — [README의 사용 비용 추정치](README.md#사용-비용-추정치) 참고).

1. [https://console.anthropic.com](https://console.anthropic.com) 접속 후 회원가입/로그인

   ![Anthropic Console 로그인 화면](assets/guide/05-anthropic-signup.png)

2. 왼쪽 메뉴에서 **Billing**(결제) 들어가서 결제 수단(카드) 등록 및 최소 금액 충전 (보통 $5부터 가능)
3. 왼쪽 메뉴에서 **API Keys** 클릭 → **Create Key** 클릭

   ![API 키 생성 화면](assets/guide/06-anthropic-api-key.png)

4. 생성된 키(`sk-ant-`로 시작하는 긴 문자열)를 복사 — **이 화면을 벗어나면 다시 볼 수 없으니** 메모장 등에 잠깐 붙여두세요

---

## 4단계. 초기 설정

2단계에서 압축을 푼 폴더를 열어보면 `setup.command`(Mac) 또는 `setup.bat`(Windows) 파일이 있습니다. 이걸 **더블클릭**하세요.

- 검은 화면(터미널)이 뜨면서 순서대로 물어봅니다.
- **Gemini API 키**: 3단계에서 복사해둔 키를 붙여넣고 Enter
- **Claude API 키**: 준비했다면 붙여넣고 Enter, 없으면 그냥 Enter(비워두고 넘어가기)
- **로그인 비밀번호**: 원하는 값을 입력하고 Enter

  ![setup 스크립트 실행 화면](assets/guide/07-setup-script.png)

"설정 완료!"라는 문구가 보이면 성공입니다. 이 창은 닫아도 됩니다.

> **Mac에서 "확인되지 않은 개발자" 경고가 뜨면?** 파일을 마우스 오른쪽 클릭 → "열기" 선택 → 다시 한번 "열기"를 누르면 실행됩니다 (한 번만 해주면 됩니다).

---

## 5단계. 앱 실행

같은 폴더에서 이번엔 `start.command`(Mac) 또는 `start.bat`(Windows)을 **더블클릭**하세요.

검은 화면에 로그가 계속 흘러가다가 멈추는 듯한 상태가 되면(예: `Uvicorn running on...` 같은 문구가 보이면) 준비된 겁니다. 처음 실행할 때는 필요한 파일들을 받느라 2~5분 정도 걸릴 수 있습니다.

![start 스크립트 실행 화면](assets/guide/08-start-script.png)

> **이 창은 닫지 말고 그대로 켜두세요** — 창을 닫으면 앱도 같이 꺼집니다.

---

## 6단계. 접속 및 로그인

1. 브라우저(크롬 등)를 열고 주소창에 `http://localhost:8000` 입력
2. 4단계에서 정한 비밀번호 입력 → 로그인

   ![로그인 화면](assets/guide/09-login-screen.png)

---

## 7단계. 첫 사용

1. **설정** 탭 → 이력서/포트폴리오 PDF 업로드 → 프로필 자동 생성
2. **+ 회사 추가** → 채용공고 URL 붙여넣기 또는 텍스트/이미지 입력 → AI 분석
3. **대시보드**에서 결과 확인, Q&A로 질문도 가능

---

## 앱을 끄고/다시 켜고 싶을 때

- **끄기**: 5단계에서 실행해둔 창에서 `Control + C`를 누르거나, `stop.command`(Mac)/`stop.bat`(Windows)을 더블클릭
- **다시 켜기**: `start.command`/`start.bat`을 다시 더블클릭 (설정 파일과 데이터는 그대로 남아있습니다)

---

## 터미널로 직접 하고 싶다면 (선택)

스크립트 없이 터미널 명령어로 직접 하고 싶은 분들을 위한 방법입니다. 위 4~5단계 대신 아래 순서를 따르면 됩니다.

1. 터미널 열기 — Mac: `Command(⌘) + Space` → "터미널" 입력 → Enter / Windows: 시작 메뉴 → "PowerShell" 검색 → Enter
2. 2단계에서 압축을 푼 폴더로 이동 — 터미널에 `cd ` 라고 입력(마지막에 띄어쓰기 한 칸)한 뒤, 폴더를 마우스로 끌어서 터미널 창 위에 놓으면 경로가 자동으로 채워집니다 → Enter
3. 설정 파일 만들기: `cp .env.example .env` 입력 후 Enter
4. 파일 열어서 값 채우기 — Mac: `open -e .env` / Windows: `notepad .env` 입력 후, 열린 파일에서 `GOOGLE_API_KEY=`와 `APP_SECRET=` 뒤에 값을 채우고(Claude 키가 있으면 `ANTHROPIC_API_KEY=`도) 저장
5. 실행: `docker compose up --build` 입력 후 Enter
6. 끄기: 같은 창에서 `Control + C`, 또는 새 터미널에서 같은 폴더로 이동해 `docker compose down`

---

## 문제가 생겼을 때

| 증상 | 원인/해결 |
|---|---|
| `docker: command not found` 또는 비슷한 오류 | Docker Desktop이 실행 중이 아닙니다. 아이콘을 찾아 실행한 뒤 다시 시도 |
| `port is already allocated` 같은 오류 | 8000번 포트를 다른 프로그램이 쓰고 있습니다. 컴퓨터를 재시작하거나, 다른 프로그램을 종료 후 재시도 |
| 로그인이 안 됨 | 4단계에서 정한 비밀번호를 정확히 입력했는지 확인 (대소문자 구분) |
| 공고 분석 시 "인증" 관련 오류 | API 키가 정확히 입력됐는지 확인 — 다시 설정하려면 `.env` 파일을 지우고 `setup.command`/`setup.bat`을 다시 실행 |
| 그 외 궁금한 점 | [README.md](README.md)에 기능별 설명이 더 자세히 있습니다 |
