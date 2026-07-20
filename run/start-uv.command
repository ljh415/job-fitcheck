#!/bin/bash
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "uv가 설치되어 있지 않습니다. 아래 명령어를 터미널에 입력해 설치한 뒤 다시 실행해주세요:"
  echo ""
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo ""
  read -p "엔터를 누르면 창이 닫힙니다..." _
  exit 1
fi

if [ ! -f .env ]; then
  echo ".env 파일이 없습니다. 먼저 run/setup.command를 더블클릭해서 실행해주세요."
  read -p "엔터를 누르면 창이 닫힙니다..." _
  exit 1
fi

echo "Job FitCheck를 시작합니다 (Docker 없이 uv로 실행). 처음 실행할 때는 파이썬/필요한 패키지를 받느라 잠시 걸릴 수 있습니다."
echo "준비되면 브라우저에서 http://localhost:8000 을 여세요."
echo "이 창을 닫으면 앱이 꺼집니다."
echo ""
uv run --python 3.12 --with-requirements backend/requirements.txt backend/main.py
