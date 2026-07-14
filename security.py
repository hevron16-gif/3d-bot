"""
AutoDiag AI v1.0 — Модуль защиты
Rate limiting, security headers, input sanitization, safe error handling.
"""

import time
import hashlib
import hmac
import re
import logging
from collections import defaultdict
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

MAX_BODY_SIZE = 100 * 1024       # 100 KB
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
        """Извлечь IP клиента."""
        # X-Forwarded-For (за прокси)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        # X-Real-IP
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
        # Прямой IP
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
    """Добавляет HTTP-заголовки безопасности ко всем ответам."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), "
            "interest-cohort=()"
        )
        # Не кэшировать API-ответы
        response.headers["Cache-Control"] = "no-store, max-age=0"

        # Убираем заголовок Server (если не переопределён uvicorn)
        if "Server" in response.headers:
            del response.headers["Server"]

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
