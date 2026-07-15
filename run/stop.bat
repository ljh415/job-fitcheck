@echo off
cd /d "%~dp0\.."

docker compose down
echo.
echo 중지했습니다. 다시 쓰려면 run\start.bat을 실행하세요.
pause
