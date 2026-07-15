@echo off
cd /d "%~dp0"

if not exist .env (
  echo .env 파일이 없습니다. 먼저 setup.bat을 더블클릭해서 실행해주세요.
  pause
  exit /b 1
)

echo Job FitCheck를 시작합니다. 준비되면 브라우저에서 http://localhost:8000 을 여세요.
echo 이 창을 닫으면 앱이 꺼집니다.
echo.
docker compose up --build
