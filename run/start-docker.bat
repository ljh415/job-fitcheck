@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

if not exist .env (
  copy .env.example .env >nul
  echo === Job FitCheck 초기 설정 ===
  echo.
  set "gemini_key="
  set "claude_key="
  set "app_secret="
  set /p gemini_key="Gemini API 키(AIza... 로 시작)를 붙여넣고 Enter: "
  set /p claude_key="Claude API 키(sk-ant-... 로 시작, 없으면 그냥 Enter): "
  set /p app_secret="로그인 비밀번호로 쓸 값을 정해서 입력하고 Enter: "

  powershell -NoProfile -Command "(Get-Content .env) -replace 'GOOGLE_API_KEY=.*', 'GOOGLE_API_KEY=!gemini_key!' | Set-Content -Encoding UTF8 .env"
  powershell -NoProfile -Command "(Get-Content .env) -replace 'APP_SECRET=.*', 'APP_SECRET=!app_secret!' | Set-Content -Encoding UTF8 .env"
  if not "!claude_key!"=="" (
    powershell -NoProfile -Command "(Get-Content .env) -replace 'ANTHROPIC_API_KEY=.*', 'ANTHROPIC_API_KEY=!claude_key!' | Set-Content -Encoding UTF8 .env"
  )

  echo.
  echo 설정 완료!
  echo.
)

echo Job FitCheck를 시작합니다 (Docker). 준비되면 브라우저에서 http://localhost:8000 을 여세요.
echo 이 창을 닫으면 앱이 꺼집니다.
echo.
docker compose up --build
