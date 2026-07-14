"""
AutoDiag AI v1.0 — Панель администратора
Управление базой: пользователи, коды ошибок, статистика.

Только для Enterprise-пользователей.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone

from database import (
    get_error_stats, get_all_history, get_historical_codes,
    lookup_error, lookup_errors_batch, auto_update_codes,
    get_user_tier, set_user_tier,
)

router = APIRouter(prefix="/admin", tags=["admin"])


# ================ Модели ================

class CodeUpdate(BaseModel):
    code: str
    description: Optional[str] = None
    severity: Optional[str] = None
    recommendations: Optional[str] = None
    russian_cars_only: Optional[bool] = None
    gas_equipment: Optional[bool] = None


class UserTierUpdate(BaseModel):
    user_id: str
    tier: str          # free / pro / enterprise
    valid_until: Optional[str] = None  # ISO-дата


# ================ Проверка прав ================

def verify_admin(user_id: str = Query(..., description="ID пользователя")):
    tier = get_user_tier(user_id)
    if tier != "enterprise":
        raise HTTPException(status_code=403, detail="Доступ запрещён. Требуется Enterprise-подписка.")
    return user_id


# ================ Дашборд ================

@router.get("/dashboard")
def admin_dashboard(admin_id: str = Depends(verify_admin)):
    """Сводка по системе."""
    import database
    conn = database.get_conn()
    total_codes   = conn.execute("SELECT COUNT(*) as cnt FROM error_codes").fetchone()["cnt"]
    total_users   = conn.execute("SELECT COUNT(*) as cnt FROM paid_users").fetchone()["cnt"]
    total_diags   = conn.execute("SELECT COUNT(*) as cnt FROM diagnostics").fetchone()["cnt"]
    total_hist    = conn.execute("SELECT COUNT(*) as cnt FROM historical_codes").fetchone()["cnt"]
    conn.close()
    return {
        "total_error_codes": total_codes,
        "total_users": total_users,
        "total_diagnostics": total_diags,
        "total_historical_codes": total_hist,
        "chroma_cases": _chroma_count(),
    }


# ================ Коды ошибок ================

@router.get("/codes")
def list_codes(
    category: Optional[str] = None,
    severity: Optional[str] = None,
    russian_only: bool = False,
    gas_only: bool = False,
    limit: int = 100,
    admin_id: str = Depends(verify_admin),
):
    """Получить список кодов ошибок с фильтрацией."""
    import database
    conn = database.get_conn()
    q = "SELECT * FROM error_codes WHERE 1=1"
    params = []
    if category:
        q += " AND category = ?"
        params.append(category)
    if severity:
        q += " AND severity = ?"
        params.append(severity)
    if russian_only:
        q += " AND russian_cars_only = 1"
    if gas_only:
        q += " AND gas_equipment = 1"
    q += " ORDER BY code LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return {"count": len(rows), "codes": rows}


@router.put("/codes/update")
def update_code(update: CodeUpdate, admin_id: str = Depends(verify_admin)):
    """Обновить данные кода ошибки."""
    import database
    conn = database.get_conn()
    existing = conn.execute("SELECT * FROM error_codes WHERE code = ?", (update.code.upper(),)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Код {update.code} не найден")

    fields = []
    values = []
    for field in ["description", "severity", "recommendations", "russian_cars_only", "gas_equipment"]:
        val = getattr(update, field, None)
        if val is not None:
            fields.append(f"{field} = ?")
            values.append(val)
    if not fields:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")

    values.append(update.code.upper())
    conn.execute(f"UPDATE error_codes SET {', '.join(fields)} WHERE code = ?", values)
    conn.commit()
    conn.close()
    return {"status": "updated", "code": update.code}


# ================ История диагностик ================

@router.get("/history")
def admin_history(limit: int = 100, admin_id: str = Depends(verify_admin)):
    """Получить всю историю диагностик."""
    return {"diagnostics": get_all_history(limit)}


@router.get("/stats")
def admin_stats(admin_id: str = Depends(verify_admin)):
    """Статистика по ошибкам."""
    return {"error_stats": get_error_stats()}


@router.get("/historical")
def admin_historical(
    car_brand: Optional[str] = None,
    mode: Optional[str] = None,
    admin_id: str = Depends(verify_admin),
):
    """Исторические коды (03/07/0A) с частотностью."""
    return {"historical_codes": get_historical_codes(car_brand, mode)}


# ================ Пользователи ================

@router.get("/users")
def list_users(admin_id: str = Depends(verify_admin)):
    """Список пользователей с подписками."""
    import database
    conn = database.get_conn()
    users = conn.execute("SELECT * FROM paid_users ORDER BY id").fetchall()
    conn.close()
    return {"users": users}


@router.put("/users/tier")
def update_user_tier(update: UserTierUpdate, admin_id: str = Depends(verify_admin)):
    """Изменить уровень подписки пользователя."""
    set_user_tier(update.user_id, update.tier, update.valid_until)
    return {"status": "updated", "user_id": update.user_id, "tier": update.tier}


# ================ Автообновление ================

@router.post("/auto-update")
def trigger_auto_update(admin_id: str = Depends(verify_admin)):
    """Запустить автообновление базы кодов."""
    updated = auto_update_codes()
    return {"status": "ok", "updated_count": updated}


# ================ Вспомогательные ================

def _chroma_count() -> int:
    try:
        from chroma_memory import chroma
        return chroma.count()
    except Exception:
        return -1
