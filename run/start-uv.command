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

if ! command -v uv >/dev/null 2>&1; then
  read -p "uv가 설치되어 있지 않습니다. 지금 설치할까요? (Y/n, 기본 Y): " install_uv
  install_uv=${install_uv:-Y}
  if [[ "$install_uv" =~ ^[Yy] ]]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
      echo ""
      echo "설치는 됐지만 이 창에서 바로 인식이 안 됩니다. 터미널 창을 새로 열어서 이 스크립트를 다시 실행해주세요."
      read -p "엔터를 누르면 창이 닫힙니다..." _
      exit 1
    fi
  else
    echo "설치를 건너뛰었습니다. https://astral.sh/uv 안내에 따라 직접 설치한 뒤 다시 실행해주세요."
    read -p "엔터를 누르면 창이 닫힙니다..." _
    exit 1
  fi
fi

echo "Job FitCheck를 시작합니다 (Docker 없이 uv로 실행). 처음 실행할 때는 파이썬/필요한 패키지를 받느라 잠시 걸릴 수 있습니다."
echo "준비되면 브라우저에서 http://localhost:8000 을 여세요."
echo "이 창을 닫으면 앱이 꺼집니다."
echo ""
uv run --python 3.12 --with-requirements backend/requirements.txt backend/main.py
