#!/bin/bash
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo ".env 파일이 없습니다. 먼저 setup.command를 더블클릭해서 실행해주세요."
  read -p "엔터를 누르면 창이 닫힙니다..." _
  exit 1
fi

echo "Job FitCheck를 시작합니다. 준비되면 브라우저에서 http://localhost:8000 을 여세요."
echo "이 창을 닫으면 앱이 꺼집니다."
echo ""
docker compose up --build
