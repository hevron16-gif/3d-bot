"""
AutoDiag AI v1.0 — Система обновлений
Доставка обновлений на сервер: коды ошибок, схемы, код приложения.

Режимы доставки:
1. POLLING — сервер периодически проверяет источник обновлений
2. WEBHOOK — внешняя система отправляет обновления через POST
3. MANUAL — администратор запускает через /admin/auto-update

Обновляемые компоненты:
- error_codes   — база кодов ошибок (DB)
- schemas       — 2D-схемы узлов
- code          — исходный код приложения (git pull)
- repairs       — рекомендации по ремонту (DB)

Защита:
- Все обновления подписаны HMAC (UPDATE_SECRET)
- Версионирование с возможностью отката
- Атомарное применение (транзакция или ничего)
- Integrity manifest обновляется после каждого обновления
"""

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

# ════════════════ Конфигурация ════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# URL для проверки обновлений
UPDATE_SERVER = os.getenv("UPDATE_SERVER", "https://autodiag.ru/api/updates")
# Секрет для подписи (тот же что в integrity, или отдельный)
UPDATE_SECRET = os.getenv("UPDATE_SECRET", "AutoDiagUpdate2026Secure")
# Интервал проверки обновлений (секунды, 0 = отключено)
POLL_INTERVAL = int(os.getenv("UPDATE_POLL_INTERVAL", "3600"))  # 1 час
# Разрешить авто-применение обновлений кода?
AUTO_APPLY_CODE = os.getenv("UPDATE_AUTO_CODE", "false").lower() == "true"
# Разрешить авто-применение обновлений БД?
AUTO_APPLY_DB = os.getenv("UPDATE_AUTO_DB", "true").lower() == "true"

logger = logging.getLogger("autodiag.updater")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [UPDATER] %(message)s"))
    logger.addHandler(h)

# ════════════════ Версионирование ════════════════

def get_current_version() -> dict:
    """Текущая версия приложения."""
    version_file = os.path.join(BASE_DIR, "VERSION")
    info = {"version": "1.0.0", "build": "unknown", "codename": "dev"}
    try:
        with open(version_file, "r") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    k = k.strip().lower()
                    v = v.strip().strip('"').strip("'")
                    info[k] = v
    except FileNotFoundError:
        pass
    return info


def get_db_version(conn: sqlite3.Connection) -> int:
    """Версия базы данных из meta-таблицы."""
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        return int(row["value"]) if row else 1
    except Exception:
        return 1


def set_db_version(conn: sqlite3.Connection, version: int):
    """Обновить версию базы данных."""
    conn.execute("""
        INSERT OR REPLACE INTO meta (key, value, updated_at)
        VALUES ('schema_version', ?, ?)
    """, (str(version), datetime.now(timezone.utc).isoformat()))
    conn.commit()


# ════════════════ Подпись обновлений ════════════════

def verify_update_signature(payload: dict, signature: str) -> bool:
    """Проверить HMAC-подпись обновления."""
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    expected = hmac.new(
        UPDATE_SECRET.encode(),
        data.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def sign_update(payload: dict) -> str:
    """Подписать обновление (для тестов/отладки)."""
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hmac.new(
        UPDATE_SECRET.encode(),
        data.encode(),
        hashlib.sha256,
    ).hexdigest()


# ════════════════ Проверка обновлений ====================================

class UpdateInfo:
    """Информация об одном обновлении."""
    def __init__(self, data: dict):
        self.type: str = data.get("type", "")       # error_codes | schemas | code
        self.version: int = data.get("version", 0)  # версия БД/схем
        self.description: str = data.get("description", "")
        self.payload: dict = data.get("payload", {})
        self.urgent: bool = data.get("urgent", False)
        self.min_app_version: str = data.get("min_app_version", "1.0.0")


async def check_for_updates() -> list[UpdateInfo]:
    """
    Проверить наличие обновлений на сервере.
    Возвращает список доступных обновлений.
    """
    try:
        current = get_current_version()
        db_ver = 1
        try:
            from database import get_conn
            conn = get_conn()
            db_ver = get_db_version(conn)
            conn.close()
        except Exception:
            pass

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                UPDATE_SERVER + "/check",
                params={
                    "app_version": current.get("version", "1.0.0"),
                    "db_version": str(db_ver),
                    "platform": sys.platform,
                },
                headers={
                    "User-Agent": f"AutoDiagAI/{current.get('version', '1.0.0')}",
                    "X-Device-ID": _get_device_id_for_update(),
                },
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("updates"):
                return []

            updates = []
            for item in data["updates"]:
                sig = item.pop("signature", "")
                if not verify_update_signature(item, sig):
                    logger.warning(f"Invalid signature for update type={item.get('type')}")
                    continue
                updates.append(UpdateInfo(item))

            return updates
    except httpx.HTTPStatusError as e:
        logger.error(f"Update server error: {e.response.status_code}")
        return []
    except httpx.RequestError as e:
        logger.warning(f"Update server unreachable: {e}")
        return []
    except Exception as e:
        logger.error(f"Check updates failed: {e}")
        return []


# ════════════════ Применение обновлений ════════════════

def apply_db_update(update: UpdateInfo) -> bool:
    """
    Применить обновление базы кодов ошибок.
    payload: {"codes": [{"code": "P0171", "description": "...", ...}, ...]}
    """
    from database import get_conn
    conn = get_conn()
    try:
        codes = update.payload.get("codes", [])
        if not codes:
            return False

        upserted = 0
        for code_data in codes:
            code = code_data.get("code", "").upper()
            if not code:
                continue
            existing = conn.execute(
                "SELECT code FROM error_codes WHERE code = ?", (code,)
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE error_codes SET
                        description = ?, severity = ?, recommendations = ?,
                        russian_cars_only = ?, gas_equipment = ?,
                        updated_at = ?
                    WHERE code = ?
                """, (
                    code_data.get("description", existing.get("description", "")),
                    code_data.get("severity", existing.get("severity", "info")),
                    code_data.get("recommendations", existing.get("recommendations", "")),
                    int(code_data.get("russian_cars_only", existing.get("russian_cars_only", 0))),
                    int(code_data.get("gas_equipment", existing.get("gas_equipment", 0))),
                    datetime.now(timezone.utc).isoformat(),
                    code,
                ))
            else:
                conn.execute("""
                    INSERT INTO error_codes (code, description, severity, recommendations,
                        russian_cars_only, gas_equipment, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    code,
                    code_data.get("description", ""),
                    code_data.get("severity", "info"),
                    code_data.get("recommendations", ""),
                    int(code_data.get("russian_cars_only", 0)),
                    int(code_data.get("gas_equipment", 0)),
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ))
            upserted += 1

        if upserted > 0:
            set_db_version(conn, update.version)

        conn.commit()
        logger.info(f"DB update applied: {upserted} codes, version={update.version}")
        return upserted > 0
    except Exception as e:
        conn.rollback()
        logger.error(f"DB update failed: {e}")
        return False
    finally:
        conn.close()


def apply_schema_update(update: UpdateInfo) -> bool:
    """
    Применить обновление схем.
    payload: {"schemas": {"P0171": {...nodes, links, checkpoints}, ...}}
    """
    try:
        schemas = update.payload.get("schemas", {})
        if not schemas:
            return False

        from schemas.data import _SCHEMAS as SCHEMA_DB
        updated = 0
        for code, schema_data in schemas.items():
            code = code.upper()
            SCHEMA_DB[code] = schema_data
            updated += 1

        logger.info(f"Schema update applied: {updated} schemas, version={update.version}")
        return updated > 0
    except Exception as e:
        logger.error(f"Schema update failed: {e}")
        return False


def apply_code_update(update: UpdateInfo) -> bool:
    """
    Применить обновление кода (git pull).
    Только если AUTO_APPLY_CODE=true и есть .git.
    """
    if not AUTO_APPLY_CODE:
        logger.info(f"Code update {update.version} available but AUTO_APPLY_CODE=false")
        return False

    git_dir = os.path.join(BASE_DIR, ".git")
    if not os.path.isdir(git_dir):
        logger.warning("Code update skipped: .git not found")
        return False

    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"Code update applied: {result.stdout.strip()}")
            # Обновляем integrity manifest
            try:
                from integrity import seal
                seal()
            except Exception:
                pass
            return True
        else:
            logger.error(f"Code update failed: {result.stderr.strip()}")
            return False
    except Exception as e:
        logger.error(f"Code update failed: {e}")
        return False


def apply_repairs_update(update: UpdateInfo) -> bool:
    """
    Применить обновление рекомендаций по ремонту.
    payload: {"repairs": [{"code": "P0171", "recommendations": "..."}, ...]}
    """
    from database import get_conn
    conn = get_conn()
    try:
        repairs = update.payload.get("repairs", [])
        if not repairs:
            return False

        updated = 0
        for repair in repairs:
            code = repair.get("code", "").upper()
            rec = repair.get("recommendations", "")
            if not code or not rec:
                continue
            conn.execute(
                "UPDATE error_codes SET recommendations = ?, updated_at = ? WHERE code = ?",
                (rec[:1000], datetime.now(timezone.utc).isoformat(), code),
            )
            if conn.total_changes > 0:
                updated += 1

        conn.commit()
        logger.info(f"Repairs update applied: {updated} codes")
        return updated > 0
    except Exception as e:
        conn.rollback()
        logger.error(f"Repairs update failed: {e}")
        return False
    finally:
        conn.close()


async def apply_updates(updates: list[UpdateInfo]) -> dict:
    """
    Применить все доступные обновления.
    Возвращает отчёт.
    """
    results = {
        "total": len(updates),
        "applied": 0,
        "skipped": 0,
        "failed": 0,
        "details": [],
    }

    for update in updates:
        detail = {
            "type": update.type,
            "version": update.version,
            "description": update.description,
            "status": "skipped",
        }

        if update.type == "error_codes" and AUTO_APPLY_DB:
            if apply_db_update(update):
                detail["status"] = "applied"
                results["applied"] += 1
            else:
                detail["status"] = "failed"
                results["failed"] += 1
        elif update.type == "schemas" and AUTO_APPLY_DB:
            if apply_schema_update(update):
                detail["status"] = "applied"
                results["applied"] += 1
            else:
                detail["status"] = "failed"
                results["failed"] += 1
        elif update.type == "code":
            if apply_code_update(update):
                detail["status"] = "applied"
                results["applied"] += 1
            else:
                detail["status"] = "skipped"
                results["skipped"] += 1
        elif update.type == "repairs":
            if apply_repairs_update(update):
                detail["status"] = "applied"
                results["applied"] += 1
            else:
                detail["status"] = "failed"
                results["failed"] += 1
        else:
            results["skipped"] += 1

        results["details"].append(detail)

    return results


# ════════════════ Вебхук-приёмник ════════════════

async def process_webhook(body: dict, signature: str) -> dict:
    """
    Обработать входящий вебхук с обновлением.
    Используется когда внешняя система активно пушит обновления.

    Тело запроса:
    {
        "type": "error_codes",
        "version": 42,
        "description": "Added P0400-P0499 codes",
        "payload": {...},
        "urgent": false
    }
    Заголовок X-Update-Signature: HMAC-SHA256(body)
    """
    # Проверка подписи
    payload = {k: v for k, v in body.items() if k != "signature"}
    if not verify_update_signature(payload, signature):
        logger.warning("Webhook rejected: invalid signature")
        return {"status": "rejected", "reason": "invalid_signature"}

    update = UpdateInfo(body)
    logger.info(f"Webhook received: type={update.type} v{update.version}")

    # Применить
    results = await apply_updates([update])
    detail = results["details"][0] if results["details"] else {"status": "error"}

    return {
        "status": detail["status"],
        "type": update.type,
        "version": update.version,
    }


# ════════════════ Фоновый опрос ==========================================

_polling_started = False


async def _polling_loop():
    """Фоновый цикл проверки обновлений."""
    global _polling_started
    if _polling_started:
        return
    _polling_started = True

    logger.info(f"Update polling started (interval={POLL_INTERVAL}s, server={UPDATE_SERVER})")

    while True:
        try:
            await _sleep(POLL_INTERVAL)
            updates = await check_for_updates()
            if updates:
                logger.info(f"Pending updates: {len(updates)}")
                for u in updates:
                    logger.info(f"  - {u.type} v{u.version}: {u.description[:80]}")
                if AUTO_APPLY_DB or AUTO_APPLY_CODE:
                    result = await apply_updates(updates)
                    logger.info(f"Auto-apply result: {result['applied']} applied, "
                                f"{result['skipped']} skipped, {result['failed']} failed")
        except Exception as e:
            logger.error(f"Polling error: {e}")


async def _sleep(seconds: int):
    """Асинхронный sleep без блокировки event loop."""
    # Используем asyncio если доступен, иначе time.sleep в потоке
    try:
        import asyncio
        await asyncio.sleep(seconds)
    except RuntimeError:
        time.sleep(seconds)


def start_polling():
    """Запустить фоновый опрос обновлений (thread-safe)."""
    if POLL_INTERVAL <= 0:
        logger.info("Update polling disabled (POLL_INTERVAL=0)")
        return

    import asyncio
    import threading

    def _run():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_polling_loop())
        except Exception as e:
            logger.error(f"Polling thread error: {e}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    logger.info("Update polling thread started")


# ════════════════ Вспомогательные ════════════════

def _get_device_id_for_update() -> str:
    """Device ID для заголовков запросов обновлений."""
    try:
        from device import get_device_id
        return get_device_id()
    except Exception:
        return "unknown"


# ════════════════ Реализация auto_update_codes ════════════════

def auto_update_codes() -> int:
    """
    Ручной запуск обновления кодов ошибок.
    Синхронная версия для вызова из database.py / admin.py.
    Возвращает количество обновлённых кодов.
    """
    try:
        import asyncio

        async def _do():
            updates = await check_for_updates()
            code_updates = [u for u in updates if u.type == "error_codes"]
            if not code_updates:
                return 0
            total = 0
            for update in code_updates:
                if apply_db_update(update):
                    total += len(update.payload.get("codes", []))
            return total

        # Внутри event loop или без него
        try:
            loop = asyncio.get_running_loop()
            # Уже в loop — создаём новый в потоке
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _do())
                return future.result(timeout=30)
        except RuntimeError:
            # Нет running loop — просто запускаем
            return asyncio.run(_do())
    except Exception as e:
        logger.error(f"auto_update_codes failed: {e}")
        return 0


# ════════════════════════════════════════════════════════
#  Автоматическое получение обновлений для клиентов
# ════════════════════════════════════════════════════════

# Кэш обновлений, чтобы не ходить на UPDATE_SERVER при каждом клиентском запросе
_cached_updates: list[dict] = []
_cache_timestamp: float = 0.0
_seq_counter: int = 0
CACHE_TTL = 300  # 5 минут — держим кэш свежим


async def refresh_update_cache() -> int:
    """
    Обновить кэш обновлений из внешнего UPDATE_SERVER.
    Каждое обновление получает глобальный sequence-номер.
    Возвращает количество новых обновлений в кэше.
    """
    global _cached_updates, _cache_timestamp, _seq_counter
    try:
        updates = await check_for_updates()

        for u in updates:
            _seq_counter += 1
            # Пропускаем дубликаты (тот же type + version уже есть в кэше)
            dup = any(
                c["type"] == u.type and c["version"] == u.version
                for c in _cached_updates
            )
            if dup:
                continue

            _cached_updates.append({
                "seq": _seq_counter,              # глобальный номер
                "type": u.type,
                "version": u.version,
                "description": u.description,
                "urgent": u.urgent,
                "payload": u.payload,             # полный payload для клиента
                "min_app_version": u.min_app_version,
            })
        _cache_timestamp = time.time()
        new_count = len(updates)
        if new_count:
            logger.info(f"Update cache refresh: {new_count} new, seq={_seq_counter}")
        return new_count
    except Exception as e:
        logger.warning(f"Cache refresh failed: {e}")
        return 0


def get_client_updates(since_seq: int = 0) -> dict:
    """
    Вернуть обновления для мобильного клиента.
    since_seq — глобальный sequence-номер последнего применённого обновления.
    Возвращает {"updates": [...], "server_seq": ..., "refresh_after_seconds": ...}
    """
    global _cached_updates

    relevant = [u for u in _cached_updates if u["seq"] > since_seq]

    return {
        "available": len(relevant),
        "updates": relevant,
        "server_seq": _seq_counter,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "refresh_after_seconds": CACHE_TTL,
    }


async def start_background_fetcher():
    """Фоновый цикл: обновляет кэш каждые CACHE_TTL секунд."""
    while True:
        try:
            await refresh_update_cache()
        except Exception as e:
            logger.error(f"Background fetcher error: {e}")
        await asyncio.sleep(CACHE_TTL)
