"""
AutoDiag AI v1.0 — Облачная синхронизация
Синхронизация диагностик и успешных кейсов между пользователями.

Free: неактивна.
Paid (Pro/Enterprise): активна.
"""

import json
import httpx
from datetime import datetime, timezone
from typing import Optional

from database import queue_sync, get_sync_queue, mark_synced

# Заглушка облачного API — в продакшене заменить на реальный эндпоинт
CLOUD_API_URL = "https://api.autodiag.ru/v1/sync"
CLOUD_API_KEY = None  # загружать из env в продакшене


class CloudSync:
    """Облачная синхронизация данных между устройствами и пользователями."""

    def __init__(self, api_url: str = CLOUD_API_URL, api_key: str = None):
        self.api_url = api_url
        self.api_key = api_key

    async def push_diagnosis(self, user_id: str, error_code: str, car_brand: str,
                             diagnosis: str, solution: str = None) -> bool:
        """Отправить результат диагностики в облако."""
        payload = {
            "type": "diagnosis",
            "user_id": user_id,
            "error_code": error_code,
            "car_brand": car_brand,
            "diagnosis": diagnosis,
            "solution": solution,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        # Сохраняем в локальную очередь
        queue_sync(payload)
        # Отправляем в облако (если доступно)
        return await self._send(payload)

    async def push_success_case(self, user_id: str, error_code: str,
                                car_brand: str, diagnosis: str,
                                solution: str) -> bool:
        """Отправить успешный кейс для общего обучения."""
        payload = {
            "type": "success_case",
            "user_id": user_id,
            "error_code": error_code,
            "car_brand": car_brand,
            "diagnosis": diagnosis,
            "solution": solution,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "shared": True,
        }
        queue_sync(payload)
        return await self._send(payload)

    async def pull_shared_cases(self, error_code: str = None,
                                car_brand: str = None,
                                limit: int = 10) -> list[dict]:
        """Получить общие успешные кейсы из облака."""
        params = {"limit": limit}
        if error_code:
            params["error_code"] = error_code
        if car_brand:
            params["car_brand"] = car_brand
        result = await self._fetch("GET", "/cases", params=params)
        return result if isinstance(result, list) else []

    async def flush_queue(self) -> int:
        """Отправить все неотправленные записи из локальной очереди."""
        queue = get_sync_queue(limit=50)
        if not queue:
            return 0
        synced = []
        for item in queue:
            payload = json.loads(item["payload"])
            success = await self._send(payload)
            if success:
                synced.append(item["id"])
        mark_synced(synced)
        return len(synced)

    async def _send(self, payload: dict) -> bool:
        """Отправить payload в облако. Заглушка — всегда успех."""
        # В продакшене:
        # try:
        #     async with httpx.AsyncClient(timeout=10) as client:
        #         resp = await client.post(
        #             f"{self.api_url}/push",
        #             headers={"Authorization": f"Bearer {self.api_key}"},
        #             json=payload,
        #         )
        #         return resp.status_code == 200
        # except Exception:
        #     return False
        return True  # заглушка

    async def _fetch(self, method: str, path: str, params: dict = None) -> Optional[dict]:
        """GET-запрос к облачному API. Заглушка — пустой список."""
        # В продакшене:
        # try:
        #     async with httpx.AsyncClient(timeout=10) as client:
        #         resp = await client.get(
        #             f"{self.api_url}{path}",
        #             headers={"Authorization": f"Bearer {self.api_key}"},
        #             params=params or {},
        #         )
        #         return resp.json() if resp.status_code == 200 else None
        # except Exception:
        #     return None
        return []


# Глобальный экземпляр
cloud = CloudSync()
