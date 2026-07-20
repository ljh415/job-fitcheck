#!/bin/bash
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "=== Job FitCheck 초기 설정 ==="
  echo ""
  read -p "Gemini API 키(AIza... 로 시작)를 붙여넣고 Enter: " gemini_key
  read -p "Claude API 키(sk-ant-... 로 시작, 없으면 그냥 Enter): " claude_key
  read -p "로그인 비밀번호로 쓸 값을 정해서 입력하고 Enter: " app_secret

  if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s|GOOGLE_API_KEY=.*|GOOGLE_API_KEY=$gemini_key|" .env
    sed -i '' "s|APP_SECRET=.*|APP_SECRET=$app_secret|" .env
    if [ -n "$claude_key" ]; then
      sed -i '' "s|ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$claude_key|" .env
    fi
  else
    sed -i "s|GOOGLE_API_KEY=.*|GOOGLE_API_KEY=$gemini_key|" .env
    sed -i "s|APP_SECRET=.*|APP_SECRET=$app_secret|" .env
    if [ -n "$claude_key" ]; then
      sed -i "s|ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$claude_key|" .env
    fi
  fi

  echo ""
  echo "설정 완료!"
  echo ""
fi

echo "Job FitCheck를 시작합니다 (Docker). 준비되면 브라우저에서 http://localhost:8000 을 여세요."
echo "이 창을 닫으면 앱이 꺼집니다."
echo ""
docker compose up --build
