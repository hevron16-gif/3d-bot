"""
AutoDiag AI v1.0 — Модуль базы данных (SQLite)
Офлайн-расшифровка, история диагностик, исторические коды (03/07/0A),
автообновление, free/paid пользователи.
"""

import sqlite3
import os
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path("autodiag.db")
SCHEMA_PATH = Path("schema.sql")

AUTO_UPDATE_URL = "https://autodiag.ru/api/codes.json"  # заглушка

# ===================== Локаль tuple → dict =====================
def dict_factory(cursor, row):
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Создать таблицы и заполнить базовые коды."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS error_codes (
            code TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            category TEXT DEFAULT 'engine',
            russian_cars_only INTEGER DEFAULT 0,
            gas_equipment     INTEGER DEFAULT 0,
            special_equipment INTEGER DEFAULT 0,
            severity          TEXT DEFAULT 'medium',
            recommendations   TEXT,
            schema_url        TEXT,
            source            TEXT DEFAULT 'manual',
            created_at        TEXT,
            updated_at        TEXT
        );

        CREATE TABLE IF NOT EXISTS diagnostics (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT NOT NULL,
            error_code TEXT NOT NULL,
            car_brand  TEXT,
            car_model  TEXT,
            vin        TEXT,
            diagnosis  TEXT,
            source     TEXT DEFAULT 'offline',
            user_id    TEXT,
            status     TEXT DEFAULT 'completed'
        );

        CREATE TABLE IF NOT EXISTS historical_codes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            code       TEXT NOT NULL,
            mode       TEXT NOT NULL,
            timestamp  TEXT NOT NULL,
            car_brand  TEXT,
            car_model  TEXT,
            frequency  INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS admins (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT DEFAULT 'admin'
        );

        CREATE TABLE IF NOT EXISTS paid_users (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          TEXT UNIQUE NOT NULL,
            tier             TEXT DEFAULT 'free',
            features         TEXT DEFAULT '[]',
            valid_until      TEXT
        );

        CREATE TABLE IF NOT EXISTS license_keys (
            key_hash     TEXT PRIMARY KEY,
            tier         TEXT NOT NULL,
            user_id      TEXT,
            device_id    TEXT,
            activated_at TEXT,
            valid_until  TEXT,
            is_active    INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sync_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            payload     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            synced      INTEGER DEFAULT 0
        );
    """)

    # Заполнить базовые коды если пусто
    if conn.execute("SELECT COUNT(*) as cnt FROM error_codes").fetchone()["cnt"] == 0:
        _seed_codes(conn)

    conn.commit()
    conn.close()


def _seed_codes(conn: sqlite3.Connection):
    """Предзагрузка популярных кодов с упором на российские авто + ГБО."""
    codes = [
        # Стандартные OBD-II
        ("P0100", "Датчик массового расхода воздуха (MAF) — неисправность цепи", "engine", 0, 0, 0, "high", "Проверить разъём MAF, целостность проводки, загрязнение датчика.", None),
        ("P0101", "MAF — выход за пределы диапазона", "engine", 0, 0, 0, "high", "Очистить или заменить MAF.", None),
        ("P0102", "MAF — низкий сигнал", "engine", 0, 0, 0, "high", "Проверить питание датчика, заменить при неисправности.", None),
        ("P0103", "MAF — высокий сигнал", "engine", 0, 0, 0, "high", "Проверить проводку, заменить датчик.", None),
        ("P0110", "Датчик температуры впускного воздуха — неисправность цепи", "engine", 0, 0, 0, "medium", "Проверить датчик и проводку.", None),
        ("P0113", "Датчик температуры воздуха — высокий сигнал", "engine", 0, 0, 0, "medium", "Проверить датчик IAT, заменить.", None),
        ("P0115", "Датчик температуры ОЖ — неисправность цепи", "engine", 0, 0, 0, "high", "Проверить датчик ECT и проводку.", None),
        ("P0120", "Датчик положения дроссельной заслонки (TPS) — неисправность", "engine", 0, 0, 0, "high", "Проверить TPS, адаптировать заслонку.", None),
        ("P0130", "Датчик кислорода (bank 1, sensor 1) — неисправность цепи", "engine", 0, 0, 0, "high", "Проверить лямбда-зонд, проводку.", None),
        ("P0131", "Датчик кислорода — низкое напряжение", "engine", 0, 0, 0, "high", "Проверить лямбда-зонд, возможен подсос воздуха.", None),
        ("P0134", "Датчик кислорода — нет активности", "engine", 0, 0, 0, "high", "Заменить лямбда-зонд.", None),
        ("P0135", "Подогрев датчика кислорода — неисправность цепи", "engine", 0, 0, 0, "medium", "Проверить цепь подогрева лямбда-зонда.", None),
        ("P0171", "Слишком бедная смесь (bank 1)", "engine", 0, 1, 0, "high", "Проверить подсос воздуха, топливный фильтр, форсунки, лямбда-зонд. На ГБО — проверить редуктор и фильтр газа.", None),
        ("P0172", "Слишком богатая смесь (bank 1)", "engine", 0, 1, 0, "high", "Проверить форсунки, давление топлива, лямбда-зонд. На ГБО — возможно залипание клапана редуктора.", None),
        ("P0200", "Неисправность цепи форсунок", "engine", 0, 0, 0, "high", "Проверить проводку форсунок, ЭБУ.", None),
        ("P0300", "Случайные / множественные пропуски зажигания", "engine", 0, 1, 0, "high", "Проверить свечи, катушки, провода, форсунки. На ГБО — возможно неверная настройка.", None),
        ("P0301", "Пропуски зажигания в цилиндре 1", "engine", 0, 0, 0, "high", "Проверить свечу, катушку, компрессию цилиндра 1.", None),
        ("P0302", "Пропуски зажигания в цилиндре 2", "engine", 0, 0, 0, "high", "Проверить свечу, катушку, компрессию цилиндра 2.", None),
        ("P0303", "Пропуски зажигания в цилиндре 3", "engine", 0, 0, 0, "high", "Проверить свечу, катушку, компрессию цилиндра 3.", None),
        ("P0304", "Пропуски зажигания в цилиндре 4", "engine", 0, 0, 0, "high", "Проверить свечу, катушку, компрессию цилиндра 4.", None),
        ("P0325", "Датчик детонации — неисправность цепи", "engine", 0, 0, 0, "medium", "Проверить датчик детонации, проводку.", None),
        ("P0335", "Датчик положения коленвала (CKP) — неисправность цепи", "engine", 0, 0, 0, "critical", "Критично! Проверить датчик коленвала, проводку, зазор.", None),
        ("P0340", "Датчик положения распредвала (CMP) — неисправность", "engine", 0, 0, 0, "high", "Проверить датчик распредвала и цепь.", None),
        ("P0351", "Катушка зажигания A — неисправность цепи", "engine", 0, 0, 0, "high", "Проверить катушку, проводку, ЭБУ.", None),
        ("P0400", "Система EGR — неисправность", "engine", 0, 0, 0, "medium", "Проверить клапан EGR, магистрали.", None),
        ("P0420", "Низкая эффективность катализатора (bank 1)", "engine", 0, 1, 0, "medium", "Проверить катализатор, лямбда-зонды. На ГБО — катализатор может быстрее деградировать.", None),
        ("P0430", "Низкая эффективность катализатора (bank 2)", "engine", 0, 0, 0, "medium", "Проверить катализатор bank 2.", None),
        ("P0440", "Система улавливания паров топлива (EVAP) — неисправность", "evap", 0, 0, 0, "low", "Проверить крышку бензобака, клапан продувки.", None),
        ("P0500", "Датчик скорости — неисправность", "transmission", 0, 0, 0, "high", "Проверить датчик скорости, проводку.", None),
        ("P0505", "Регулятор холостого хода (IAC) — неисправность", "engine", 0, 0, 0, "medium", "Проверить клапан IAC, очистить дроссель.", None),
        ("P0560", "Напряжение системы — неисправность", "electrical", 0, 0, 0, "high", "Проверить АКБ, генератор, проводку.", None),
        ("P0601", "Ошибка контрольной суммы ПЗУ ЭБУ", "ecu", 0, 0, 0, "critical", "Возможен сбой прошивки ЭБУ. Требуется перепрошивка.", None),
        ("P0700", "Неисправность системы управления АКПП", "transmission", 0, 0, 0, "high", "Считать коды АКПП отдельно.", None),

        # Российские авто (специфичные коды)
        ("P1123", "Бедная смесь в режиме холостого хода (ВАЗ/ГАЗ)", "engine", 1, 0, 0, "high", "Характерно для ВАЗ: подсос воздуха, загрязнение форсунок.", None),
        ("P1124", "Богатая смесь в режиме холостого хода (ВАЗ/ГАЗ)", "engine", 1, 0, 0, "high", "Характерно для ВАЗ: проверить регулятор давления топлива.", None),
        ("P1135", "Датчик кислорода — неверный сигнал (Lada)", "engine", 1, 0, 0, "high", "Заменить ДК1 на Ладе. Частая проблема.", None),
        ("P2135", "Датчик положения дроссельной заслонки — корреляция (ГАЗ)", "engine", 1, 0, 0, "high", "Характерно для ГАЗ с ЭСУД. Адаптировать дроссель.", None),
        ("P2178", "Слишком богатая смесь на холостом ходу (УАЗ)", "engine", 1, 0, 0, "medium", "Проверить систему питания, ДМРВ.", None),

        # ГБО-специфичные коды
        ("P2282", "Утечка воздуха между дросселем и клапанами (ГБО)", "engine", 0, 1, 0, "high", "Проверить прокладки впуска, форсунки ГБО.", None),
        ("P2294", "Регулятор давления топлива — неисправность (ГБО)", "engine", 0, 1, 0, "high", "Проверить газовый редуктор, магистрали.", None),

        # Спецтехника
        ("P1515", "Ошибка педали газа (спецтехника)", "engine", 0, 0, 1, "critical", "Проверить датчик педали газа, адаптировать.", None),
        ("U0100", "Потеря связи с ЭБУ двигателя", "communication", 0, 0, 1, "critical", "Проверить CAN-шину, питание ЭБУ.", None),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO error_codes (code, description, category, russian_cars_only, gas_equipment, special_equipment, severity, recommendations, schema_url) VALUES (?,?,?,?,?,?,?,?,?)",
        codes
    )


# ===================== API =====================

def lookup_error(code: str) -> Optional[dict]:
    """Офлайн-поиск ошибки."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM error_codes WHERE code = ?", (code.upper(),)).fetchone()
    conn.close()
    return row


def lookup_errors_batch(codes: list[str]) -> list[dict]:
    """Пакетный офлайн-поиск."""
    if not codes:
        return []
    conn = get_conn()
    placeholders = ",".join("?" * len(codes))
    rows = conn.execute(
        f"SELECT * FROM error_codes WHERE code IN ({placeholders})",
        [c.upper() for c in codes]
    ).fetchall()
    conn.close()
    return rows


def save_diagnosis(user_id: str, error_code: str, car_brand: str, car_model: str,
                   vin: str, diagnosis: str, source: str = "ai") -> int:
    """Сохранить результат диагностики в историю."""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO diagnostics (timestamp, error_code, car_brand, car_model, vin, diagnosis, source, user_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (datetime.now(timezone.utc).isoformat(), error_code, car_brand, car_model, vin, diagnosis, source, user_id)
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_history(user_id: str, limit: int = 50) -> list[dict]:
    """Получить историю диагностик пользователя.
    Free-пользователи видят только последние 10 записей."""
    from pricing import is_paid
    if not is_paid(user_id):
        limit = min(limit, 10)
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM diagnostics WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return rows


def get_all_history(limit: int = 100) -> list[dict]:
    """Получить всю историю (админ)."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM diagnostics ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows


def save_historical_code(code: str, mode: str, car_brand: str = None, car_model: str = None):
    """Сохранить исторический код (режимы 03, 07, 0A)."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT id, frequency FROM historical_codes WHERE code = ? AND mode = ? AND car_brand = ?",
        (code, mode, car_brand)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE historical_codes SET frequency = frequency + 1, timestamp = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), existing["id"])
        )
    else:
        conn.execute(
            "INSERT INTO historical_codes (code, mode, timestamp, car_brand, car_model, frequency) VALUES (?,?,?,?,?,1)",
            (code, mode, datetime.now(timezone.utc).isoformat(), car_brand, car_model)
        )
    conn.commit()
    conn.close()

    # Миграция: добавить столбцы, если их нет (для существующих БД)
    _migrate_db()


def _migrate_db():
    """Добавить недостающие столбцы в существующие таблицы."""
    conn = get_conn()
    try:
        # Проверяем наличие столбца source в error_codes
        cols = conn.execute("PRAGMA table_info(error_codes)").fetchall()
        col_names = {c["name"] for c in cols}

        if "source" not in col_names:
            conn.execute("ALTER TABLE error_codes ADD COLUMN source TEXT DEFAULT 'manual'")
        if "created_at" not in col_names:
            conn.execute("ALTER TABLE error_codes ADD COLUMN created_at TEXT")
        if "updated_at" not in col_names:
            conn.execute("ALTER TABLE error_codes ADD COLUMN updated_at TEXT")

        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def get_historical_codes(car_brand: str = None, mode: str = None) -> list[dict]:
    """Получить исторические коды (анализ частотности)."""
    conn = get_conn()
    q = "SELECT * FROM historical_codes WHERE 1=1"
    params = []
    if car_brand:
        q += " AND car_brand = ?"
        params.append(car_brand)
    if mode:
        q += " AND mode = ?"
        params.append(mode)
    q += " ORDER BY frequency DESC LIMIT 50"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return rows


def get_error_stats() -> list[dict]:
    """Статистика по ошибкам: частотность."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT error_code, count(*) as cnt, car_brand
           FROM diagnostics GROUP BY error_code, car_brand
           ORDER BY cnt DESC LIMIT 30"""
    ).fetchall()
    conn.close()
    return rows


# ===================== Auto-update =====================

def auto_update_codes() -> int:
    """Автообновление базы кодов из внешнего источника."""
    from updater import auto_update_codes as _real_auto_update
    return _real_auto_update()


# ===================== Paid Users =====================

def get_user_tier(user_id: str) -> str:
    """Получить уровень подписки пользователя: free / pro / enterprise."""
    conn = get_conn()
    row = conn.execute("SELECT tier FROM paid_users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["tier"] if row else "free"


def set_user_tier(user_id: str, tier: str, valid_until: str = None):
    """Установить уровень подписки."""
    conn = get_conn()
    conn.execute(
        """INSERT INTO paid_users (user_id, tier, valid_until) VALUES (?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET tier=excluded.tier, valid_until=excluded.valid_until""",
        (user_id, tier, valid_until)
    )
    conn.commit()
    conn.close()


def get_user_features(user_id: str) -> list[str]:
    """Получить список включённых фич пользователя."""
    tier = get_user_tier(user_id)
    if tier == "enterprise":
        return ["offline", "elm327", "basic_history",
                "ai", "schemas", "live_graphs", "self_learning", "sync",
                "full_history", "basic_simulator", "admin", "auto_update"]
    elif tier == "pro":
        return ["offline", "elm327", "basic_history",
                "ai", "schemas", "live_graphs", "self_learning", "sync"]
    else:
        return ["offline", "elm327", "basic_history"]


# ===================== Sync =====================

def queue_sync(payload: dict):
    """Добавить запись в очередь синхронизации."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO sync_queue (payload, created_at) VALUES (?, ?)",
        (json.dumps(payload), datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()


def get_sync_queue(limit: int = 100) -> list[dict]:
    """Получить неотправленные записи синхронизации."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM sync_queue WHERE synced = 0 ORDER BY id ASC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return rows


def mark_synced(sync_ids: list[int]):
    """Пометить записи как синхронизированные."""
    if not sync_ids:
        return
    conn = get_conn()
    placeholders = ",".join("?" * len(sync_ids))
    conn.execute(f"UPDATE sync_queue SET synced = 1 WHERE id IN ({placeholders})", sync_ids)
    conn.commit()
    conn.close()


# Инициализация при импорте
init_db()
