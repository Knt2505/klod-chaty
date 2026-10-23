#!/usr/bin/env bash
# Встановлення Клода і Чаті на чистий Ubuntu-сервер однією командою:
#   curl -fsSL https://raw.githubusercontent.com/OWNER/klod-chaty/main/setup.sh | bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/OWNER/klod-chaty.git}"
DIR="${DIR:-$HOME/klod-chaty}"

ask() {  # ask <змінна> <питання> [secret]
  local value=""
  while [ -z "$value" ]; do
    if [ "${3:-}" = "secret" ]; then
      read -rsp "$2: " value < /dev/tty; echo
    else
      read -rp "$2: " value < /dev/tty
    fi
  done
  printf -v "$1" '%s' "$value"
}

echo "== Клод і Чаті: встановлення =="

if ! command -v git >/dev/null; then
  apt-get update -qq && apt-get install -y -qq git
fi
if ! command -v docker >/dev/null; then
  echo "Встановлюю Docker..."
  curl -fsSL https://get.docker.com | sh
fi

if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only
else
  git clone "$REPO_URL" "$DIR"
fi
cd "$DIR"

if [ ! -f .env ]; then
  echo "Встав ключі (секретні не показуються під час введення):"
  ask KLOD_BOT_TOKEN "Токен бота Клода (від @BotFather)" secret
  ask CHATY_BOT_TOKEN "Токен бота Чаті (від @BotFather)" secret
  ask ANTHROPIC_API_KEY "Anthropic API key" secret
  ask OPENAI_API_KEY "OpenAI API key" secret
  umask 077
  cat > .env <<ENV
KLOD_BOT_TOKEN=$KLOD_BOT_TOKEN
CHATY_BOT_TOKEN=$CHATY_BOT_TOKEN
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
OPENAI_API_KEY=$OPENAI_API_KEY
ENV
fi

docker compose up -d --build

echo
echo "Готово! Бот запущений."
echo "Тепер у своїй групі з Клодом і Чаті напиши: /bind"
echo "Логи: cd $DIR && docker compose logs -f --tail 50"
