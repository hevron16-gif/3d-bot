"""
AutoDiag AI v1.0 — Живые данные + графики
Потоковая выдача PID с подготовкой структуры под графики (Chart.js / Plotly).
"""

import random
import time
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, field

from elm327 import SimulatedELM327


@dataclass
class LiveSample:
    timestamp: str
    rpm: float = 0
    speed: float = 0
    coolant_temp: float = 0
    maf: float = 0
    throttle_pos: float = 0
    intake_temp: float = 0
    engine_load: float = 0
    map_kpa: float = 0
    short_term_fuel: float = 0
    long_term_fuel: float = 0
    o2_b1s1: float = 0
    o2_b1s2: float = 0
    fuel_pressure: float = 0


class LiveDataCollector:
    """Сборщик живых данных с историей для графиков."""

    def __init__(self, max_samples: int = 300):
        self.max_samples = max_samples
        self._samples: list[LiveSample] = []

    def add_sample(self, data: dict):
        """Добавить срез данных."""
        ts = datetime.now(timezone.utc).isoformat()
        sample = LiveSample(
            timestamp=ts,
            rpm=data.get("rpm", 0),
            speed=data.get("speed", 0),
            coolant_temp=data.get("coolant_temp", 0),
            maf=data.get("maf", 0),
            throttle_pos=data.get("throttle_pos", 0),
            intake_temp=data.get("intake_temp", 0),
            engine_load=data.get("engine_load", 0),
            map_kpa=data.get("map_kpa", 0),
            short_term_fuel=data.get("short_term_fuel", 0),
            long_term_fuel=data.get("long_term_fuel", 0),
            o2_b1s1=data.get("o2_b1s1", 0),
            o2_b1s2=data.get("o2_b1s2", 0),
            fuel_pressure=data.get("fuel_pressure", 0),
        )
        self._samples.append(sample)
        if len(self._samples) > self.max_samples:
            self._samples = self._samples[-self.max_samples:]

    def get_history(self, limit: int = 100) -> list[dict]:
        """Получить историю в формате для графиков."""
        samples = self._samples[-limit:]
        return [s.__dict__ for s in samples]

    def get_graph_data(self) -> dict:
        """Подготовить данные для отрисовки графиков (Chart.js-совместимый формат)."""
        if not self._samples:
            return {"labels": [], "datasets": []}

        samples = self._samples[-60:]  # последние 60 точек
        labels = [s.timestamp[-12:-7] if len(s.timestamp) > 12 else str(i)
                  for i, s in enumerate(samples)]

        return {
            "labels": labels,
            "datasets": [
                {
                    "label": "RPM",
                    "data": [s.rpm for s in samples],
                    "borderColor": "#2196F3",
                    "yAxisID": "y-rpm",
                },
                {
                    "label": "Скорость (км/ч)",
                    "data": [s.speed for s in samples],
                    "borderColor": "#4CAF50",
                    "yAxisID": "y-speed",
                },
                {
                    "label": "Темп. ОЖ (°C)",
                    "data": [s.coolant_temp for s in samples],
                    "borderColor": "#FF9800",
                    "yAxisID": "y-temp",
                },
                {
                    "label": "MAF (г/с)",
                    "data": [s.maf for s in samples],
                    "borderColor": "#9C27B0",
                    "yAxisID": "y-maf",
                },
                {
                    "label": "Дроссель (%)",
                    "data": [s.throttle_pos for s in samples],
                    "borderColor": "#F44336",
                    "yAxisID": "y-throttle",
                },
                {
                    "label": "Нагрузка (%)",
                    "data": [s.engine_load for s in samples],
                    "borderColor": "#607D8B",
                    "yAxisID": "y-load",
                },
            ],
        }

    def clear(self):
        self._samples.clear()


# Глобальный коллектор
collector = LiveDataCollector()
