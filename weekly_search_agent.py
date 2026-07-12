"""
Автономный недельный агент поиска — standalone скрипт.
Запускается вручную или через cron на Render:

  python weekly_search_agent.py           # обычный прогон (5 кодов)
  python weekly_search_agent.py --all     # все 30+ кодов
  python weekly_search_agent.py --dry     # тестовый прогон без сохранения
  python weekly_search_agent.py --codes=10  # указать количество
"""

import sys
import os
import json
from datetime import datetime, timezone
from pathlib import Path

# ─── Настройка окружения ───
# Рабочая директория — папка, где лежит этот скрипт
SCRIPT_DIR = Path(__file__).resolve().parent
os.chdir(SCRIPT_DIR)

# Подгружаем .env если есть (для локального запуска)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Проверяем наличие ключа DeepSeek
if not os.environ.get("DEEPSEEK_API_KEY"):
    print("[ОШИБКА] DEEPSEEK_API_KEY не задан. Установите переменную окружения.")
    sys.exit(1)

import asyncio
import auto_search


def parse_args():
    """Разбирает аргументы командной строки."""
    dry_run = "--dry" in sys.argv
    run_all = "--all" in sys.argv
    max_codes = 5  # по умолчанию

    for arg in sys.argv[1:]:
        if arg.startswith("--codes="):
            try:
                max_codes = int(arg.split("=", 1)[1])
            except ValueError:
                print(f"[ОШИБКА] Некорректное значение: {arg}")
                sys.exit(1)

    if run_all:
        max_codes = 99  # обработает все, но skip_recent ограничит

    return dry_run, max_codes, run_all


async def main():
    dry_run, max_codes, run_all = parse_args()

    mode = "DRY RUN (без сохранения)" if dry_run else "БОЕВОЙ ПРОГОН"
    print("=" * 60)
    print(f"  АВТОНОМНЫЙ НЕДЕЛЬНЫЙ АГЕНТ ПОИСКА")
    print(f"  Режим: {mode}")
    print(f"  Лимит кодов: {max_codes}")
    print(f"  Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 60)
    print()

    start = datetime.now(timezone.utc)

    report = await auto_search.run_weekly_agent(
        max_codes=max_codes,
        skip_recent_hours=24 if not run_all else 0,
        dry_run=dry_run,
    )

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()

    # ─── Итоговый отчёт ───
    print()
    print("-" * 40)
    print(f"  НАЙДЕНО нового: {report['found']}")
    print(f"  ПРОПУЩЕНО:       {report['skipped']}")
    print(f"  ОШИБОК:          {report['errors']}")
    print(f"  Время:           {elapsed:.1f} сек")
    print("-" * 40)

    # Детали по каждому коду
    if report["details"]:
        print()
        print("ДЕТАЛИ ПО КОДАМ:")
        for d in report["details"]:
            status_icon = {
                "found_new":      "[+]",
                "nothing_new":    "[-]",
                "all_duplicates": "[=]",
                "error":          "[!]",
            }.get(d["status"], "[?]")
            print(f"  {status_icon} {d['code']} {d['brand']} -- {d['status']}")

    # Лог последних запусков
    log_path = auto_search.AGENT_LOG
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            total_lines = sum(1 for _ in f)
        print(f"\nВсего записей в логе агента: {total_lines}")

    if dry_run:
        print("\n[!] Это был DRY RUN -- изменения НЕ сохранены.")
        print("    Для боевого прогона запустите без --dry")
    else:
        print("\n[+] Прогон завершён. Новые данные сохранены в knowledge_base.jsonl.")


if __name__ == "__main__":
    asyncio.run(main())
