"""
AutoDiag AI v1.0 — Загрузчик схем из интернета.

Скачивает изображения схем узлов по коду ошибки и марке авто.
Использует httpx.AsyncClient, единый со всем проектом.
"""

import httpx
from pathlib import Path
from typing import Optional


class SchemaDownloader:
    """Загрузчик схем узлов."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self.schemas_dir = Path(__file__).parent / "downloaded"
        self.schemas_dir.mkdir(exist_ok=True)
        self._client = client

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        return self._client

    async def search_and_download(self, error_code: str, brand: str) -> Optional[str]:
        """Поиск и загрузка схемы. Реальный парсинг отложен."""
        filename = f"{error_code}_{brand}.jpg"
        filepath = self.schemas_dir / filename
        if filepath.exists():
            return str(filepath)

        client = await self._ensure_client()
        queries = [
            f"схема {error_code} {brand} двигатель датчик",
            f"расположение датчика {error_code} {brand}",
            f"{brand} {error_code} схема"
        ]
        for query in queries:
            try:
                url = (
                    "https://yandex.ru/images/search"
                    f"?text={query.replace(' ', '+')}"
                )
                headers = {"User-Agent": "Mozilla/5.0"}
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    image_url = (
                        "https://via.placeholder.com/800x600"
                        f"?text=Schema+{error_code}"
                    )
                    img_response = await client.get(image_url)
                    img_response.raise_for_status()
                    with open(filepath, "wb") as f:
                        f.write(img_response.content)
                    return str(filepath)
            except (httpx.HTTPError, OSError):
                continue
        return None

    async def get_schema(self, error_code: str, brand: str) -> Optional[str]:
        """
        Получить локальный путь к схеме или None.
        Возвращает None — вызывающий код сам решает, что показать.
        """
        return await self.search_and_download(error_code, brand)
