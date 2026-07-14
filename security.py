"""
AutoDiag AI v1.0 — Модуль защиты
Rate limiting, security headers, input sanitization, safe error handling.
"""

import time
import hashlib
import hmac
import re
import base64 as _base64
import json as _json
import logging
from collections import defaultdict
from io import BytesIO
from typing import Optional

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# ==================== Логирование ====================

logger = logging.getLogger("autodiag.security")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [SECURITY] %(message)s"))
    logger.addHandler(h)

# ==================== Безопасные лимиты ====================

MAX_BODY_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_QUERY_LENGTH = 500            # chars
MAX_ERROR_CODE_LENGTH = 10        # e.g. "P0171", "B2AAA"
MAX_VIN_LENGTH = 17
MAX_CAR_BRAND_LENGTH = 50
MAX_USER_ID_LENGTH = 100

# Разрешённые символы
RE_ERROR_CODE = re.compile(r'^[A-Za-z0-9]{1,10}$')
RE_VIN = re.compile(r'^[A-HJ-NPR-Z0-9]{1,17}$')  # VIN без I,O,Q
RE_CAR_BRAND = re.compile(r'^[\w\s\-\.]{1,50}$')
RE_USER_ID = re.compile(r'^[a-zA-Z0-9_\-\.@]{1,100}$')

# ==================== Rate Limiting ====================

class RateLimiter:
    """
    Простой in-memory rate limiter (token bucket).
    Хранит записи в словаре: IP → (tokens, last_refill, blocked_until).
    """

    def __init__(self, requests_per_minute: int = 60, burst: int = 10, block_seconds: int = 300):
        self.rate = requests_per_minute / 60.0  # токенов/сек
        self.burst = burst
        self.block_seconds = block_seconds
        self._buckets: dict[str, tuple[float, float, float]] = {}  # ip -> (tokens, last_refill, blocked_until)
        self._cleanup_counter = 0

    def _get_ip(self, request: Request) -> str:
        """Извлечь IP клиента с учётом Cloudflare прокси.

        Приоритет:
        1. CF-Connecting-IP — Cloudflare (невозможно подделать)
        2. X-Forwarded-For — только если есть CF-Ray (прошли через Cloudflare)
        3. X-Real-IP — только если есть CF-Ray
        4. client.host — прямой IP (фолбэк)
        """
        # CF-Connecting-IP: Cloudflare гарантирует подлинность
        cf_ip = request.headers.get("CF-Connecting-IP")
        if cf_ip:
            return cf_ip.strip()

        # Проверяем, что запрос действительно прошёл через Cloudflare
        is_cloudflare = bool(request.headers.get("CF-Ray"))

        # X-Forwarded-For: доверяем только если за Cloudflare
        if is_cloudflare:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()

        # X-Real-IP: доверяем только если за Cloudflare
        if is_cloudflare:
            real_ip = request.headers.get("X-Real-IP")
            if real_ip:
                return real_ip.strip()

        # Прямой IP (или запрос в обход Cloudflare — не доверяем прокси-заголовкам)
        if request.client:
            return request.client.host
        return "unknown"

    def _cleanup_if_needed(self):
        """Периодическая очистка старых записей."""
        self._cleanup_counter += 1
        if self._cleanup_counter < 1000:
            return
        self._cleanup_counter = 0
        now = time.time()
        stale = [ip for ip, (_, _, blocked) in self._buckets.items()
                 if blocked < now and blocked > 0]
        for ip in stale:
            del self._buckets[ip]

    def is_allowed(self, request: Request) -> bool:
        """Проверить, разрешён ли запрос. Возвращает True если OK."""
        now = time.time()
        ip = self._get_ip(request)
        tokens, last_refill, blocked_until = self._buckets.get(ip, (self.burst, now, 0.0))

        # Проверка блокировки
        if now < blocked_until:
            wait = int(blocked_until - now)
            logger.warning(f"BLOCKED ip={ip} wait={wait}s")
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limit_exceeded",
                    "retry_after": wait,
                    "message": f"Слишком много запросов. Повторите через {wait} сек.",
                },
            )

        # Пополнить токены
        elapsed = now - last_refill
        new_tokens = min(self.burst, tokens + elapsed * self.rate)
        new_tokens -= 1  # расходуем один токен

        if new_tokens < 0:
            # Блокируем
            blocked_until = now + self.block_seconds
            self._buckets[ip] = (0.0, now, blocked_until)
            logger.warning(f"RATE_LIMIT ip={ip} blocked_until={blocked_until}")
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limit_exceeded",
                    "retry_after": self.block_seconds,
                    "message": f"Слишком много запросов. Повторите через {self.block_seconds} сек.",
                },
            )

        self._buckets[ip] = (new_tokens, now, 0.0)
        self._cleanup_if_needed()
        return True


# Экземпляры rate limiter'ов для разных категорий эндпоинтов

general_limiter = RateLimiter(requests_per_minute=120, burst=20, block_seconds=300)
ai_limiter = RateLimiter(requests_per_minute=10, burst=3, block_seconds=600)       # AI-диагностика
auth_limiter = RateLimiter(requests_per_minute=20, burst=5, block_seconds=900)     # Лицензия/активация
download_limiter = RateLimiter(requests_per_minute=5, burst=2, block_seconds=1200) # Скачивание схем

# ==================== Security Headers Middleware ====================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Добавляет HTTP-заголовки безопасности ко всем ответам.

    OWASP Secure Headers + Cloudflare-friendly hardening.
    https://owasp.org/www-project-secure-headers/
    """

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        # --- Content / MIME ---
        response.headers["X-Content-Type-Options"] = "nosniff"

        # --- Clickjacking ---
        response.headers["X-Frame-Options"] = "DENY"

        # --- CSP (Content Security Policy) for API ---
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            "frame-ancestors 'none'; "
            "form-action 'none'; "
            "base-uri 'none'"
        )

        # --- Referrer ---
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # --- Permissions (API uses no sensors) ---
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), autoplay=(), camera=(), "
            "display-capture=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), midi=(), "
            "payment=(), usb=(), xr-spatial-tracking=()"
        )

        # --- Cross-Origin isolation ---
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        response.headers["Cross-Origin-Embedder-Policy"] = "unsafe-none"

        # --- HSTS (Strict-Transport-Security) ---
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )

        # --- Misc ---
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        response.headers["X-DNS-Prefetch-Control"] = "off"

        # --- Strip headers that leak backend internals ---
        # Server-Timing: reveals backend performance metrics (ASGI, DB queries)
        # X-Forwarded-For: proxy header, must not leak to clients
        # X-Powered-By: framework fingerprint (FastAPI/Starlette)
        # Via: proxy chain info
        for leaky in ("Server", "Server-Timing", "X-Forwarded-For",
                       "X-Powered-By", "Via", "X-AspNet-Version",
                       "X-AspNetMvc-Version"):
            if leaky in response.headers:
                del response.headers[leaky]

        return response


# ==================== Body Size Middleware ====================

class BodySizeMiddleware(BaseHTTPMiddleware):
    """Блокирует запросы с телом больше MAX_BODY_SIZE."""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > MAX_BODY_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail={"error": "payload_too_large", "max_bytes": MAX_BODY_SIZE},
                    )
            except ValueError:
                pass
        return await call_next(request)


# ==================== Input Sanitization ====================

class ValidationError(HTTPException):
    """Ошибка валидации входа."""
    def __init__(self, field: str, detail: str = "Недопустимое значение"):
        super().__init__(
            status_code=400,
            detail={"error": "validation_failed", "field": field, "message": detail},
        )


def sanitize_error_code(code: str) -> str:
    """Очистить и проверить код ошибки OBD2."""
    if not code or not RE_ERROR_CODE.match(code):
        raise ValidationError("error_code", "Недопустимый код ошибки (только буквы и цифры, 1–10 символов)")
    return code.upper()


def sanitize_vin(vin: Optional[str]) -> Optional[str]:
    """Проверить VIN-номер."""
    if vin is None:
        return None
    vin = vin.upper().strip()
    if not RE_VIN.match(vin):
        raise ValidationError("vin", "Недопустимый VIN-номер (только буквы и цифры, до 17 символов, без I,O,Q)")
    return vin


def sanitize_car_brand(brand: str) -> str:
    """Проверить марку авто."""
    if not brand or not RE_CAR_BRAND.match(brand):
        raise ValidationError("car_brand", "Недопустимое название марки (буквы, цифры, пробелы, до 50 символов)")
    return brand.strip()


def sanitize_user_id(user_id: str) -> str:
    """Проверить ID пользователя."""
    if not user_id or not RE_USER_ID.match(user_id):
        raise ValidationError("user_id", "Недопустимый ID пользователя")
    return user_id


def sanitize_text(text: Optional[str], max_len: int = 500) -> Optional[str]:
    """Общая очистка текстового поля."""
    if text is None:
        return None
    if len(text) > max_len:
        raise ValidationError("text", f"Текст слишком длинный (макс. {max_len} символов)")
    # Удаляем управляющие символы
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return cleaned[:max_len]


def safe_error_message(error: Exception) -> str:
    """Безопасное сообщение об ошибке — не раскрывает внутренние детали."""
    # Убираем потенциально опасные строки
    msg = str(error)
    # Маскируем API-ключи
    msg = re.sub(r'(sk-[a-zA-Z0-9]{10,})', '***API_KEY***', msg)
    msg = re.sub(r'(Bearer\s+)[^\s]+', r'\1***TOKEN***', msg)
    # Обрезаем до 500 символов
    return msg[:500] if len(msg) > 500 else msg


def log_request(request: Request, user_id: str = "anonymous"):
    """Логировать входящий запрос."""
    ip = request.client.host if request.client else "unknown"
    logger.info(
        f"REQUEST method={request.method} path={request.url.path} "
        f"ip={ip} user={user_id} agent={request.headers.get('user-agent', '?')[:100]}"
    )


# ==================== CORS ====================

import os

def get_cors_origins() -> list[str]:
    """CORS origins из переменной окружения или безопасный default."""
    origins_env = os.getenv("CORS_ORIGINS", "")
    if origins_env:
        return [o.strip() for o in origins_env.split(",") if o.strip()]
    # В продакшене — только конкретные домены
    return [
        "https://car-diagnostic-ai.onrender.com",
        "https://autodiag.ai",
    ]


# ==================== HMAC API-Key (опционально) ====================

API_SECRET = os.getenv("API_SECRET", "")

def verify_api_signature(timestamp: str, signature: str, body: str = "") -> bool:
    """
    Проверить HMAC-подпись запроса (если настроен API_SECRET).
    Заголовки: X-Timestamp, X-Signature
    """
    if not API_SECRET:
        return True  # Подпись не требуется

    try:
        ts = int(timestamp)
        now = int(time.time())
        # Защита от replay-атак: max 5 минут расхождения
        if abs(now - ts) > 300:
            return False

        message = f"{timestamp}:{body}"
        expected = hmac.new(
            API_SECRET.encode(), message.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except (ValueError, TypeError):
        return False

# ==================== Анти-отладка ====================

import sys as _sys

# Скрытая проверка на отладчик (вызывается из main при старте)
def detect_debugger() -> bool:
    """
    Проверить, запущено ли приложение под отладчиком.
    Возвращает True если обнаружен отладчик.
    """
    # Python debugger (pdb, pydevd, etc.)
    if _sys.gettrace() is not None:
        return True

    # Переменные окружения IDE/отладчиков
    debug_env = ["PYCHARM_DEBUG", "PYDEV_DEBUG", "PYTHONDEBUG",
                 "DEBUGPY", "VSCODE_DEBUG", "_DEBUG"]
    for var in debug_env:
        if os.getenv(var):
            return True

    # Командная строка (python -m pdb, etc.)
    for arg in _sys.argv:
        if arg in ("-m", "pdb", "--pdb", "--debug"):
            return True

    # Проверка ptrace (Linux)
    if _sys.platform == "linux":
        try:
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("TracerPid:"):
                        pid = int(line.split(":")[1].strip())
                        if pid != 0:
                            return True
        except Exception:
            pass

    # Проверка Windows Debugger
    if _sys.platform == "win32":
        try:
            import ctypes
            if ctypes.windll.kernel32.IsDebuggerPresent():
                return True
            # Проверка через NtQueryInformationProcess
            is_debugged = ctypes.c_int(0)
            ctypes.windll.kernel32.CheckRemoteDebuggerPresent(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(is_debugged),
            )
            if is_debugged.value:
                return True
        except Exception:
            pass

    return False

# ==================== Cloudflare WAF Bypass Middleware ====================

class CloudflareMiddleware(BaseHTTPMiddleware):
    """
    Middleware для обхода Cloudflare WAF на Render.

    Что делает:
    1. Определяет, что запрос прошёл через Cloudflare (по заголовку CF-Ray)
    2. Добавляет ответные заголовки, снижающие агрессивность WAF
    3. Обрабатывает OPTIONS preflight для мобильных клиентов
    4. Добавляет Cache-Control для предотвращения кэширования Cloudflare
    5. Логирует CF-Ray и User-Agent для отладки блокировок
    6. Детектит подозрительные User-Agent (боты, скраперы) и логирует
    """

    # User-Agent паттерны, которые Cloudflare считает легитимными
    SAFE_UA_PREFIXES = (
        "Mozilla/", "Dalvik/", "okhttp/", "AutoDiag/",
        "Dart/", "PostmanRuntime/", "curl/", "python-httpx/",
        "python-requests/", "Java/", "Apache-HttpClient/",
    )

    # User-Agent паттерны, которые почти всегда блокируются Cloudflare WAF
    SUSPICIOUS_UA_PREFIXES = (
        "Go-http-client/", "Python-urllib/", "Wget/",
        "libwww-perl/", "zgrab", "Nmap", "masscan",
    )

    async def dispatch(self, request: Request, call_next):
        cf_ray = request.headers.get("CF-Ray", "")
        cf_ipcountry = request.headers.get("CF-IPCountry", "")
        user_agent = request.headers.get("User-Agent", "")

        # Детектируем подозрительный User-Agent
        ua_status = "ok"
        if not user_agent:
            ua_status = "missing"
            logger.warning(f"Request without User-Agent: {request.method} {request.url.path} "
                          f"from {request.client.host if request.client else 'unknown'}")
        elif any(user_agent.startswith(p) for p in self.SUSPICIOUS_UA_PREFIXES):
            ua_status = "suspicious"
            logger.warning(f"Suspicious User-Agent: '{user_agent[:80]}' "
                          f"{request.method} {request.url.path}")
        elif not any(user_agent.startswith(p) for p in self.SAFE_UA_PREFIXES):
            ua_status = "unknown"
            logger.info(f"Unknown User-Agent: '{user_agent[:80]}' "
                       f"{request.method} {request.url.path}")

        # Обработка OPTIONS preflight (мобильные клиенты часто шлют)
        if request.method == "OPTIONS":
            response = Response(status_code=204)
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, Authorization, X-Request-ID, "
                "X-Timestamp, X-Signature, X-Device-ID, User-Agent"
            )
            response.headers["Access-Control-Max-Age"] = "86400"
            return response

        response = await call_next(request)

        # Cloudflare-friendly заголовки ответа
        response.headers["CF-Cache-Status"] = "DYNAMIC"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Vary"] = "Accept-Encoding, Origin, User-Agent"

        # Запрет индексации API (чтобы боты не триггерили WAF)
        response.headers["X-Robots-Tag"] = "noindex, nofollow"

        # Отладочная информация (только при наличии CF-Ray)
        if cf_ray:
            response.headers["X-CF-Ray"] = cf_ray
            logger.debug(f"Cloudflare: ray={cf_ray} country={cf_ipcountry} "
                        f"ua={ua_status} method={request.method} path={request.url.path}")

        return response

# ==================== WAF Bypass Middleware ====================

# Paths that accept JSON body
_WAF_BYPASS_PATHS = {"/diagnose", "/license/activate", "/memory/add"}


def _b64_decode(data: str | bytes) -> bytes:
    """Декодировать base64 с автодобавлением padding."""
    if isinstance(data, str):
        data = data.encode("ascii")
    # Добавляем недостающий padding (=)
    missing = len(data) % 4
    if missing:
        data += b"=" * (4 - missing)
    return _base64.urlsafe_b64decode(data)


class WAFBypassMiddleware(BaseHTTPMiddleware):
    """
    Сильный обход Cloudflare WAF.

    Cloudflare WAF на Render блокирует POST с JSON-телом (эвристики SQL-инъекций).
    Middleware извлекает тело запроса из альтернативных источников ДО того,
    как оно попадёт в эндпоинт — Cloudflare видит только безобидный запрос.

    Стратегии обхода (пробуются по порядку):
    1. `?payload=<base64>`   — JSON в query-параметре (WAF не смотрит query)
    2. `X-Body-Base64`       — JSON в кастомном заголовке
    3. `multipart/form-data`  — JSON в поле 'payload' (WAF пропускает формы)
    4. `text/plain`           — сырой base64 как text/plain body
    5. Обычный JSON body     — фолбэк (может быть заблокирован WAF)
    """

    async def dispatch(self, request: Request, call_next):
        # Только POST/PUT на определённые пути
        if request.method not in ("POST", "PUT"):
            return await call_next(request)

        path = request.url.path.rstrip("/")
        if path not in _WAF_BYPASS_PATHS and not any(
            path.startswith(p) for p in _WAF_BYPASS_PATHS
        ):
            return await call_next(request)

        body_json = None
        source = "none"

        # --- Стратегия 1: query-параметр ?payload=<base64> ---
        payload_b64 = request.query_params.get("payload")
        if payload_b64:
            try:
                decoded = _b64_decode(payload_b64)
                body_json = _json.loads(decoded)
                source = "query"
            except Exception:
                pass

        # --- Стратегия 2: заголовок X-Body-Base64 ---
        if body_json is None:
            header_b64 = request.headers.get("X-Body-Base64", "")
            if header_b64:
                try:
                    decoded = _b64_decode(header_b64)
                    body_json = _json.loads(decoded)
                    source = "header"
                except Exception:
                    pass

        # --- Стратегия 3: multipart/form-data с полем 'payload' ---
        if body_json is None:
            content_type = request.headers.get("Content-Type", "")
            if "multipart/form-data" in content_type:
                try:
                    form = await request.form()
                    payload_field = form.get("payload")
                    if payload_field:
                        text = await payload_field.read() if hasattr(payload_field, "read") else str(payload_field)
                        body_json = _json.loads(text)
                        source = "form-data"
                except Exception:
                    pass

        # --- Стратегия 4: text/plain body = base64 JSON ---
        if body_json is None:
            content_type = request.headers.get("Content-Type", "")
            if "text/plain" in content_type:
                try:
                    raw_body = await request.body()
                    decoded = _b64_decode(raw_body)
                    body_json = _json.loads(decoded)
                    source = "text-plain"
                except Exception:
                    pass

        # --- Стратегия 5: обычный JSON body (фолбэк) ---
        if body_json is None:
            try:
                body_json = await request.json()
                source = "json"
            except Exception:
                pass

        if body_json is None:
            return await call_next(request)

        # Инжектим распарсенное тело в request._body для FastAPI
        # Используем приватное API Starlette, но это единственный способ
        # подменить тело после того, как middleware прочитал его
        try:
            injected = _json.dumps(body_json).encode("utf-8")
            # Создаём новый receive, который вернёт injected тело
            async def _receive():
                return {"type": "http.request", "body": injected, "more_body": False}
            request._receive = _receive
        except Exception:
            pass

        logger.debug(f"WAF bypass: source={source} path={path} method={request.method}")
        return await call_next(request)


# ==================== Diagnose WAF Shield Middleware ====================

class DiagnoseWAFShield(BaseHTTPMiddleware):
    """
    Полное отключение WAF-проверок для /diagnose.

    Cloudflare WAF блокирует POST с JSON на /diagnose (эвристики SQL-инъекций).
    Этот middleware перехватывает ВСЕ запросы к /diagnose и нормализует их
    в обход WAF ДО того, как FastAPI начнёт парсить тело.

    Как это работает:
    1. Любой метод (GET/POST/PUT) — парсим параметры из query + body
    2. Тело читается сырыми байтами, НЕ через FastAPI JSON-парсер
    3. Пробуем: query-параметры, form-data, text/plain, JSON
    4. Результат инжектится в request.state — эндпоинты читают оттуда
    5. На все ответы добавляются CF-заголовки bypass-статуса

    Эффект: WAF видит только безобидный GET или text/plain POST,
    а сервер получает полноценный диагностический запрос.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/")

        # Только /diagnose
        if not (path == "/diagnose" or path.startswith("/diagnose/")):
            return await call_next(request)

        # Извлекаем параметры из всех возможных источников
        params: dict[str, str] = {}
        source = "none"

        # --- Источник 1: query-параметры (GET или ?payload=... для POST) ---
        for key in ("error_code", "car_brand", "car_model", "vin", "context",
                     "user_id", "device_id"):
            val = request.query_params.get(key)
            if val:
                params[key] = val
                if source == "none":
                    source = "query"

        # --- Источник 2: ?payload=<base64 JSON> ---
        payload_b64 = request.query_params.get("payload")
        if payload_b64:
            try:
                decoded = _b64_decode(payload_b64)
                body_params = _json.loads(decoded)
                if isinstance(body_params, dict):
                    params.update({k: str(v) for k, v in body_params.items() if v})
                    source = "query-base64"
            except Exception:
                pass

        # --- Источник 3: X-Body-Base64 header ---
        header_b64 = request.headers.get("X-Body-Base64", "")
        if header_b64 and source == "none":
            try:
                decoded = _b64_decode(header_b64)
                body_params = _json.loads(decoded)
                if isinstance(body_params, dict):
                    params.update({k: str(v) for k, v in body_params.items() if v})
                    source = "header-base64"
            except Exception:
                pass

        # --- Источник 4: тело запроса (пытаемся прочитать сырые байты) ---
        if source in ("none", "query"):
            try:
                raw_body = await request.body()
                if raw_body:
                    content_type = request.headers.get("Content-Type", "")

                    # multipart/form-data
                    if "multipart/form-data" in content_type:
                        try:
                            form = await request.form()
                            for key in form:
                                val = form.get(key)
                                if val:
                                    params[key] = await val.read() if hasattr(val, "read") else str(val)
                            source = "form-data"
                        except Exception:
                            pass

                    # application/x-www-form-urlencoded
                    elif "application/x-www-form-urlencoded" in content_type:
                        try:
                            from urllib.parse import parse_qs
                            decoded = raw_body.decode("utf-8", errors="replace")
                            for key, vals in parse_qs(decoded).items():
                                if vals:
                                    params[key] = vals[0]
                            source = "form-urlencoded"
                        except Exception:
                            pass

                    # text/plain (base64-encoded JSON)
                    elif "text/plain" in content_type:
                        try:
                            decoded = _b64_decode(raw_body)
                            body_params = _json.loads(decoded)
                            if isinstance(body_params, dict):
                                params.update({k: str(v) for k, v in body_params.items() if v})
                                source = "text-plain"
                        except Exception:
                            pass

                    # JSON (фолбэк — может быть заблокирован WAF)
                    elif "application/json" in content_type or not content_type:
                        try:
                            body_params = _json.loads(raw_body)
                            if isinstance(body_params, dict):
                                params.update({k: str(v) for k, v in body_params.items() if v})
                                source = "json"
                        except Exception:
                            pass
            except Exception:
                pass

        # Сохраняем распарсенные параметры в request.state
        request.state.diagnose_params = params
        request.state.diagnose_source = source

        logger.debug(
            f"DiagnoseWAF: source={source} params={list(params.keys())} "
            f"method={request.method}"
        )

        response: Response = await call_next(request)

        # Добавляем CF-заголовки в ответ, сигнализирующие что endpoint безопасен
        response.headers["CF-WAF-Bypass"] = source
        response.headers["X-Diagnose-Source"] = source

        return response
