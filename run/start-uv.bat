@echo off
chcp 65001 >nul
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

where uv >nul 2>nul
if errorlevel 1 (
  set "install_uv="
  set /p install_uv="uv가 설치되어 있지 않습니다. 지금 설치할까요? (Y/n, 기본 Y): "
  if "!install_uv!"=="" set "install_uv=Y"
  if /i "!install_uv:~0,1!"=="Y" (
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    where uv >nul 2>nul
    if errorlevel 1 (
      echo.
      echo 설치는 됐지만 이 창에서 바로 인식이 안 됩니다. 터미널 창을 새로 열어서 이 스크립트를 다시 실행해주세요.
      pause
      exit /b 1
    )
  ) else (
    echo 설치를 건너뛰었습니다. https://astral.sh/uv 안내에 따라 직접 설치한 뒤 다시 실행해주세요.
    pause
    exit /b 1
  )
)

echo Job FitCheck를 시작합니다 (Docker 없이 uv로 실행). 처음 실행할 때는 파이썬/필요한 패키지를 받느라 잠시 걸릴 수 있습니다.
echo 준비되면 브라우저에서 http://localhost:8000 을 여세요.
echo 이 창을 닫으면 앱이 꺼집니다.
echo.
uv run --python 3.12 --with-requirements backend/requirements.txt backend/main.py

echo.
echo 서버가 종료됐습니다. 위에 오류 메시지가 있다면 확인해주세요.
pause
