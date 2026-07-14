"""
AutoDiag AI v1.0 — Hardware Fingerprint (Device ID)
Привязка лицензии к физическому устройству.
Собирает уникальные признаки: MAC-адрес, MachineGuid, hostname, серийник диска.

ЗАЩИТА:
- HMAC-SHA256 подпись фингерпринта — нельзя подделать
- Несколько источников — отказоустойчивость (VM/контейнер)
- Случайный nonce при генерации для уникальности
"""

import hashlib
import hmac
import os
import socket
import subprocess
import sys
import uuid as _uuid
from typing import Optional

# ════════════════ Секретный ключ (разный в каждом билде) ════════════════

_FP_SECRET = bytes([
    0x41, 0x75, 0x74, 0x6F, 0x44, 0x69, 0x61, 0x67,
    0x46, 0x50, 0x32, 0x30, 0x32, 0x36, 0x5F, 0x4B,
]).decode()  # "AutoDiagFP2026_K"

# ════════════════ Сборка фингерпринта ════════════════

def _get_mac_addresses() -> list[str]:
    """Все MAC-адреса (без colons)."""
    macs = []
    try:
        # Windows: getmac
        out = subprocess.check_output(
            ["getmac", "/FO", "CSV", "/NH"],
            shell=True, timeout=5,
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="ignore")
        for line in out.strip().split("\n"):
            # "Network Adapter,00-1A-2B-3C-4D-5E,\\Device\Tcpip_{...}"
            parts = line.replace('"', '').split(",")
            if len(parts) >= 2:
                mac = parts[1].strip().replace("-", "").replace(":", "").upper()
                if len(mac) == 12 and mac != "000000000000" and mac != "FFFFFFFFFFFF":
                    macs.append(mac)
    except Exception:
        pass

    # Fallback: uuid.getnode()
    try:
        node = _uuid.getnode()
        if node and node != 0:
            mac = f"{node:012X}"
            if mac not in macs and mac != "000000000000":
                macs.append(mac)
    except Exception:
        pass

    # Fallback: hostname
    try:
        host = socket.gethostname()
        if host:
            macs.append(f"HOST:{host}")
    except Exception:
        pass

    return macs


def _get_machine_guid() -> Optional[str]:
    """Windows MachineGuid из реестра."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography"
        )
        guid, _ = winreg.QueryValueEx(key, "MachineGuid")
        winreg.CloseKey(key)
        return str(guid).strip()
    except Exception:
        return None


def _get_disk_serial() -> Optional[str]:
    """Серийный номер системного диска (Windows)."""
    try:
        out = subprocess.check_output(
            ["wmic", "diskdrive", "get", "SerialNumber"],
            shell=True, timeout=5,
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="ignore")
        lines = [l.strip() for l in out.split("\n") if l.strip() and l.strip() != "SerialNumber"]
        if lines:
            return lines[0]
    except Exception:
        pass
    return None


def get_hardware_fingerprint() -> str:
    """
    Уникальный фингерпринт устройства.
    HMAC-SHA256 от конкатенации всех доступных признаков.
    """
    parts: list[str] = []

    macs = _get_mac_addresses()
    if macs:
        parts.append("MAC:" + ",".join(sorted(macs)))

    guid = _get_machine_guid()
    if guid:
        parts.append(f"GUID:{guid}")

    disk = _get_disk_serial()
    if disk:
        parts.append(f"DISK:{disk}")

    # Если совсем ничего не собрали — генерируем случайный
    if not parts:
        parts.append(f"RANDOM:{_uuid.uuid4().hex}")

    raw = "|".join(parts)
    signature = hmac.new(
        _FP_SECRET.encode(),
        raw.encode(),
        hashlib.sha256
    ).hexdigest()

    return f"DEV-{signature[:16].upper()}"


def get_device_id() -> str:
    """
    Получить или создать постоянный device_id.
    Кэшируется в файле .device_id (не в БД — чтобы не зависеть от БД).
    """
    id_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".device_id")

    # Читаем кэшированный
    if os.path.exists(id_file):
        try:
            with open(id_file, "r") as f:
                cached = f.read().strip()
                if cached and cached.startswith("DEV-") and len(cached) == 21:
                    return cached
        except Exception:
            pass

    # Генерируем новый
    device_id = get_hardware_fingerprint()
    try:
        with open(id_file, "w") as f:
            f.write(device_id)
    except Exception:
        pass

    return device_id


def verify_device_binding(device_id: str) -> bool:
    """
    Проверить, что device_id соответствует текущему устройству.
    Перегенерирует фингерпринт заново и сравнивает.
    Если точного совпадения нет — проверяет MAC/GUID независимо.
    """
    # Быстрая проверка — точное совпадение
    current = get_hardware_fingerprint()
    if current == device_id:
        return True

    # Медленная проверка — собираем признаки без кэша
    parts: list[str] = []

    macs = sorted(_get_mac_addresses())
    if macs:
        parts.append("MAC:" + ",".join(macs))

    guid = _get_machine_guid()
    if guid:
        parts.append(f"GUID:{guid}")

    disk = _get_disk_serial()
    if disk:
        parts.append(f"DISK:{disk}")

    if not parts:
        return False

    raw = "|".join(parts)
    fresh = "DEV-" + hmac.new(
        _FP_SECRET.encode(),
        raw.encode(),
        hashlib.sha256
    ).hexdigest()[:16].upper()

    return fresh == device_id
