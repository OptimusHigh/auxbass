#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  TG Player - Pterodactyl Startup & Auto-Update Script
# ═══════════════════════════════════════════════════════════════════

echo "========================================================"
echo "          🎵 TG Player - Pterodactyl Starter            "
echo "========================================================"

# Disable interactive git prompts to prevent server hang
export GIT_TERMINAL_PROMPT=0

# Disable compiled C-extensions that cause SIGSEGV on container Python 3.11/glibc
export PROPCACHE_NO_EXTENSIONS=1
export YARL_NO_EXTENSIONS=1
export MULTIDICT_NO_EXTENSIONS=1
export FROZENLIST_NO_EXTENSIONS=1
export AIOHTTP_NO_EXTENSIONS=1
# Load and export proxy if configured
if [ -f ".env" ]; then
    PROXY_VAL=$(grep -E '^(PROXY_URL|HTTP_PROXY|HTTPS_PROXY)=' .env | head -n 1 | cut -d '=' -f2- | tr -d '"' | tr -d "'")
    if [ -n "$PROXY_VAL" ]; then
        export HTTP_PROXY="$PROXY_VAL"
        export HTTPS_PROXY="$PROXY_VAL"
        export http_proxy="$PROXY_VAL"
        export https_proxy="$PROXY_VAL"
        export NO_PROXY="localhost,127.0.0.1,192.168.0.0/16,172.16.0.0/12,10.0.0.0/8,882e5ebb-62ad-4b74-9724-e490d1f030b6"
        export no_proxy="$NO_PROXY"
        echo "🌐 Using Proxy from .env: ${PROXY_VAL##*@}"
    fi
fi

# Ensure temp directory uses container persistent storage (avoids 100MB tmpfs limit)
mkdir -p /home/container/tmp
export TMPDIR=/home/container/tmp
export TEMP=/home/container/tmp
export TMP=/home/container/tmp


# Allow git operations in Docker container directory
git config --global --add safe.directory "*" 2>/dev/null || true
git config --global init.defaultBranch main 2>/dev/null || true

# 1. Run updater
if [ -f "update.sh" ]; then
    bash update.sh
fi

# 2. Launch Application (FastAPI + WebApp Static + Telegram Bot)
echo "🚀 Запуск TG Player (start.py)..."
exec python start.py

