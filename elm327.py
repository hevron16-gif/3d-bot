"""
AutoDiag AI v1.0 — Модуль ELM327
Чтение ошибок через ELM327 по serial-порту.
Поддерживает режимы: 03 (подтверждённые), 07 (pending), 0A (permanent).
"""

import time
from typing import Optional
from dataclasses import dataclass, field

# Реальный serial — раскомментировать при наличии адаптера:
# import serial

DEFAULT_PORT = "COM3"    # Windows
BAUDRATE = 38400
TIMEOUT = 5.0


@dataclass
class OBDResponse:
    raw: str
    codes: list[str] = field(default_factory=list)
    mode: str = "03"


class ELM327:
    """Обёртка над ELM327 через AT-команды."""

    def __init__(self, port: str = DEFAULT_PORT, baudrate: int = BAUDRATE):
        self.port = port
        self.baudrate = baudrate
        self._ser = None
        self._connected = False

    def connect(self) -> bool:
        """Подключиться к адаптеру. Возвращает True при успехе."""
        try:
            # import serial  # раскомментировать в продакшене
            # self._ser = serial.Serial(self.port, self.baudrate, timeout=TIMEOUT)
            # self._ser.write(b"ATZ\r")
            # time.sleep(1)
            # self._ser.read_all()
            # self._send("ATE0")   # echo off
            # self._send("ATL0")   # linefeed off
            # self._send("ATH1")   # headers on
            # self._send("ATSP0")  # auto protocol
            self._connected = True
            return True
        except Exception:
            self._connected = False
            return False

    def disconnect(self):
        """Отключиться."""
        if self._ser:
            self._ser.close()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def _send(self, cmd: str) -> str:
        """Отправить AT/OBD команду и прочитать ответ."""
        if not self._ser:
            return "NO_CONNECTION"
        self._ser.write((cmd + "\r").encode())
        time.sleep(0.3)
        response = self._ser.read_all().decode("utf-8", errors="ignore").strip()
        return response

    def _parse_codes(self, raw: str, mode: str = "03") -> OBDResponse:
        """Разобрать сырой ответ ELM327 в список кодов."""
        codes = []
        for line in raw.split("\r"):
            line = line.strip()
            if len(line) >= 4 and line[:2].isdigit():
                # Формат: 43 01 33 00 00 00 — байты ответа
                data = line.replace(" ", "")
                for i in range(0, len(data) - 3, 4):
                    code = _bytes_to_dtc(data[i:i + 4])
                    if code:
                        codes.append(code)
        return OBDResponse(raw=raw, codes=codes, mode=mode)

    # ------------------- Режимы чтения ошибок -------------------

    def read_current_codes(self) -> OBDResponse:
        """Режим 03 — текущие подтверждённые DTC."""
        raw = self._send("03")
        return self._parse_codes(raw, "03")

    def read_pending_codes(self) -> OBDResponse:
        """Режим 07 — ожидающие (pending) DTC."""
        raw = self._send("07")
        return self._parse_codes(raw, "07")

    def read_permanent_codes(self) -> OBDResponse:
        """Режим 0A — перманентные DTC."""
        raw = self._send("0A")
        return self._parse_codes(raw, "0A")

    def read_all_codes(self) -> dict:
        """Прочитать все три режима за один заход."""
        return {
            "current":   self.read_current_codes(),
            "pending":   self.read_pending_codes(),
            "permanent": self.read_permanent_codes(),
        }

    def clear_codes(self) -> str:
        """Режим 04 — сброс ошибок и Check Engine."""
        return self._send("04")

    # ------------------- Живые данные (PID) -------------------

    def get_pid(self, pid: str) -> Optional[float]:
        """Запросить один PID (например, '0C' = RPM). Возвращает значение или None."""
        if not self._ser:
            return None
        raw = self._send(f"01{pid}")
        return _parse_pid(raw, pid)

    def get_live_data(self) -> dict:
        """Получить срез основных PID: RPM, скорость, температура, MAF и др."""
        pids = {
            "0C": "rpm",             # RPM = (256*A + B) / 4
            "0D": "speed",           # km/h = A
            "05": "coolant_temp",    # °C = A - 40
            "10": "maf",             # g/s = (256*A + B) / 100
            "11": "throttle_pos",    # % = A * 100 / 255
            "0F": "intake_temp",     # °C = A - 40
            "04": "engine_load",     # % = A * 100 / 255
            "0B": "map",             # kPa = A
            "06": "short_term_fuel", # % = (A - 128) * 100 / 128
            "07": "long_term_fuel",  # % = (A - 128) * 100 / 128
            "0A": "fuel_pressure",   # kPa = 3 * A
            "1F": "runtime",         # seconds = 256*A + B
            "2F": "fuel_level",      # % = A * 100 / 255
        }
        result = {"connected": self._connected}
        for pid, name in pids.items():
            val = self.get_pid(pid)
            if val is not None:
                result[name] = val
        return result


# ===================== Утилиты =====================

# Таблица преобразования первых 2 бит байта в тип DTC
_DTC_TYPE = {
    0: "P0",  # Powertrain — SAE
    1: "P1",  # Powertrain — производитель
    2: "P2",  # Powertrain — SAE
    3: "P3",  # Powertrain — совместно
    4: "C0",  # Chassis — SAE
    5: "C1",  # Chassis — производитель
    6: "C2",  # Chassis — производитель
    7: "C3",  # Chassis — совместно
    8: "B0",  # Body — SAE
    9: "B1",  # Body — производитель
    10: "B2", # Body — производитель
    11: "B3", # Body — совместно
    12: "U0", # Network — SAE
    13: "U1", # Network — производитель
    14: "U2", # Network — производитель
    15: "U3", # Network — совместно
}


def _bytes_to_dtc(hex4: str) -> Optional[str]:
    """Преобразовать 4-байтовый hex (2 байта) в код DTC вида P0101."""
    try:
        val = int(hex4, 16)
    except ValueError:
        return None
    type_bits = (val >> 14) & 0x03
    digit3 = (val >> 12) & 0x03
    digit4 = (val >> 8) & 0x0F
    digit5 = (val >> 4) & 0x0F
    digit6 = val & 0x0F
    dtc_type = _DTC_TYPE.get(type_bits, "??")
    return f"{dtc_type[0]}{digit3:X}{digit4:X}{digit5:X}{digit6:X}"


def _parse_pid(raw: str, pid: str) -> Optional[float]:
    """Разобрать ответ на запрос PID. Упрощённая версия."""
    if not raw or "NO DATA" in raw:
        return None
    try:
        # Ожидаемый формат: "41 0C 1A F8" → RPM = (0x1A * 256 + 0xF8) / 4
        lines = raw.strip().split("\r")
        for line in lines:
            line = line.strip().replace(" ", "")
            if line.startswith(f"41{pid}") and len(line) >= 6:
                data = line[4:]
                A = int(data[:2], 16) if len(data) >= 2 else 0
                B = int(data[2:4], 16) if len(data) >= 4 else 0

                # Формулы для разных PID
                if pid == "0C":  # RPM
                    return (A * 256 + B) / 4.0
                elif pid == "0D":  # Speed
                    return float(A)
                elif pid in ("05", "0F"):  # Temperature
                    return float(A - 40)
                elif pid == "10":  # MAF
                    return (A * 256 + B) / 100.0
                elif pid == "11":  # Throttle
                    return A * 100.0 / 255.0
                elif pid == "04":  # Engine load
                    return A * 100.0 / 255.0
                elif pid == "0B":  # MAP
                    return float(A)
                elif pid in ("06", "07"):  # Fuel trim
                    return (int(data[:2], 16) - 128) * 100.0 / 128.0
                elif pid == "0A":  # Fuel pressure
                    return float(3 * A)
                elif pid == "1F":  # Runtime
                    return float(A * 256 + B)
                elif pid == "2F":  # Fuel level
                    return A * 100.0 / 255.0
                else:
                    return float(A)
    except (ValueError, IndexError):
        return None
    return None


# ===================== Симулятор (без реального ELM) =====================

import random

class SimulatedELM327(ELM327):
    """Симулятор ELM327 для тестирования без реального адаптера."""

    def __init__(self):
        super().__init__(port="SIMULATED")
        self._connected = True
        self._injected_codes: list[str] = []

    def connect(self) -> bool:
        self._connected = True
        return True

    def inject_code(self, code: str):
        """Инжектировать код ошибки для теста."""
        self._injected_codes.append(code.upper())

    def clear_injected(self):
        self._injected_codes.clear()

    def read_current_codes(self) -> OBDResponse:
        codes = list(self._injected_codes)
        if not codes:
            codes = ["P0171"] if random.random() > 0.7 else []
        return OBDResponse(raw="SIMULATED 03", codes=codes, mode="03")

    def read_pending_codes(self) -> OBDResponse:
        codes = ["P0420"] if random.random() > 0.8 else []
        return OBDResponse(raw="SIMULATED 07", codes=codes, mode="07")

    def read_permanent_codes(self) -> OBDResponse:
        codes = ["P0300"] if random.random() > 0.9 else []
        return OBDResponse(raw="SIMULATED 0A", codes=codes, mode="0A")

    def get_live_data(self) -> dict:
        return {
            "connected": True,
            "rpm": round(random.uniform(750, 3500), 1),
            "speed": round(random.uniform(0, 120), 1),
            "coolant_temp": round(random.uniform(75, 105), 1),
            "maf": round(random.uniform(2.0, 25.0), 2),
            "throttle_pos": round(random.uniform(0, 85), 1),
            "intake_temp": round(random.uniform(15, 55), 1),
            "engine_load": round(random.uniform(10, 90), 1),
            "map": round(random.uniform(25, 100), 1),
            "short_term_fuel": round(random.uniform(-10, 10), 1),
            "long_term_fuel": round(random.uniform(-15, 15), 1),
            "fuel_pressure": round(random.uniform(250, 400), 1),
            "runtime": int(random.uniform(60, 3600)),
            "fuel_level": round(random.uniform(20, 95), 1),
        }
