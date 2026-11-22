#!/usr/bin/env python3
"""Деплой проекта ЯМОГУ на PythonAnywhere одной командой (ASGI, uvicorn).

Запускать в консоли Bash на PythonAnywhere из папки проекта:

    python3 deploy_pythonanywhere.py

Что делает скрипт:
  1. создаёт виртуальное окружение venv и ставит requirements.txt;
  2. создаёт .env со случайным SECRET_KEY (если .env ещё нет);
  3. инициализирует базу dekanat.db (если её нет);
  4. ставит утилиту `pa` в отдельное окружение (~/.pa-cli);
  5. создаёт сайт командой `pa website create` — или перезагружает, если он уже есть.

Повторный запуск безопасен: готовое пропускается, а сайт просто перезагружается.
Перед первым запуском: Account -> API token -> Create, и удалите старое WSGI-приложение
на вкладке Web, если оно висит на этом же домене.
"""
from __future__ import annotations

import argparse
import getpass
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / "venv"
PA_CLI_DIR = Path.home() / ".pa-cli"
PLACEHOLDER_KEY = "replace-with-a-strong-random-secret-key"


def step(text: str) -> None:
    print(f"\n==> {text}")


def run(cmd: list[str], dry: bool, check: bool = True, **kw) -> subprocess.CompletedProcess:
    print("   $", " ".join(cmd))
    if dry:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(cmd, check=check, text=True, **kw)


def fail(msg: str) -> None:
    print(f"\nОШИБКА: {msg}", file=sys.stderr)
    sys.exit(1)


def setup_venv(python: str, dry: bool) -> Path:
    step(f"Виртуальное окружение ({python})")
    vpython = VENV_DIR / "bin" / "python"
    if vpython.exists():
        print("   venv уже есть — пропускаю создание")
    else:
        run([python, "-m", "venv", str(VENV_DIR)], dry)
    run([str(vpython), "-m", "pip", "install", "--upgrade", "pip"], dry)
    run([str(vpython), "-m", "pip", "install", "-r", str(PROJECT_DIR / "requirements.txt")], dry)
    return vpython


def setup_env_file(dry: bool) -> None:
    step("Файл .env")
    env_path = PROJECT_DIR / ".env"
    example = PROJECT_DIR / ".env.example"
    if env_path.exists():
        text = env_path.read_text(encoding="utf-8")
        if PLACEHOLDER_KEY not in text:
            print("   .env уже настроен — пропускаю")
            return
    elif example.exists():
        text = example.read_text(encoding="utf-8")
    else:
        text = "ACCESS_TOKEN_EXPIRE_HOURS=24\nDATABASE_PATH=dekanat.db\nCORS_ORIGINS=*\n"
    key = secrets.token_urlsafe(48)
    if re.search(r"^SECRET_KEY=.*$", text, flags=re.M):
        text = re.sub(r"^SECRET_KEY=.*$", f"SECRET_KEY={key}", text, flags=re.M)
    else:
        text = f"SECRET_KEY={key}\n" + text
    if not dry:
        env_path.write_text(text, encoding="utf-8")
    print("   записан .env со случайным SECRET_KEY (ключ нигде не показывается)")


def setup_database(vpython: Path, dry: bool) -> None:
    step("База данных")
    if (PROJECT_DIR / "dekanat.db").exists():
        print("   dekanat.db уже есть — пропускаю инициализацию")
    elif (PROJECT_DIR / "scripts").is_dir():
        run([str(vpython), "setup_db.py"], dry, cwd=PROJECT_DIR)
    else:
        fail("нет ни dekanat.db, ни папки scripts/ — загрузите одно из двух и запустите снова")


def setup_pa_cli(dry: bool) -> Path:
    step("Утилита pa (pythonanywhere CLI)")
    pa = PA_CLI_DIR / "bin" / "pa"
    if not pa.exists():
        run([sys.executable, "-m", "venv", str(PA_CLI_DIR)], dry)
    # предупреждение про typing-extensions в выводе можно игнорировать
    run([str(PA_CLI_DIR / "bin" / "pip"), "install", "--upgrade", "pythonanywhere"], dry, check=False)
    if not dry and not pa.exists():
        fail("не удалось установить утилиту pa")
    return pa


def deploy_site(pa: Path, domain: str, dry: bool) -> None:
    step(f"Сайт {domain}")
    command = (
        f"{VENV_DIR}/bin/uvicorn --app-dir {PROJECT_DIR} "
        "--uds ${DOMAIN_SOCKET} main:app"
    )
    exists = run([str(pa), "website", "get", "--domain", domain], dry,
                 check=False, capture_output=True).returncode == 0
    if exists and not dry:
        print("   сайт уже существует — перезагружаю")
        run([str(pa), "website", "reload", "--domain", domain], dry)
    else:
        # ${DOMAIN_SOCKET} передаётся как есть — подставит сам PythonAnywhere
        res = run([str(pa), "website", "create", "--domain", domain, "--command", command],
                  dry, check=False)
        if res.returncode != 0:
            fail(
                "не удалось создать сайт. Проверьте:\n"
                "  - создан ли API token (Account -> API token);\n"
                "  - удалено ли старое WSGI-приложение на вкладке Web;\n"
                "  - домен указан верно (--domain)."
            )
    print(f"\nГотово! Сайт: https://{domain}")
    print("(первые секунды возможен 404 — обновите страницу)")
    print(f"Логи: /var/log/{domain}.error.log  и  /var/log/{domain}.server.log")
    print(f"Обновление кода: git pull && python3 deploy_pythonanywhere.py")


def main() -> None:
    user = getpass.getuser().lower()
    ap = argparse.ArgumentParser(description="Деплой ЯМОГУ на PythonAnywhere")
    ap.add_argument("--domain", help=f"домен сайта (по умолчанию {user}.pythonanywhere.com)")
    ap.add_argument("--eu", action="store_true", help="аккаунт на EU-сервере (eu.pythonanywhere.com)")
    ap.add_argument("--python", default=sys.executable,
                    help="интерпретатор для venv, например python3.12 (по умолчанию текущий)")
    ap.add_argument("--dry-run", action="store_true", help="только показать команды, ничего не выполнять")
    args = ap.parse_args()

    domain = args.domain or f"{user}.{'eu.' if args.eu else ''}pythonanywhere.com"
    for f in ("main.py", "index.html", "requirements.txt"):
        if not (PROJECT_DIR / f).exists():
            fail(f"в папке проекта нет файла {f}")
    if shutil.which(args.python) is None and not Path(args.python).exists():
        fail(f"интерпретатор {args.python} не найден")

    vpython = setup_venv(args.python, args.dry_run)
    setup_env_file(args.dry_run)
    setup_database(vpython, args.dry_run)
    pa = setup_pa_cli(args.dry_run)
    deploy_site(pa, domain, args.dry_run)


if __name__ == "__main__":
    main()
