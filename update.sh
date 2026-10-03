#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  TG Player - Pterodactyl Updater (Startup Command 1)
# ═══════════════════════════════════════════════════════════════════

set -e

echo "========================================================"
echo "          🔄 TG Player - Auto Updater                   "
echo "========================================================"

# Disable interactive git prompts
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
    fi
fi


# Allow git operations in Docker container directory
git config --global --add safe.directory "*" 2>/dev/null || true
git config --global init.defaultBranch main 2>/dev/null || true

# 1. Update repository from Git
DEFAULT_REPO="https://github.com/OptimusHigh/auxbass.git"

if [ -n "$GITHUB_TOKEN" ]; then
    REPO_URL="https://${GITHUB_TOKEN}@github.com/OptimusHigh/auxbass.git"
elif [ -n "$GIT_TOKEN" ]; then
    REPO_URL="https://${GIT_TOKEN}@github.com/OptimusHigh/auxbass.git"
else
    REPO_URL="${GIT_ADDRESS:-$DEFAULT_REPO}"
fi

echo "📦 [1/3] Обновление кода из Git..."
if [ ! -d ".git" ]; then
    git init 2>/dev/null || true
fi

git remote add origin "$REPO_URL" 2>/dev/null || git remote set-url origin "$REPO_URL" 2>/dev/null || true

if git fetch origin main --depth=1 2>/dev/null || git fetch origin 2>/dev/null; then
    BRANCH="main"
    if git show-ref --verify --quiet refs/remotes/origin/main; then
        BRANCH="main"
    elif git show-ref --verify --quiet refs/remotes/origin/master; then
        BRANCH="master"
    fi
    git checkout -B "$BRANCH" "origin/$BRANCH" 2>/dev/null || git reset --hard "origin/$BRANCH" 2>/dev/null || true
    echo "✅ Исходный код обновлен!"
else
    echo "⚠️ Не удалось обновить через Git (нет сети или доступа), используем текущие файлы."
fi

# 2. Update Python dependencies
# Clean up broken precompiled C-extensions that crash on container Python 3.11
find .local/lib/python* -name "_helpers_c*.so" -delete 2>/dev/null || true

if [ -f "requirements.txt" ]; then
    REQ_HASH=$(md5sum requirements.txt 2>/dev/null | awk '{print $1}' || cksum requirements.txt 2>/dev/null || true)
    STORED_HASH=""
    [ -f ".requirements.hash" ] && STORED_HASH=$(cat .requirements.hash 2>/dev/null || true)
    
    if [ -n "$REQ_HASH" ] && [ "$REQ_HASH" = "$STORED_HASH" ]; then
        echo "🐍 [2/3] Зависимости Python актуальны (пропуск установки)."
    else
        echo "🐍 [2/3] Проверка и обновление зависимостей Python..."
        (pip install -r requirements.txt 2>/dev/null || pip install --no-cache-dir -r requirements.txt || true)
        find .local/lib/python* -name "_helpers_c*.so" -delete 2>/dev/null || true
        echo "$REQ_HASH" > .requirements.hash 2>/dev/null || true
    fi
fi

# 3. Update WebApp Static Bundle
echo "🌐 [3/3] Проверка и обновление WebApp..."
DOWNLOADED=0

# Priority 1: Python updater (pure standard library urllib + tarfile, works in any Docker container)
if [ -f "scripts/download_webapp.py" ]; then
    if python scripts/download_webapp.py; then
        DOWNLOADED=1
    fi
fi

# Priority 2: Fallback to curl + tar if Python script was missing or failed
if [ "$DOWNLOADED" -eq 0 ]; then
    TAR_URL="https://github.com/OptimusHigh/auxbass/releases/latest/download/webapp-dist.tar.gz"
    TEMP_TAR="/tmp/webapp-dist.tar.gz"
    if curl -fsSL --connect-timeout 15 --max-time 90 -L "$TAR_URL" -o "$TEMP_TAR" && [ -s "$TEMP_TAR" ]; then
        echo "📦 Распаковка актуальной сборки WebApp из GitHub Releases (curl)..."
        tar -xzf "$TEMP_TAR" -C . 2>/dev/null || true
        rm -f "$TEMP_TAR"
        DOWNLOADED=1
        echo "✅ WebApp успешно обновлен из GitHub Releases!"
    fi
fi

# Priority 3: Fallback build if dist is completely missing
if [ "$DOWNLOADED" -eq 0 ] && ([ ! -d "webapp/dist" ] || [ ! -f "webapp/dist/index.html" ]); then
    if command -v npm &> /dev/null; then
        echo "🔨 Сборка WebApp локально через npm..."
        (cd webapp && npm install && npm run build) || true
    else
        echo "ℹ️ WebApp сборка сохранена из текущего кеша."
    fi
fi

# 4. Run SQLite Schema Migrations
if [ -f "scripts/migrate_sqlite.py" ]; then
    echo "🗄️ [4/4] Проверка и применение миграций SQLite..."
    python -c "import scripts.migrate_sqlite as m; m.migrate_sqlite_db('tg_player.db')" 2>/dev/null || true
fi

echo "========================================================"
echo "          ✅ Обновление успешно завершено!              "
echo "========================================================"
