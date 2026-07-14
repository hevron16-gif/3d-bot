"""
AutoDiag AI v1.0 — Расширенный OBD-симулятор
Полноценный симулятор для тестирования без реального ELM327.
Поддерживает: живые данные, инжект ошибок, все режимы (03/07/0A),
российские авто, ГБО, спецтехнику.
"""

import random
import time
from typing import Optional
from datetime import datetime, timezone

# ===================== Список поддерживаемых авто =====================

RUSSIAN_CARS = {
    "lada_granta":     {"brand": "Lada",        "model": "Granta",       "year": 2024, "fuel": "petrol"},
    "lada_vesta":      {"brand": "Lada",        "model": "Vesta",        "year": 2024, "fuel": "petrol"},
    "lada_niva":       {"brand": "Lada",        "model": "Niva Legend",  "year": 2024, "fuel": "petrol"},
    "gaz_gazelle":     {"brand": "ГАЗ",         "model": "Газель Next",  "year": 2023, "fuel": "gas",    "gas_equipment": True},
    "gaz_sobol":       {"brand": "ГАЗ",         "model": "Соболь",       "year": 2023, "fuel": "petrol"},
    "uaz_patriot":     {"brand": "УАЗ",         "model": "Патриот",      "year": 2024, "fuel": "petrol"},
    "uaz_buhanka":     {"brand": "УАЗ",         "model": "Буханка",      "year": 2024, "fuel": "petrol"},
    "kamaz_54901":     {"brand": "КамАЗ",       "model": "54901",        "year": 2024, "fuel": "diesel", "special": True},
    "mtz_82":          {"brand": "МТЗ",         "model": "82 Belarus",   "year": 2023, "fuel": "diesel", "special": True},
}

# ===================== Симулятор =====================

class SimulatorState:
    """Состояние симулятора — имитация реального автомобиля."""

    def __init__(self, car_key: str = "lada_vesta"):
        self.car = RUSSIAN_CARS.get(car_key, RUSSIAN_CARS["lada_vesta"])
        self.car_key = car_key
        self.engine_running = False
        self.engine_runtime = 0   # секунд
        self.start_time = None

        # Текущие значения PID
        self.rpm = 0
        self.speed = 0
        self.coolant_temp = random.uniform(20, 30)  # холодный старт
        self.maf = 0
        self.throttle_pos = 0
        self.intake_temp = random.uniform(15, 25)
        self.engine_load = 0
        self.map_kpa = 100        # атмосферное
        self.short_term_fuel = 0
        self.long_term_fuel = random.uniform(-5, 5)
        self.o2_b1s1 = 0.45       # лямбда
        self.o2_b1s2 = 0.45
        self.fuel_pressure = 0
        self.fuel_level = random.uniform(30, 80)
        self.battery_voltage = 12.6

        # Ошибки
        self.active_codes: list[str] = []         # mode 03 — текущие
        self.pending_codes: list[str] = []         # mode 07 — pending
        self.permanent_codes: list[str] = []       # mode 0A — перманентные

        # Инжектированные ошибки (не сбрасываются clear_codes)
        self._injected: list[str] = []

    # --------------- Управление двигателем ---------------

    def start_engine(self):
        if not self.engine_running:
            self.engine_running = True
            self.start_time = time.time()
            self.rpm = random.uniform(1100, 1400)  # прогрев
            self.coolant_temp = random.uniform(20, 35)

    def stop_engine(self):
        self.engine_running = False
        self.rpm = 0
        self.maf = 0
        self.engine_load = 0
        self.fuel_pressure = 0
        self.start_time = None

    # --------------- Симуляция работы ---------------

    def tick(self, dt: float = 1.0):
        """Обновить состояние симуляции (1 тик ≈ 1 секунда)."""
        if not self.engine_running:
            return

        self.engine_runtime = int(time.time() - self.start_time) if self.start_time else 0

        # Прогрев
        target_temp = random.uniform(85, 95)
        if self.coolant_temp < target_temp:
            self.coolant_temp += random.uniform(0.2, 0.8) * dt
        else:
            self.coolant_temp += random.uniform(-0.3, 0.5) * dt
        self.coolant_temp = max(15, min(110, self.coolant_temp))

        # Обороты холостого хода (после прогрева)
        if self.throttle_pos < 2:
            idle_target = 850 if self.coolant_temp > 70 else 1200
            if abs(self.rpm - idle_target) < 10:
                self.rpm = idle_target + random.uniform(-30, 30)
            else:
                self.rpm += (idle_target - self.rpm) * 0.1 * dt
        else:
            # Обороты зависят от дросселя
            self.rpm = 850 + self.throttle_pos * 40 + random.uniform(-50, 50)

        # MAF ~ расход воздуха
        self.maf = max(0, self.rpm / 500 * random.uniform(0.8, 1.2))
        if self.rpm > 2000:
            self.maf += random.uniform(2, 8)

        # Нагрузка
        self.engine_load = max(5, min(100, self.throttle_pos * 0.8 + self.rpm / 50))

        # MAP
        self.map_kpa = max(20, 100 - self.rpm / 60 + random.uniform(-3, 3))

        # Коррекции топлива
        self.short_term_fuel += random.uniform(-1, 1)
        self.short_term_fuel = max(-25, min(25, self.short_term_fuel))

        # Лямбда
        self.o2_b1s1 = 0.1 + random.uniform(0, 0.7)

        # Давление топлива
        self.fuel_pressure = 300 + random.uniform(-20, 20)

        # Напряжение
        if self.rpm > 800:
            self.battery_voltage = 13.8 + random.uniform(-0.5, 0.3)
        else:
            self.battery_voltage = 12.4 + random.uniform(-0.2, 0.2)

    # --------------- Инжект и сброс ошибок ---------------

    def inject_code(self, code: str, mode: str = "current"):
        """Инжектировать код ошибки в указанный режим."""
        code = code.upper()
        self._injected.append(code)
        if mode == "current":
            self.active_codes.append(code)
        elif mode == "pending":
            self.pending_codes.append(code)
        elif mode == "permanent":
            self.permanent_codes.append(code)

    def clear_codes(self):
        """Сбросить все ошибки (кроме инжектированных)."""
        self.active_codes = [c for c in self.active_codes if c in self._injected]
        self.pending_codes = [c for c in self.pending_codes if c in self._injected]
        # Перманентные не сбрасываются (по OBD-II спецификации)
        # self.permanent_codes оставляем

    # --------------- Генерация естественных ошибок ---------------

    def generate_natural_errors(self):
        """Случайная генерация ошибок для реалистичности."""
        if self.engine_running and random.random() < 0.002:
            code = random.choice(["P0300", "P0171", "P0420", "P0134", "P0301", "P0302"])
            if code not in self.active_codes:
                self.active_codes.append(code)

    # --------------- Получение данных ---------------

    def get_live_data(self) -> dict:
        """Срез текущих живых данных."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "car": self.car,
            "engine_running": self.engine_running,
            "engine_runtime": self.engine_runtime,
            "rpm": round(self.rpm, 1),
            "speed": round(self.speed, 1),
            "coolant_temp": round(self.coolant_temp, 1),
            "maf": round(self.maf, 2),
            "throttle_pos": round(self.throttle_pos, 1),
            "intake_temp": round(self.intake_temp, 1),
            "engine_load": round(self.engine_load, 1),
            "map_kpa": round(self.map_kpa, 1),
            "short_term_fuel": round(self.short_term_fuel, 1),
            "long_term_fuel": round(self.long_term_fuel, 1),
            "o2_b1s1": round(self.o2_b1s1, 3),
            "o2_b1s2": round(self.o2_b1s2, 3),
            "fuel_pressure": round(self.fuel_pressure, 1),
            "fuel_level": round(self.fuel_level, 1),
            "battery_voltage": round(self.battery_voltage, 1),
        }

    def get_codes(self) -> dict:
        """Все ошибки по режимам."""
        return {
            "current": list(set(self.active_codes)),
            "pending": list(set(self.pending_codes)),
            "permanent": list(set(self.permanent_codes)),
            "injected": list(set(self._injected)),
            "check_engine": len(self.active_codes) > 0 or len(self.pending_codes) > 0,
        }


# Глобальный экземпляр симулятора
simulator = SimulatorState()
