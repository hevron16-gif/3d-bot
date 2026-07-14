"""
AutoDiag AI v1.0 — Модуль лицензионных ключей
Генерация, активация и валидация лицензионных ключей.

Формат ключа: AUTODIAG-XXXX-XXXX-XXXX (4 блока по 4 символа, 16 hex-цифр)
Ключ генерируется из SHA256(секрет + tier + timestamp) и привязывается к устройству при активации.

Таблица license_keys:
  key_hash TEXT PRIMARY KEY  -- SHA256 ключа
  tier TEXT                  -- pro / enterprise
  user_id TEXT               -- кто активировал
  device_id TEXT             -- ID устройства (чтобы один ключ = одно устройство)
  activated_at TEXT          -- ISO-дата активации
  valid_until TEXT           -- срок действия (год с момента активации)
  is_active INTEGER          -- 1 = активен, 0 = деактивирован
"""

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import database as db

router = APIRouter(prefix="/license", tags=["license"])

SECRET_KEY = b"AutoDiagAI-SecretKey-2026-v1"  # в продакшене — из переменной окружения

# ════════════════ Генерация ключа ════════════════

def generate_license_key(tier: str) -> tuple[str, str]:
    """
    Сгенерировать новый лицензионный ключ.
    Возвращает (ключ, хеш_ключа).

    tier: 'pro' или 'enterprise'
    """
    timestamp = int(time.time())
    raw = f"{tier}:{timestamp}:{secrets.token_hex(8)}"
    h = hmac.new(SECRET_KEY, raw.encode(), hashlib.sha256).hexdigest()[:16]
    key = f"AUTODIAG-{h[0:4].upper()}-{h[4:8].upper()}-{h[8:12].upper()}-{h[12:16].upper()}"
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    return key, key_hash


def validate_key_format(key: str) -> Optional[str]:
    """
    Проверить формат ключа. Возвращает нормализованный ключ или None.
    Пример: 'AUTODIAG-ABCD-1234-EF56-7890'
    """
    key = key.strip().upper()
    parts = key.split("-")
    if len(parts) != 5 or parts[0] != "AUTODIAG":
        return None
    for p in parts[1:]:
        if len(p) != 4 or not all(c in "0123456789ABCDEF" for c in p):
            return None
    return key

# ════════════════ Активация ════════════════

def activate_license(key: str, user_id: str, device_id: str) -> dict:
    """
    Активировать лицензионный ключ на устройство.
    Возвращает результат активации.
    """
    normalized = validate_key_format(key)
    if not normalized:
        return {"success": False, "error": "invalid_format",
                "message": "Неверный формат ключа. Ожидается: AUTODIAG-XXXX-XXXX-XXXX-XXXX"}

    key_hash = hashlib.sha256(normalized.encode()).hexdigest()

    # Проверяем, существует ли такой ключ в базе пре-сгенерированных
    conn = db.get_conn()
    existing = conn.execute(
        "SELECT * FROM license_keys WHERE key_hash = ?", (key_hash,)
    ).fetchone()

    if not existing:
        # Проверяем, валидный ли ключ (HMAC-подпись)
        # Извлекаем hex-часть и реконструируем
        hex_part = normalized.replace("AUTODIAG-", "").replace("-", "").lower()
        # Проверка: ключ должен быть сгенерирован нами
        # простая проверка — ищем по префиксу в базе
        conn.close()
        return {"success": False, "error": "key_not_found",
                "message": "Лицензионный ключ не найден. Проверьте правильность ввода."}

    if existing["is_active"] and existing["user_id"] != user_id:
        conn.close()
        return {"success": False, "error": "already_activated",
                "message": "Этот ключ уже активирован другим пользователем."}

    if existing["is_active"] and existing["device_id"] and existing["device_id"] != device_id:
        conn.close()
        return {"success": False, "error": "device_mismatch",
                "message": "Лицензия привязана к другому устройству. "
                           "Для переноса обратитесь в поддержку."}

    # Активируем
    tier = existing["tier"]
    valid_until = existing["valid_until"] or (
        datetime.now(timezone.utc) + timedelta(days=365)
    ).isoformat()

    conn.execute(
        """UPDATE license_keys
           SET user_id = ?, device_id = ?, activated_at = ?, valid_until = ?, is_active = 1
           WHERE key_hash = ?""",
        (user_id, device_id, datetime.now(timezone.utc).isoformat(), valid_until, key_hash)
    )
    conn.commit()

    # Обновляем tier пользователя
    db.set_user_tier(user_id, tier, valid_until)

    conn.close()

    return {
        "success": True,
        "tier": tier,
        "valid_until": valid_until,
        "features": db.get_user_features(user_id),
        "message": f"Лицензия {tier.upper()} успешно активирована!"
    }


# ════════════════ Проверка ════════════════

def get_license_status(user_id: str, device_id: str) -> dict:
    """Получить статус лицензии для пользователя и устройства."""
    tier = db.get_user_tier(user_id)
    features = db.get_user_features(user_id)

    if tier == "free":
        return {
            "tier": "free",
            "is_paid": False,
            "features": features,
            "locked_features": ["ai", "schemas", "live_graphs", "self_learning", "sync",
                                "full_history", "basic_simulator", "admin", "auto_update"],
            "message": "Бесплатная версия. Оформите Pro для полного доступа.",
            "upgrade_url": "/pricing/plans",
        }

    conn = db.get_conn()
    row = conn.execute(
        "SELECT valid_until FROM license_keys WHERE user_id = ? AND is_active = 1 ORDER BY activated_at DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    conn.close()

    valid_until = row["valid_until"] if row else None
    is_expired = False
    if valid_until:
        try:
            expiry = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
            is_expired = expiry < datetime.now(timezone.utc)
        except (ValueError, TypeError):
            pass

    if is_expired:
        return {
            "tier": tier,
            "is_paid": True,
            "is_expired": True,
            "valid_until": valid_until,
            "features": features,
            "message": "Срок действия лицензии истёк. Продлите для продолжения.",
            "upgrade_url": "/pricing/plans",
        }

    return {
        "tier": tier,
        "is_paid": True,
        "is_expired": False,
        "valid_until": valid_until,
        "features": features,
        "message": f"Активна лицензия {tier.upper()}.",
    }


# ════════════════ Pre-generate keys (admin) ════════════════

def pre_generate_keys(tier: str, count: int, days_valid: int = 365) -> list[str]:
    """
    Предварительно сгенерировать ключи и сохранить в БД.
    Возвращает список ключей (показать админу).
    """
    keys = []
    conn = db.get_conn()
    valid_until = (datetime.now(timezone.utc) + timedelta(days=days_valid)).isoformat()

    for _ in range(count):
        key, key_hash = generate_license_key(tier)
        conn.execute(
            "INSERT OR REPLACE INTO license_keys (key_hash, tier, valid_until, is_active) VALUES (?, ?, ?, 0)",
            (key_hash, tier, valid_until)
        )
        keys.append(key)

    conn.commit()
    conn.close()
    return keys


# ════════════════ Эндпоинты ════════════════

class ActivateRequest(BaseModel):
    key: str
    device_id: str = ""


@router.post("/activate")
def activate(req: ActivateRequest, user_id: str = Query(..., description="ID пользователя")):
    """Активировать лицензионный ключ."""
    if not req.key or not req.key.strip():
        raise HTTPException(status_code=400, detail="Лицензионный ключ обязателен.")
    if not req.device_id:
        raise HTTPException(status_code=400, detail="device_id обязателен для привязки ключа.")

    result = activate_license(req.key.strip(), user_id, req.device_id.strip())
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.get("/status")
def status(user_id: str = Query(..., description="ID пользователя"),
           device_id: str = Query(default="", description="ID устройства")):
    """Проверить статус лицензии."""
    return get_license_status(user_id, device_id)


@router.get("/features")
def features(user_id: str = Query(..., description="ID пользователя")):
    """Получить доступные и недоступные фичи."""
    from pricing import get_paid_features
    return get_paid_features(user_id)
