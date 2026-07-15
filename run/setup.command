#!/bin/bash
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  echo ".env 파일이 이미 있습니다. 새로 설정하려면 .env 파일을 지우고 다시 실행해주세요."
else
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
  echo "설정 완료! 이제 run/start.command를 더블클릭해서 실행하세요."
fi

echo ""
read -p "엔터를 누르면 창이 닫힙니다..." _
