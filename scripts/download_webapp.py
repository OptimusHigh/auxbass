"""
Скрипт для автоматического скачивания и распаковки последнего релиза WebApp из GitHub Releases.
Использует исключительно стандартную библиотеку Python (urllib + tarfile),
что гарантирует работу в любых минимальных Docker-контейнерах без curl или tar.
"""
import json
import os
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "webapp" / "dist"
VERSION_FILE = DIST_DIR / "version.json"
RELEASE_URL = "https://github.com/OptimusHigh/auxbass/releases/latest/download/webapp-dist.tar.gz"


def get_current_build_id() -> str | None:
    if VERSION_FILE.exists():
        try:
            data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
            return data.get("buildId", "unknown")
        except Exception:
            pass
    return None


def download_and_extract() -> bool:
    current_build = get_current_build_id()
    print(f"🌐 Проверка и загрузка WebApp из GitHub Releases...")
    print(f"   Текущая версия на сервере: {current_build or 'отсутствует'}")

    req = urllib.request.Request(
        RELEASE_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TG-Player-Updater",
            "Accept": "application/octet-stream, application/gzip, */*",
        },
    )

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp_file:
        tmp_path = Path(tmp_file.name)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = getattr(resp, "status", 200)
                if status != 200:
                    print(f"⚠️ Ошибка HTTP при скачивании релиза: {status}")
                    return False

                total_bytes = 0
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    tmp_file.write(chunk)
                    total_bytes += len(chunk)

            tmp_file.flush()
            tmp_file.close()

            if total_bytes < 1000:
                print(f"⚠️ Скачанный файл слишком мал ({total_bytes} байт). Обновление пропущено.")
                return False

            print(f"📦 Распаковка архива ({total_bytes / 1024:.1f} KB)...")
            with tarfile.open(tmp_path, "r:gz") as tar:
                try:
                    tar.extractall(ROOT_DIR, filter="data")
                except TypeError:
                    tar.extractall(ROOT_DIR)

            new_build = get_current_build_id()
            print(f"✅ WebApp успешно обновлен! Актуальная версия: {new_build}")
            return True

        except Exception as e:
            print(f"⚠️ Ошибка при скачивании/распаковке WebApp: {e}")
            return False
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass


if __name__ == "__main__":
    success = download_and_extract()
    sys.exit(0 if success else 1)
