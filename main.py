"""
AutoDiag AI v1.0.10 — Главный модуль
CarDiagnosticAI: ИИ-диагностика автомобилей с поддержкой ELM327,
офлайн-базы SQLite, самообучения ChromaDB и облачной синхронизации.

Версия: 1.0.10 (WAF-bypass fix)
"""

from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from typing import Optional
import httpx
import os
import asyncio
import threading
import time
from datetime import datetime, timezone

# ==================== Собственные модули ====================

import database as db
from database import (
    lookup_error, lookup_errors_batch,
    save_diagnosis, get_history, get_all_history, get_error_stats,
    save_historical_code, get_historical_codes,
    get_user_tier, get_user_features,
    lookup_ai_cache, save_ai_cache, check_ai_rate_limit, get_ai_rate_limit_remaining,
)
from elm327 import SimulatedELM327
from simulator import SimulatorState, RUSSIAN_CARS
from chroma_memory import chroma
from live import collector

# Глобальный симулятор (обёртка для потокобезопасной замены)
class _SimRef:
    def __init__(self):
        self._inst = SimulatorState()

    def get(self):
        return self._inst

    def set(self, inst):
        self._inst = inst


sim_ref = _SimRef()
simulator = sim_ref.get()  # начальный экземпляр
from schemas import (
    _SCHEMAS,
    get_schema as get_schema_data,
    get_schema_or_upgrade,
    list_available_schemas,
    render_schema_svg,
    downloader_get_schema,
    get_download_stats,
    refresh_all_schemas,
)
from sync import cloud
import logging

logger = logging.getLogger("autodiag")
from pricing import router as pricing_router, require_feature, is_paid, get_paid_features
from license import router as license_router
from admin import router as admin_router

# ==================== Защита от взлома ====================

import integrity
from device import get_device_id, verify_device_binding

# Глобальный флаг: приложение скомпрометировано?
_APP_COMPROMISED = False
_APP_TAMPER_MODE = "normal"  # normal | free_only | shutdown

# ==================== Защита ====================

from security import (
    SecurityHeadersMiddleware, BodySizeMiddleware,
    CloudflareMiddleware, WAFBypassMiddleware, DiagnoseWAFShield,
    general_limiter, ai_limiter, auth_limiter, download_limiter,
    sanitize_error_code, sanitize_vin, sanitize_car_brand,
    sanitize_user_id, sanitize_text,
    safe_error_message, log_request, get_cors_origins,
    detect_debugger,
)

# ==================== Обновления ====================

from updater import POLL_INTERVAL, UPDATE_SERVER, start_polling

# ==================== Фоновый агент ====================

from weekly_agent import MIN_RUN_INTERVAL


def _require_enterprise(user_id: str):
    """Требовать Enterprise-подписку. 402 при несоответствии."""
    if _APP_COMPROMISED:
        raise HTTPException(status_code=402, detail={
            "error": "integrity_failure",
            "feature": "basic_simulator",
            "message": "Целостность приложения нарушена. Платные функции недоступны.",
        })
    tier = get_user_tier(user_id)
    if tier != "enterprise":
        raise HTTPException(status_code=402, detail={
            "error": "payment_required",
            "feature": "basic_simulator",
            "message": "Симулятор доступен только в версии Enterprise (1 990 ₽/мес).",
        })


def _require_paid(user_id: str):
    """Требовать платную подписку (Pro или Enterprise)."""
    if _APP_COMPROMISED:
        raise HTTPException(status_code=402, detail={
            "error": "integrity_failure",
            "message": "Целостность приложения нарушена. Платная подписка недоступна.",
        })
    if not is_paid(user_id):
        raise HTTPException(status_code=402, detail={
            "error": "payment_required",
            "message": "Требуется платная подписка (Pro или Enterprise).",
        })

class UTF8JSONResponse(JSONResponse):
    """JSONResponse с явным charset=utf-8 — для корректного чтения UTF-8 на клиентах."""
    media_type = "application/json; charset=utf-8"


# ==================== FastAPI App ====================

app = FastAPI(
    title="AutoDiag AI",
    description="ИИ-диагностика автомобилей. ELM327 + DeepSeek + ChromaDB + Облако.",
    version="1.0.11",
    default_response_class=UTF8JSONResponse,
)

# CORS — только доверенные origins (можно переопределить через CORS_ORIGINS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-Timestamp", "X-Signature"],
    max_age=600,
)

# 1. Cloudflare: OPTIONS preflight, CF headers, UA detection
app.add_middleware(CloudflareMiddleware)

# 2. Diagnose WAF shield: полностью отключает WAF-проверки на /diagnose
app.add_middleware(DiagnoseWAFShield)

# 3. WAF bypass: извлекает JSON из base64/query/form-data/text-plain ДО эндпоинтов
app.add_middleware(WAFBypassMiddleware)

# 4. Security headers: CSP, HSTS, X-Content-Type-Options etc.
app.add_middleware(SecurityHeadersMiddleware)

# Ограничение размера тела запроса (100 KB)
app.add_middleware(BodySizeMiddleware)

# ==================== Exception Handlers (Cloudflare-friendly) ====================

@app.exception_handler(403)
async def waf_blocked_handler(request: Request, exc: HTTPException):
    """Обработчик 403: подсказка клиенту, что делать при блокировке."""
    return UTF8JSONResponse(
        status_code=403,
        content={
            "error": "forbidden",
            "detail": str(exc.detail) if exc.detail else "Доступ запрещён",
            "hint": "Используйте GET /diagnose?error_code=...&car_brand=... вместо POST.",
            "cf_ray": request.headers.get("CF-Ray", ""),
        },
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@app.exception_handler(429)
async def rate_limit_handler(request: Request, exc: HTTPException):
    """Обработчик 429: подсказка по rate limiting."""
    retry_after = 60
    if exc.detail and isinstance(exc.detail, dict):
        retry_after = exc.detail.get("retry_after", 60)
    return UTF8JSONResponse(
        status_code=429,
        content={
            "error": "rate_limited",
            "detail": str(exc.detail) if exc.detail else "Слишком много запросов",
            "retry_after_seconds": retry_after,
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Reset": str(int(time.time() + retry_after)),
        },
    )


@app.exception_handler(422)
async def unprocessable_entity_handler(request: Request, exc):
    """
    Обработчик 422: если DiagnoseWAFShield уже распарсил тело через WAF-bypass,
    игнорируем ошибку валидации Pydantic и возвращаем офлайн-диагностику.
    """
    path = request.url.path.rstrip("/")
    params = getattr(request.state, "diagnose_params", None)
    if path == "/diagnose" and params and params.get("error_code"):
        error_code = params.get("error_code", "")
        car_brand = params.get("car_brand", "")
        car_model = params.get("car_model", "")
        vin = params.get("vin", "")
        user_id = params.get("user_id", "anonymous")
        log_request(request, user_id)
        return _offline_diagnose(error_code, car_brand, car_model, vin, user_id)
    # Для других путей — пробрасываем 422 как обычно
    return UTF8JSONResponse(
        status_code=422,
        content={"detail": exc.errors() if hasattr(exc, "errors") else str(exc)},
    )


@app.exception_handler(402)
async def payment_required_handler(request: Request, exc: HTTPException):
    """Обработчик 402: платная функция недоступна."""
    return UTF8JSONResponse(
        status_code=402,
        content=exc.detail if isinstance(exc.detail, dict) else {
            "error": "payment_required",
            "detail": str(exc.detail) if exc.detail else "Требуется платная подписка",
        },
        headers={"X-Upgrade-URL": "/pricing/plans"},
    )

# Монтируем роутеры
app.include_router(pricing_router)
app.include_router(license_router)
app.include_router(admin_router)

# ==================== Обновления ====================

# Эндпоинты обновлений
@app.get("/updates/check")
async def updates_check(user_id: str = Query(default="admin")):
    """Проверить наличие обновлений."""
    from updater import check_for_updates
    updates = await check_for_updates()
    return {
        "available": len(updates),
        "updates": [{"type": u.type, "version": u.version,
                      "description": u.description, "urgent": u.urgent}
                     for u in updates],
    }


@app.post("/updates/apply")
async def updates_apply(user_id: str = Query(default="admin")):
    """Применить все доступные обновления."""
    from updater import check_for_updates, apply_updates
    _require_enterprise(user_id)
    updates = await check_for_updates()
    if not updates:
        return {"status": "ok", "message": "No updates available", "applied": 0}
    result = await apply_updates(updates)
    return result


@app.post("/updates/webhook")
async def updates_webhook(request: Request):
    """
    Приём вебхука с обновлениями от внешней системы.
    Требуется заголовок X-Update-Signature с HMAC подписью.
    """
    signature = request.headers.get("X-Update-Signature", "")
    if not signature:
        raise HTTPException(status_code=401, detail={"error": "missing_signature"})

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "invalid_json"})

    from updater import process_webhook
    result = await process_webhook(body, signature)
    return result


@app.get("/updates/client-check")
async def updates_client_check(
    seq: int = Query(default=0, description="Глобальный sequence-номер последнего применённого обновления клиента"),
    app_version: str = Query(default="1.0.10"),
):
    """
    Проверка обновлений для мобильного клиента.
    seq=0 → новый клиент, получает все обновления.
    seq=N → возвращаются только обновления с seq > N.

    Пример ответа:
    {
      "available": 2,
      "updates": [
        {"seq": 15, "type": "error_codes", "version": 1700000000,
         "payload": {"codes": [...]}},
        ...
      ],
      "server_seq": 17,
      ...
    }
    """
    from updater import get_client_updates
    return get_client_updates(since_seq=seq)


@app.get("/updates/status")
def updates_status():
    """Текущее состояние системы обновлений."""
    from updater import get_current_version, POLL_INTERVAL, UPDATE_SERVER, AUTO_APPLY_DB, AUTO_APPLY_CODE
    ver = get_current_version()
    return {
        "app_version": ver.get("version"),
        "build": ver.get("build"),
        "codename": ver.get("codename"),
        "update_server": UPDATE_SERVER,
        "poll_interval_seconds": POLL_INTERVAL,
        "auto_apply_db": AUTO_APPLY_DB,
        "auto_apply_code": AUTO_APPLY_CODE,
        "device_id": _get_device_id_safe(),
    }


def _get_device_id_safe() -> str:
    try:
        from device import get_device_id
        return get_device_id()
    except Exception:
        return "unavailable"


# ==================== Фоновый агент ====================

# Эндпоинты weekly agent
@app.get("/agent/status")
def agent_status():
    """Состояние фонового агента."""
    from weekly_agent import get_agent
    agent = get_agent()
    state = agent.state
    return {
        "last_run": datetime.fromtimestamp(state.last_run, tz=timezone.utc).isoformat()
                     if state.last_run else None,
        "total_runs": state.total_runs,
        "total_found": state.total_found,
        "last_result": state.last_result,
        "next_run_in_seconds": max(0, int(
            MIN_RUN_INTERVAL - (time.time() - state.last_run)
        )) if state.last_run else 0,
    }


@app.post("/agent/run")
async def agent_run(user_id: str = Query(default="admin"), force: bool = Query(default=False)):
    """Запустить фонового агента вручную."""
    _require_enterprise(user_id)
    from weekly_agent import get_agent
    agent = get_agent()
    result = await agent.run(force=force)
    return result


# ==================== Конфигурация ====================

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

# Симулятор ELM (для тестов без реального адаптера)
elm = SimulatedELM327()

# Фоновая симуляция двигателя
_sim_thread = None

# ==================== Модели запросов ====================

class DiagnoseRequest(BaseModel):
    error_code: str
    car_brand: str
    car_model: Optional[str] = None
    context: Optional[str] = None
    vin: Optional[str] = None

    @field_validator("error_code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        return sanitize_error_code(v)

    @field_validator("car_brand")
    @classmethod
    def validate_brand(cls, v: str) -> str:
        return sanitize_car_brand(v)

    @field_validator("vin")
    @classmethod
    def validate_vin(cls, v: Optional[str]) -> Optional[str]:
        return sanitize_vin(v) if v else v

    @field_validator("car_model", "context")
    @classmethod
    def validate_text(cls, v: Optional[str]) -> Optional[str]:
        return sanitize_text(v, 200) if v else v

class MemoryCaseRequest(BaseModel):
    error_code: str
    car_brand: str
    diagnosis: str
    solution: str

    @field_validator("error_code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        return sanitize_error_code(v)

    @field_validator("car_brand")
    @classmethod
    def validate_brand(cls, v: str) -> str:
        return sanitize_car_brand(v)

    @field_validator("diagnosis", "solution")
    @classmethod
    def validate_text(cls, v: str) -> str:
        return sanitize_text(v, 2000) if v else v

class InjectRequest(BaseModel):
    code: str
    mode: str = "current"  # current / pending / permanent

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        return sanitize_error_code(v)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("current", "pending", "permanent"):
            raise HTTPException(status_code=400, detail={"error": "validation_failed", "field": "mode", "message": "Допустимые значения: current, pending, permanent"})
        return v

# ==================== Глобальный обработчик ошибок ====================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Безопасный обработчик ошибок — не раскрывает стектрейс."""
    if isinstance(exc, HTTPException):
        return UTF8JSONResponse(
            status_code=exc.status_code,
            content=exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)},
        )
    safe_msg = safe_error_message(exc)
    import logging
    logging.getLogger("autodiag").error(f"Unhandled error: {safe_msg}", exc_info=True)
    return UTF8JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "Внутренняя ошибка сервера. Попробуйте позже."},
    )


# ==================== События приложения ====================

@app.on_event("startup")
async def startup():
    """Инициализация при старте."""
    global _APP_COMPROMISED, _APP_TAMPER_MODE

    # Переключить stdout на UTF-8 для эмодзи
    import io, sys
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    print("🚗 AutoDiag AI v1.0 запускается...")
    print(f"   ChromaDB:  {'✅ доступна' if chroma.available else '⚠️  недоступна'}")
    print(f"   SQLite:    ✅ {db.DB_PATH}")
    print(f"   CORS:      {get_cors_origins()}")
    print(f"   Security:  rate limiting + headers + input validation")

    # Проверка целостности
    ok, mode, reason = integrity.check_on_startup()
    if mode == "shutdown":
        _APP_COMPROMISED = True
        _APP_TAMPER_MODE = "shutdown"
        print(f"   🔴 ЦЕЛОСТНОСТЬ НАРУШЕНА: {reason}")
        print(f"   ⛔ КРИТИЧЕСКОЕ НАРУШЕНИЕ — завершение работы.")
        import sys
        sys.exit(1)
    elif mode == "free_only":
        _APP_COMPROMISED = True
        _APP_TAMPER_MODE = "free_only"
        print(f"   🟡 ЦЕЛОСТНОСТЬ НАРУШЕНА: {reason}")
        print(f"   ⚠️  Приложение работает в режиме FREE-ONLY.")
    else:
        print(f"   Integrity: ✅ OK")

    # Device ID
    dev_id = get_device_id()
    print(f"   Device:    {dev_id}")

    # Анти-отладка
    if detect_debugger():
        _APP_COMPROMISED = True
        _APP_TAMPER_MODE = "free_only"
        print(f"   ⚠️  Обнаружен отладчик! Free-only режим.")

    # Запускаем фоновый тик симулятора
    global _sim_thread
    _sim_thread = threading.Thread(target=_sim_loop, daemon=True)
    _sim_thread.start()

    # Запуск фонового опроса обновлений
    start_polling()
    print(f"   Updates:   polling every {POLL_INTERVAL}s → {UPDATE_SERVER}" if POLL_INTERVAL > 0
          else "   Updates:   polling disabled")

    # Фоновое обновление кэша для клиентов
    from updater import start_background_fetcher as _start_bg_fetcher
    _ = asyncio.create_task(_start_bg_fetcher())
    from updater import refresh_update_cache as _refresh
    _ = asyncio.create_task(_refresh())  # первичное наполнение кэша
    print(f"   ClientCache: auto-refresh every 300s")

    # Запуск фонового еженедельного агента
    _agent_thread = threading.Thread(target=_weekly_agent_loop, daemon=True)
    _agent_thread.start()
    print(f"   Agent:     weekly background search active")


def _weekly_agent_loop():
    """Фоновый цикл еженедельного агента."""
    import asyncio as _asyncio
    import time as _time

    # Первый запуск через 10 минут после старта
    _time.sleep(600)

    while True:
        try:
            from weekly_agent import get_agent as _get_agent
            agent = _get_agent()
            loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(loop)
            result = loop.run_until_complete(agent.run())
            loop.close()

            info = (
                f"codes: {result.get('error_codes',{}).get('stored',0)}, "
                f"schemas: {result.get('schemas',{}).get('new_schemas_found',0)}, "
                f"repairs: {result.get('repairs',{}).get('updated',0)}"
            ) if result.get("status") == "completed" else result.get("reason", "")
            print(f"   [WEEKLY] {result.get('status')}: {info}")
        except Exception as e:
            print(f"   [WEEKLY] Error: {e}")

        # Следующий запуск через 7 дней
        _time.sleep(MIN_RUN_INTERVAL)


@app.on_event("shutdown")
async def shutdown():
    """Очистка при выключении."""
    print("=== AutoDiag AI stopped ===")


def _sim_loop():
    """Фоновый цикл симуляции двигателя. Использует sim_ref для потокобезопасности."""
    while True:
        try:
            s = sim_ref.get()
            s.tick()
            live_data = s.get_live_data()
            collector.add_sample(live_data)
            s.generate_natural_errors()
        except Exception:
            pass
        time.sleep(1)


# ==================== Root ====================

@app.get("/")
async def root():
    return {
        "status": "ok",
        "product": "AutoDiag AI",
        "version": "1.0.12",
        "message": "Сервер работает. Агент готов.",
        "endpoints": {
            "simulator": "/sim/live, /sim/errors",
            "live_data": "/live, /live/graph",
            "errors": "/errors, /errors/03, /errors/07, /errors/0A, /errors/clear",
            "diagnose": "/diagnose (POST), /diagnose/offline",
            "history": "/history",
            "memory": "/memory/search, /memory/add, /memory/count",
            "schemas": "/schemas/{code}, /schemas/{code}/image",
            "sync": "/sync/status",
            "cars": "/cars",
            "pricing": "/pricing/plans, /pricing/features, /pricing/status",
            "admin": "/admin/*",
        },
        "chroma_available": chroma.available,
    }


# ==================== Симулятор (Enterprise) ====================

@app.get("/sim/live")
def sim_live(request: Request, user_id: str = Query(default="anonymous", description="ID пользователя")):
    """Базовый симулятор — только Enterprise."""
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    _require_enterprise(user_id)
    data = simulator.get_live_data()
    return {
        "rpm": data["rpm"],
        "speed": data["speed"],
        "coolant_temp": data["coolant_temp"],
        "maf": data["maf"],
    }


@app.get("/sim/errors")
def sim_errors(request: Request, user_id: str = Query(default="anonymous", description="ID пользователя")):
    """Базовый симулятор ошибок — только Enterprise."""
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    _require_enterprise(user_id)
    codes = simulator.get_codes()
    errors = codes["current"] + codes["pending"]
    if not errors:
        errors = ["P0171", "P0300"]  # заглушка
    result = []
    for code in set(errors):
        info = lookup_error(code)
        result.append({
            "code": code,
            "desc": info["description"] if info else "Неизвестная ошибка",
        })
    return result


# ==================== Живые данные ====================

@app.get("/live")
def live_data(request: Request, user_id: str = Query(default="anonymous", description="ID пользователя")):
    """Текущие живые данные с датчиков (из симулятора или ELM327). Pro+."""
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    _require_paid(user_id)
    return simulator.get_live_data()


@app.get("/live/graph")
def live_graph_data(request: Request, user_id: str = Query(default="anonymous", description="ID пользователя")):
    """Данные для графиков (Chart.js-совместимый формат). Pro+."""
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    _require_paid(user_id)
    return collector.get_graph_data()


# ==================== Чтение ошибок (ELM327) ====================

@app.get("/errors")
def read_errors():
    """Прочитать ошибки: текущие, pending, перманентные."""
    codes = simulator.get_codes()
    # Расшифровать коды через офлайн-базу
    all_codes = set(codes["current"] + codes["pending"] + codes["permanent"])
    decoded = {}
    if all_codes:
        rows = lookup_errors_batch(list(all_codes))
        decoded = {r["code"]: r for r in rows}

    def enrich(code_list):
        return [{"code": c, "info": decoded.get(c)} for c in code_list]

    return {
        "check_engine": codes["check_engine"],
        "current":   enrich(codes["current"]),
        "pending":   enrich(codes["pending"]),
        "permanent": enrich(codes["permanent"]),
    }


@app.get("/errors/03")
def errors_mode_03():
    """Режим 03 — текущие подтверждённые DTC."""
    codes = simulator.get_codes()["current"]
    decoded = {}
    if codes:
        rows = lookup_errors_batch(codes)
        decoded = {r["code"]: r for r in rows}
    return {
        "mode": "03",
        "description": "Подтверждённые коды неисправностей",
        "codes": [{"code": c, "info": decoded.get(c)} for c in codes],
    }


@app.get("/errors/07")
def errors_mode_07():
    """Режим 07 — ожидающие (pending) DTC."""
    codes = simulator.get_codes()["pending"]
    decoded = {}
    if codes:
        rows = lookup_errors_batch(codes)
        decoded = {r["code"]: r for r in rows}
    return {
        "mode": "07",
        "description": "Ожидающие коды (pending)",
        "codes": [{"code": c, "info": decoded.get(c)} for c in codes],
    }


@app.get("/errors/0A")
def errors_mode_0A():
    """Режим 0A — перманентные DTC."""
    codes = simulator.get_codes()["permanent"]
    decoded = {}
    if codes:
        rows = lookup_errors_batch(codes)
        decoded = {r["code"]: r for r in rows}
    return {
        "mode": "0A",
        "description": "Перманентные коды",
        "codes": [{"code": c, "info": decoded.get(c)} for c in codes],
    }


@app.post("/errors/clear")
def clear_errors(user_id: str = Query(default="anonymous")):
    """Сбросить ошибки (режим 04)."""
    simulator.clear_codes()
    collector.clear()
    return {"status": "cleared", "message": "Ошибки сброшены. Живые данные очищены."}


@app.post("/errors/inject")
def inject_error(request: Request, body: InjectRequest,
                 user_id: str = Query(default="anonymous", description="ID пользователя")):
    """Инжектировать ошибку в симулятор для теста. Enterprise only."""
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    _require_enterprise(user_id)
    simulator.inject_code(body.code, body.mode)
    return {
        "status": "injected",
        "code": body.code,
        "mode": body.mode,
    }


# ==================== Диагностика ====================

def _extract_diagnose_params(http_request: Request, pydantic_request=None):
    """Извлечь параметры диагностики из request.state (WAF-обход) или Pydantic-модели."""
    params = getattr(http_request.state, "diagnose_params", None)
    if params:
        return (
            params.get("error_code", ""),
            params.get("car_brand", ""),
            params.get("car_model", ""),
            params.get("vin", ""),
            params.get("context", ""),
            params.get("user_id", "anonymous"),
        )
    if pydantic_request:
        return (
            pydantic_request.error_code,
            pydantic_request.car_brand,
            pydantic_request.car_model or "",
            pydantic_request.vin or "",
            pydantic_request.context or "",
            "anonymous",
        )
    return ("", "", "", "", "", "anonymous")


@app.post("/diagnose")
async def diagnose(http_request: Request, user_id: str = Query(default="anonymous")):
    """
    AI-диагностика через DeepSeek.
    Принимает параметры из request.state (DiagnoseWAFShield) или JSON-тела.
    Требуется платная подписка (Pro/Enterprise).
    """
    # Параметры из DiagnoseWAFShield (приоритет)
    params = getattr(http_request.state, "diagnose_params", None)
    if not params:
        # Фолбэк: читаем JSON-тело вручную (не через Pydantic, чтобы избежать 422)
        try:
            body = await http_request.body()
            if body:
                import json as _json
                data = _json.loads(body)
                params = {
                    "error_code": str(data.get("error_code", "")),
                    "car_brand": str(data.get("car_brand", "")),
                    "car_model": str(data.get("car_model", "")),
                    "vin": str(data.get("vin", "")),
                    "context": str(data.get("context", "")),
                    "user_id": str(data.get("user_id", "anonymous")),
                }
        except Exception:
            params = {}

    error_code = params.get("error_code", "")
    car_brand = params.get("car_brand", "")
    car_model = params.get("car_model", "")
    vin = params.get("vin", "")
    context = params.get("context", "")
    user_id = params.get("user_id", user_id)

    ai_limiter.is_allowed(http_request)
    log_request(http_request, user_id)

    # Периодическая проверка целостности (раз в 30 мин)
    integrity.periodic_check_if_needed()

    # Проверка подписки
    if not is_paid(user_id):
        # Возвращаем офлайн-диагностику для бесплатных
        return _offline_diagnose(error_code, car_brand,
                                 car_model, vin, user_id)

    if not DEEPSEEK_API_KEY:
        # Fallback на офлайн если API-ключ не настроен
        return _offline_diagnose(error_code, car_brand,
                                 car_model, vin, user_id,
                                 note="⚠️ AI-ключ не настроен. Использована офлайн-база.")

    # ── Кеш AI-ответов (пропускаем DeepSeek при повторе) ──
    cached = lookup_ai_cache(error_code, car_brand, car_model or "")
    if cached:
        diag_id = save_diagnosis(
            user_id=user_id,
            error_code=error_code,
            car_brand=car_brand,
            car_model=car_model or "",
            vin=vin or "",
            diagnosis=cached["diagnosis"],
            source="ai-cache",
        )
        import json as _json
        return {
            "error_code": error_code,
            "diagnosis": cached["diagnosis"],
            "causes": _json.loads(cached.get("causes", "[]")),
            "solutions": _json.loads(cached.get("solutions", "[]")),
            "severity": cached.get("severity", "medium"),
            "source": "ai-cache",
            "diagnosis_id": diag_id,
            "cached": True,
        }

    # ── Rate limit: 20 AI-запросов в час на пользователя ──
    if not check_ai_rate_limit(user_id):
        return _offline_diagnose(error_code, car_brand,
                                  car_model, vin, user_id,
                                  note="⚠️ Превышен лимит AI-запросов (20/час). Попробуйте позже.")

    # ── Оптимизированный промпт (короче = меньше токенов) ──
    car_info = f"{car_brand} {car_model}".strip() if car_model else car_brand
    prompt = (
        f"Ошибка {error_code} на {car_info}."
        + (f" VIN:{vin}." if vin else "")
        + (f" Контекст:{context}." if context else "")
        + " Дай диагноз и решения. JSON: {\"diagnosis\":\"...\",\"causes\":[...],\"solutions\":[...],\"severity\":\"...\"}"
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "Ты механик по российским авто (Lada, ГАЗ, УАЗ). Отвечай кратко, JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 500,
                    "response_format": {"type": "json_object"},
                },
            )
        data = resp.json()
        ai_result = data["choices"][0]["message"]["content"]

        # Парсим JSON из ответа
        import json
        try:
            parsed = json.loads(ai_result)
        except json.JSONDecodeError:
            parsed = {"diagnosis": ai_result, "causes": [], "solutions": []}

        diagnosis_text = parsed.get("diagnosis", ai_result)
        causes = parsed.get("causes", [])
        solutions = parsed.get("solutions", [])
        severity = parsed.get("severity", "medium")

        # Сохраняем в AI-кеш для будущих запросов
        save_ai_cache(error_code, car_brand, car_model or "",
                      diagnosis_text, causes, solutions, severity)

        # Сохраняем в историю
        diag_id = save_diagnosis(
            user_id=user_id,
            error_code=error_code,
            car_brand=car_brand,
            car_model=car_model or "",
            vin=vin or "",
            diagnosis=diagnosis_text,
            source="ai",
        )

        # Сохраняем в ChromaDB (самообучение)
        if chroma.available:
            chroma.add_case(
                error_code=error_code,
                car_brand=car_brand,
                diagnosis=diagnosis_text,
                solution="; ".join(solutions),
                user_id=user_id,
            )

        # Синхронизируем в облако (если платный пользователь)
        if is_paid(user_id):
            await cloud.push_diagnosis(
                user_id=user_id,
                error_code=error_code,
                car_brand=car_brand,
                diagnosis=diagnosis_text,
                solution="; ".join(solutions),
            )

        # Исторический код
        save_historical_code(error_code, "03", car_brand, car_model)

        return {
            "error_code": error_code,
            "diagnosis": diagnosis_text,
            "causes": causes,
            "solutions": solutions,
            "severity": severity,
            "source": "deepseek",
            "diagnosis_id": diag_id,
            "rate_limit_remaining": get_ai_rate_limit_remaining(user_id),
        }

    except Exception as e:
        # Fallback на офлайн при ошибке AI (безопасное сообщение — без ключей)
        return _offline_diagnose(error_code, car_brand,
                                 car_model, vin, user_id,
                                 note=f"⚠️ Ошибка AI. Использована офлайн-база.")


def _offline_diagnose(error_code: str, car_brand: str, car_model: str = None,
                      vin: str = None, user_id: str = "anonymous",
                      note: str = None) -> dict:
    """Офлайн-диагностика по локальной базе SQLite."""
    info = lookup_error(error_code)
    if info:
        diag_id = save_diagnosis(user_id, error_code, car_brand, car_model or "",
                                 vin or "", info["description"], "offline")
        save_historical_code(error_code, "03", car_brand, car_model)
        return {
            "error_code": error_code,
            "diagnosis": info["description"],
            "causes": [],
            "solutions": info.get("recommendations", "").split("; ") if info.get("recommendations") else [],
            "severity": info.get("severity", "medium"),
            "source": "offline",
            "diagnosis_id": diag_id,
            "category": info.get("category"),
            "russian_cars_only": bool(info.get("russian_cars_only")),
            "gas_equipment": bool(info.get("gas_equipment")),
            "note": note,
        }
    else:
        return {
            "error_code": error_code,
            "diagnosis": f"Код {error_code} не найден в офлайн-базе.",
            "causes": [],
            "solutions": ["Проверить код в специализированном справочнике."],
            "severity": "unknown",
            "source": "offline",
            "diagnosis_id": None,
            "note": note or "Код отсутствует в локальной базе.",
        }


@app.get("/diagnose/offline")
def offline_lookup(request: Request, code: str = Query(..., description="Код ошибки")):
    """Быстрый офлайн-поиск кода ошибки."""
    general_limiter.is_allowed(request)
    log_request(request)
    code = sanitize_error_code(code)
    info = lookup_error(code)
    if info:
        return {"found": True, "data": info}
    return {"found": False, "message": f"Код {code} не найден."}


# ==================== История диагностик ====================

@app.get("/history")
def diagnostic_history(
    request: Request,
    user_id: str = Query(default="anonymous"),
    limit: int = Query(default=50, le=200),
):
    """История диагностик пользователя."""
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    rows = get_history(user_id, limit)
    return {"user_id": user_id, "count": len(rows), "diagnostics": rows}


@app.get("/history/stats")
def history_stats():
    """Статистика по самым частым ошибкам."""
    return {"stats": get_error_stats()}


@app.get("/history/codes")
def historical_codes_analysis(
    car_brand: Optional[str] = None,
    mode: Optional[str] = None,
):
    """Анализ исторических кодов (03/07/0A) с частотностью."""
    return {"historical_codes": get_historical_codes(car_brand, mode)}


# ==================== Самообучение (ChromaDB) ====================

@app.get("/memory/search")
def memory_search(
    request: Request,
    q: str = Query(..., description="Поисковый запрос или код ошибки"),
    n: int = Query(default=5, le=20),
    user_id: str = Query(default="anonymous", description="ID пользователя"),
):
    """Поиск похожих успешных кейсов в памяти. Pro+."""
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    _require_paid(user_id)
    if not chroma.available:
        return {"available": False, "message": "ChromaDB не установлена. Установите: pip install chromadb"}
    results = chroma.search(q, n)
    return {"available": True, "query": q, "count": len(results), "results": results}


@app.post("/memory/add")
def memory_add(
    request: Request,
    body: MemoryCaseRequest,
    user_id: str = Query(default="anonymous"),
):
    """Добавить успешный кейс в память самообучения. Pro+."""
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    _require_paid(user_id)
    if not chroma.available:
        raise HTTPException(status_code=503, detail="ChromaDB недоступна")

    case_id = chroma.add_case(
        error_code=body.error_code,
        car_brand=body.car_brand,
        diagnosis=body.diagnosis,
        solution=body.solution,
        user_id=user_id,
    )
    return {"status": "added", "case_id": case_id}


@app.get("/memory/count")
def memory_count(request: Request, user_id: str = Query(default="anonymous", description="ID пользователя")):
    """Количество записей в памяти ChromaDB. Pro+."""
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    _require_paid(user_id)
    return {"available": chroma.available, "count": chroma.count()}


# ==================== Схемы узлов ====================

@app.get("/schemas")
def list_schemas():
    """Список всех доступных схем узлов."""
    return {"schemas": list_available_schemas(), "total": len(_SCHEMAS)}


@app.post("/schemas/refresh")
async def refresh_schemas(
    request: Request,
    user_id: str = Query(default="admin"),
):
    """
    Запустить пополнение библиотеки — поиск схем для всех кодов.
    Выполняется в фоне; возвращает статус сразу.
    """
    download_limiter.is_allowed(request)
    log_request(request, user_id)

    # Запускаем в фоновой задаче
    import asyncio as _asyncio
    _asyncio.create_task(_background_refresh())

    return {
        "status": "started",
        "message": f"Запущено пополнение библиотеки для {len(_SCHEMAS)} кодов. "
                   f"Это займёт несколько минут. Проверьте /schemas/stats позже.",
        "total_codes": len(_SCHEMAS),
        "codes": list(_SCHEMAS.keys()),
    }


@app.get("/schemas/stats")
def get_schemas_stats():
    """Статистика скачанных изображений схем."""
    return get_download_stats()


@app.get("/schemas/{code}")
def get_schema_endpoint(
    request: Request,
    code: str,
    user_id: str = Query(default="anonymous"),
):
    """
    Получить схему узла по коду ошибки (тестовая версия — бесплатно).
    """
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    code = sanitize_error_code(code)
    # Всегда отдаём полную схему (тестовый режим)
    result = get_schema_or_upgrade(code, is_paid=True)
    if result.get("available"):
        # Добавляем статистику скачанных изображений
        stats = get_download_stats()
        result["data"]["_downloaded_images"] = stats.get("codes", {}).get(code, 0)
    return result


@app.get("/schemas/{code}/image")
def get_schema_image(
    request: Request,
    code: str,
    user_id: str = Query(default="anonymous"),
):
    """
    Получить 2D-изображение схемы в SVG (тестовая версия — бесплатно).
    """
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    code = sanitize_error_code(code)
    result = get_schema_or_upgrade(code, is_paid=True)
    if not result.get("available"):
        return result

    from fastapi.responses import Response
    svg = render_schema_svg(code, result["data"])
    return Response(content=svg, media_type="image/svg+xml; charset=utf-8")


@app.get("/schemas/{code}/download")
async def download_schema(
    request: Request,
    code: str,
    brand: str = Query(default="LADA", description="Марка авто для поиска"),
    user_id: str = Query(default="anonymous"),
):
    """
    Поиск и скачивание реальных схем из интернета (тестовая версия — бесплатно).
    Источники: Bing Images → Google Images → Wikimedia.
    """
    download_limiter.is_allowed(request)
    log_request(request, user_id)
    code = sanitize_error_code(code)
    brand = sanitize_car_brand(brand)

    # Проверяем, есть ли схема в базе
    schema_data = get_schema_data(code)
    description = schema_data.get("description", "") if schema_data else ""

    result = await downloader_get_schema(code, description)
    if result:
        return {
            "code": code,
            "images": result["images"],
            "cached": result["cached"],
            "count": result["count"],
        }
    return {
        "code": code,
        "images": [],
        "cached": False,
        "count": 0,
        "message": "Схемы не найдены. Попробуйте позже — библиотека пополняется ежемесячно.",
    }


async def _background_refresh():
    """Фоновая задача пополнения библиотеки."""
    logger.info("Background schema refresh started")
    try:
        summary = await refresh_all_schemas(_SCHEMAS)
        logger.info(f"Schema refresh done: {summary['success']}/{summary['total']} codes")
    except Exception as exc:
        logger.error(f"Background schema refresh failed: {exc}")


# ==================== Облачная синхронизация ====================

@app.get("/sync/status")
async def sync_status(user_id: str = Query(default="anonymous")):
    """Статус облачной синхронизации."""
    pass  # paywall disabled for testing
    queue = db.get_sync_queue(limit=10)
    return {
        "available": True,
        "queue_size": len(queue),
        "pending_items": queue,
    }


@app.post("/sync/flush")
async def sync_flush(request: Request, user_id: str = Query(default="anonymous")):
    """Принудительная отправка очереди синхронизации."""
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    if not is_paid(user_id):
        raise HTTPException(status_code=402, detail="Требуется платная подписка")
    synced = await cloud.flush_queue()
    return {"status": "ok", "synced": synced}


# ==================== Автомобили ====================

@app.get("/cars")
def list_cars():
    """Список поддерживаемых российских автомобилей, спецтехники, ГБО."""
    return {
        "count": len(RUSSIAN_CARS),
        "cars": [
            {
                "key": key,
                "brand": car["brand"],
                "model": car["model"],
                "year": car["year"],
                "fuel": car["fuel"],
                "gas_equipment": car.get("gas_equipment", False),
                "special": car.get("special", False),
            }
            for key, car in RUSSIAN_CARS.items()
        ]
    }


# ==================== Управление симулятором ====================

@app.post("/simulator/start")
def simulator_start(request: Request, car_key: str = Query(default="lada_vesta"),
                    user_id: str = Query(default="anonymous", description="ID пользователя")):
    """Запустить двигатель симулятора. Enterprise only."""
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    _require_enterprise(user_id)
    if car_key in RUSSIAN_CARS:
        new_sim = SimulatorState(car_key)
        sim_ref.set(new_sim)
        global simulator
        simulator = new_sim
    simulator.start_engine()
    return {"status": "started", "car": simulator.car}


@app.post("/simulator/stop")
def simulator_stop(request: Request, user_id: str = Query(default="anonymous", description="ID пользователя")):
    """Остановить двигатель симулятора. Enterprise only."""
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    _require_enterprise(user_id)
    simulator.stop_engine()
    return {"status": "stopped"}


@app.get("/simulator/state")
def simulator_state(request: Request, user_id: str = Query(default="anonymous", description="ID пользователя")):
    """Получить полное состояние симулятора. Enterprise only."""
    general_limiter.is_allowed(request)
    log_request(request, user_id)
    _require_enterprise(user_id)
    return {
        "car": simulator.car,
        "engine_running": simulator.engine_running,
        "codes": simulator.get_codes(),
        "live": simulator.get_live_data(),
        "injected": simulator._injected,
    }


# ==================== Health Check ====================

@app.get("/health")
def health():
    """Health-check для Render."""
    return {"status": "healthy", "version": "1.0.12"}


# ==================== Статус подписки (быстрый) ====================

@app.get("/me")
def me(user_id: str = Query(default="anonymous")):
    """Информация о текущем пользователе и его подписке."""
    return {
        "user_id": user_id,
        "tier": get_user_tier(user_id),
        "features": get_user_features(user_id),
        "chroma_available": chroma.available,
    }


# ==================== Entry Point ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        server_header=False,
        log_level="warning",
    )

@app.get("/diagnose")
async def diagnose_get(http_request: Request,
                       error_code: str = Query(default="", description="Код ошибки OBD2"),
                       car_brand: str = Query(default="", description="Марка авто"),
                       car_model: str = Query(default="", description="Модель"),
                       vin: str = Query(default="", description="VIN"),
                       context: str = Query(default="", description="Дополнительный контекст"),
                       user_id: str = Query(default="anonymous")):
    """
    AI-диагностика через GET (WAF-safe, для мобильных клиентов).
    Принимает параметры из DiagnoseWAFShield (request.state) или query-параметров.
    """
    # Приоритет: параметры из WAF-shield middleware
    e, b, m, v, c, u = _extract_diagnose_params(http_request)
    error_code = e or error_code
    car_brand = b or car_brand
    car_model = m or car_model
    vin = v or vin
    context = c or context
    user_id = u if u != "anonymous" else user_id
    general_limiter.is_allowed(http_request)
    log_request(http_request, user_id)
    integrity.periodic_check_if_needed()

    if not is_paid(user_id):
        return _offline_diagnose(error_code, car_brand, car_model, vin, user_id)

    if not DEEPSEEK_API_KEY:
        return _offline_diagnose(error_code, car_brand, car_model, vin, user_id,
                                 note="⚠️ AI-ключ не настроен. Использована офлайн-база.")

    # ── Кеш AI-ответов ──
    cached = lookup_ai_cache(error_code, car_brand, car_model or "")
    if cached:
        diag_id = save_diagnosis(user_id=user_id, error_code=error_code, car_brand=car_brand,
                                  car_model=car_model or "", vin=vin or "",
                                  diagnosis=cached["diagnosis"], source="ai-cache")
        import json as _json
        return {
            "error_code": error_code,
            "diagnosis": cached["diagnosis"],
            "causes": _json.loads(cached.get("causes", "[]")),
            "solutions": _json.loads(cached.get("solutions", "[]")),
            "severity": cached.get("severity", "medium"),
            "source": "ai-cache",
            "diagnosis_id": diag_id,
            "cached": True,
        }

    # ── Rate limit ──
    if not check_ai_rate_limit(user_id):
        return _offline_diagnose(error_code, car_brand, car_model, vin, user_id,
                                  note="⚠️ Превышен лимит AI-запросов (20/час).")

    car_info = f"{car_brand} {car_model}".strip() if car_model else car_brand
    prompt = (
        f"Ошибка {error_code} на {car_info}."
        + (f" VIN:{vin}." if vin else "")
        + (f" Контекст:{context}." if context else "")
        + ' Дай диагноз и решения. JSON: {"diagnosis":"...","causes":[...],"solutions":[...],"severity":"..."}'
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "Ты механик по российским авто (Lada, ГАЗ, УАЗ). Отвечай кратко, JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 500,
                    "response_format": {"type": "json_object"},
                },
            )
        data = resp.json()
        ai_result = data["choices"][0]["message"]["content"]
        import json
        try:
            parsed = json.loads(ai_result)
        except json.JSONDecodeError:
            parsed = {"diagnosis": ai_result, "causes": [], "solutions": []}
        diagnosis_text = parsed.get("diagnosis", ai_result)
        causes = parsed.get("causes", [])
        solutions = parsed.get("solutions", [])
        severity = parsed.get("severity", "medium")

        # Кешируем
        save_ai_cache(error_code, car_brand, car_model or "",
                      diagnosis_text, causes, solutions, severity)

        diag_id = save_diagnosis(user_id=user_id, error_code=error_code, car_brand=car_brand,
                                  car_model=car_model, vin=vin, diagnosis=diagnosis_text, source="ai")
        if chroma.available:
            chroma.add_case(error_code=error_code, car_brand=car_brand,
                            diagnosis=diagnosis_text, solution="; ".join(solutions),
                            user_id=user_id)
        if is_paid(user_id):
            await cloud.push_diagnosis(user_id=user_id, error_code=error_code, car_brand=car_brand,
                                        diagnosis=diagnosis_text, solution="; ".join(solutions))
        save_historical_code(error_code, "03", car_brand, car_model)
        return {
            "error_code": error_code,
            "diagnosis": diagnosis_text,
            "causes": causes,
            "solutions": solutions,
            "severity": severity,
            "source": "ai",
            "diagnosis_id": diag_id,
            "rate_limit_remaining": get_ai_rate_limit_remaining(user_id),
        }

    except Exception as e:
        logger.warning(f"AI diagnose failed: {e}")
        return _offline_diagnose(error_code, car_brand, car_model, vin, user_id,
                                 note=f"⚠️ Ошибка AI: {str(e)[:100]}")
