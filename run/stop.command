#!/bin/bash
cd "$(dirname "$0")/.."

docker compose down
echo ""
echo "중지했습니다. 다시 쓰려면 run/start.command를 실행하세요."
read -p "엔터를 누르면 창이 닫힙니다..." _
