@echo off
cd /d "%~dp0"

if exist .env (
  echo .env 파일이 이미 있습니다. 새로 설정하려면 .env 파일을 지우고 다시 실행해주세요.
) else (
  copy .env.example .env >nul
  echo === Job FitCheck 초기 설정 ===
  echo.
  set /p gemini_key=Gemini API 키(AIza... 로 시작)를 붙여넣고 Enter:
  set /p claude_key=Claude API 키(sk-ant-... 로 시작, 없으면 그냥 Enter):
  set /p app_secret=로그인 비밀번호로 쓸 값을 정해서 입력하고 Enter:

  powershell -NoProfile -Command "(Get-Content .env) -replace 'GOOGLE_API_KEY=.*', 'GOOGLE_API_KEY=%gemini_key%' | Set-Content -Encoding UTF8 .env"
  powershell -NoProfile -Command "(Get-Content .env) -replace 'APP_SECRET=.*', 'APP_SECRET=%app_secret%' | Set-Content -Encoding UTF8 .env"
  if not "%claude_key%"=="" (
    powershell -NoProfile -Command "(Get-Content .env) -replace 'ANTHROPIC_API_KEY=.*', 'ANTHROPIC_API_KEY=%claude_key%' | Set-Content -Encoding UTF8 .env"
  )

  echo.
  echo 설정 완료! 이제 start.bat을 더블클릭해서 실행하세요.
)

echo.
pause
