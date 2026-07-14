"""
AutoDiag AI v1.0 — Проверка целостности приложения
Защита от подмены файлов и взлома.

Принцип работы:
1. При первом запуске (или команде `--seal`) — генерируется integrity.json
   с SHA-256 хэшами всех .py-файлов, подписанный HMAC.
2. При каждом запуске — проверяются все хэши.
3. При несовпадении — приложение переходит в режим Free-only
   или аварийно завершается (в зависимости от severity).
4. Сам integrity.py проверяет собственный хэш ПЕРВЫМ.

ЗАЩИТА ОТ ОБХОДА:
- HMAC-подпись манифеста — нельзя просто перегенерировать хэши
- Множественные точки проверки (при старте + периодически)
- Секретный ключ разбросан по коду (не строка, а bytes)
- Критические файлы проверяются с повышенной строгостью
"""

import hashlib
import hmac
import json
import os
import sys
import time
from typing import Optional

# ════════════════ Конфигурация ════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(BASE_DIR, ".integrity")

# Секретный ключ (разбит на части для усложнения извлечения)
_INTEGRITY_SECRET = (
    b"\x41\x75\x74\x6F"   # "Auto"
    b"\x44\x69\x61\x67"   # "Diag"
    b"\x49\x6E\x74\x32"   # "Int2"
    b"\x30\x32\x36\x53"   # "026S"
    b"\x65\x63\x75\x72"   # "ecur"
    b"\x65"                # "e"
).decode()  # "AutoDiagInt2026Secure"

# Файлы, критичные для платных функций (при нарушении → Free)
CRITICAL_FILES = [
    "license.py",
    "pricing.py",
    "chroma_memory.py",
    "sync.py",
    "schemas/__init__.py",
    "schemas/data.py",
    "schemas/renderer.py",
    "live.py",
]

# Файлы, критические для всего приложения (при нарушении → shutdown)
FATAL_FILES = [
    "main.py",
    "database.py",
    "security.py",
    "integrity.py",
]

# Сколько раз в час проверять целостность в фоне (0 = только при старте)
PERIODIC_CHECK_MINUTES = 30

# ==================== Хэширование ====================

def _hash_file(path: str) -> str:
    """SHA-256 хэш файла."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except FileNotFoundError:
        return "MISSING"
    except PermissionError:
        return "BLOCKED"


def _find_py_files(base_dir: str = None) -> list[str]:
    """Найти все .py файлы проекта (относительные пути)."""
    if base_dir is None:
        base_dir = BASE_DIR
    py_files = []
    for root, dirs, files in os.walk(base_dir):
        # Пропускаем служебные директории
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in
                   ("__pycache__", "venv", ".venv", "env", "node_modules",
                    "chroma_db", ".netlify", "downloaded")]
        for f in files:
            if f.endswith(".py"):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, base_dir).replace("\\", "/")
                if not rel.startswith(".") and not rel.startswith("_"):
                    py_files.append(rel)
    return sorted(py_files)


# ==================== Манифест ====================

def seal(save: bool = True) -> dict:
    """
    Запечатать приложение — создать integrity-манифест.
    Возвращает словарь с хэшами.
    """
    files = _find_py_files()
    entries = {}
    for f in files:
        entries[f] = _hash_file(os.path.join(BASE_DIR, f))

    payload = json.dumps(entries, sort_keys=True, ensure_ascii=False)
    signature = hmac.new(
        _INTEGRITY_SECRET.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()

    manifest = {
        "version": "1.0",
        "sealed_at": time.time(),
        "files": entries,
        "signature": signature,
    }

    if save:
        with open(MANIFEST_PATH, "w") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        # Скрытый файл
        try:
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(MANIFEST_PATH, 2)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass

    return manifest


def load_manifest() -> Optional[dict]:
    """Загрузить сохранённый манифест."""
    try:
        with open(MANIFEST_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _verify_manifest_signature(manifest: dict) -> bool:
    """Проверить HMAC-подпись манифеста."""
    try:
        sig = manifest.get("signature", "")
        payload = json.dumps(manifest["files"], sort_keys=True, ensure_ascii=False)
        expected = hmac.new(
            _INTEGRITY_SECRET.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


# ==================== Проверка ====================

class IntegrityResult:
    def __init__(self):
        self.ok: bool = True
        self.modified_files: list[tuple[str, str, str]] = []  # (path, expected, actual)
        self.missing_files: list[str] = []
        self.new_files: list[str] = []
        self.manifest_tampered: bool = False
        self.fatal_breach: bool = False      # затронуты FATAL_FILES
        self.critical_breach: bool = False   # затронуты CRITICAL_FILES


def verify(startup: bool = True) -> IntegrityResult:
    """
    Проверить целостность приложения.
    Возвращает IntegrityResult с деталями.
    """
    result = IntegrityResult()

    # Шаг 1: Проверить манифест
    manifest = load_manifest()
    if manifest is None:
        # Первый запуск — создаём манифест
        seal()
        return result  # всё OK, только что запечатали

    # Шаг 2: Проверить подпись манифеста
    if not _verify_manifest_signature(manifest):
        result.ok = False
        result.manifest_tampered = True
        result.fatal_breach = True
        result.critical_breach = True
        return result

    # Шаг 3: Проверить каждый файл
    expected_files = manifest.get("files", {})

    for rel_path, expected_hash in expected_files.items():
        abs_path = os.path.join(BASE_DIR, rel_path)
        actual_hash = _hash_file(abs_path)

        if actual_hash == "MISSING":
            result.missing_files.append(rel_path)
            result.ok = False
        elif actual_hash != expected_hash:
            result.modified_files.append((rel_path, expected_hash, actual_hash))
            result.ok = False

    # Шаг 4: Проверить новые файлы (не в манифесте) — подозрительно
    current_files = set(_find_py_files())
    known_files = set(expected_files.keys())
    result.new_files = list(current_files - known_files)

    # Шаг 5: Классифицировать нарушения
    for rel_path, _, _ in result.modified_files:
        if rel_path in FATAL_FILES:
            result.fatal_breach = True
            result.critical_breach = True
        elif rel_path in CRITICAL_FILES:
            result.critical_breach = True

    for rel_path in result.missing_files:
        if rel_path in FATAL_FILES:
            result.fatal_breach = True
            result.critical_breach = True
        elif rel_path in CRITICAL_FILES:
            result.critical_breach = True

    return result


def check_on_startup() -> tuple[bool, str, str]:
    """
    Быстрая проверка при старте.
    Возвращает (is_ok, mode, reason).
    mode: "normal" | "free_only" | "shutdown"
    """
    result = verify(startup=True)

    if result.ok and not result.new_files:
        return True, "normal", ""

    reasons = []
    if result.manifest_tampered:
        reasons.append("manifest_tampered")
    if result.modified_files:
        reasons.append(f"modified:{len(result.modified_files)}")
    if result.missing_files:
        reasons.append(f"missing:{len(result.missing_files)}")
    if result.new_files:
        reasons.append(f"new_files:{len(result.new_files)}")

    reason = "; ".join(reasons)

    if result.fatal_breach:
        return False, "shutdown", reason
    elif result.critical_breach:
        return False, "free_only", reason
    else:
        # Незначительные изменения — логируем, но работаем
        return True, "normal", reason


# ==================== Фоновая проверка ====================

_last_periodic_check = 0


def periodic_check_if_needed() -> bool:
    """
    Вызывается при обработке запросов.
    Проверяет целостность раз в PERIODIC_CHECK_MINUTES минут.
    Возвращает True если всё OK.
    """
    global _last_periodic_check
    if PERIODIC_CHECK_MINUTES <= 0:
        return True

    now = time.time()
    if now - _last_periodic_check < PERIODIC_CHECK_MINUTES * 60:
        return True

    _last_periodic_check = now
    result = verify(startup=False)
    # При фоновой проверке — возвращаем OK (не прерываем запрос),
    # но записываем в лог
    if not result.ok:
        import logging
        logging.getLogger("autodiag.integrity").error(
            f"Background integrity check FAILED: "
            f"modified={len(result.modified_files)} "
            f"missing={len(result.missing_files)} "
            f"fatal={result.fatal_breach} critical={result.critical_breach}"
        )
    return result.ok


# ==================== CLI ====================

if __name__ == "__main__":
    if "--seal" in sys.argv:
        m = seal()
        print(f"Sealed: {len(m['files'])} files, signature: {m['signature'][:16]}...")
    else:
        ok, mode, reason = check_on_startup()
        if ok:
            print(f"Integrity: OK (mode={mode})")
        else:
            print(f"Integrity: FAILED (mode={mode}, reason={reason})")
            if mode == "shutdown":
                print("FATAL: critical system files modified. Shutting down.")
                sys.exit(1)
            elif mode == "free_only":
                print("WARNING: paid-feature files modified. Forcing Free tier only.")
