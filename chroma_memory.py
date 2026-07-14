"""
AutoDiag AI v1.0 — Модуль самообучения (ChromaDB)
Память успешных решений: векторизация диагнозов, поиск похожих кейсов.
Главная фишка продукта!

Free-версия: неактивна (заглушка).
Paid-версия (Pro/Enterprise): полный доступ.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

# ChromaDB — опциональный импорт
try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False

COLLECTION_NAME = "autodiag_cases"


class ChromaMemory:
    """Векторная память успешных диагнозов."""

    def __init__(self, persist_dir: str = "./chroma_data"):
        self.persist_dir = persist_dir
        self._client = None
        self._collection = None
        self._available = False
        self._init_chroma()

    def _init_chroma(self):
        if not CHROMA_AVAILABLE:
            return
        try:
            self._client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False)
            )
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"}
            )
            self._available = True
        except Exception:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def add_case(self, error_code: str, car_brand: str, diagnosis: str,
                 solution: str, user_id: str = "anonymous",
                 metadata: dict = None) -> Optional[str]:
        """Добавить успешный кейс в память.

        Возвращает id записи или None при недоступности ChromaDB.
        """
        if not self._available:
            return None

        case_id = str(uuid.uuid4())
        document = (
            f"Код: {error_code}\n"
            f"Авто: {car_brand}\n"
            f"Диагноз: {diagnosis}\n"
            f"Решение: {solution}"
        )
        meta = {
            "error_code": error_code,
            "car_brand": car_brand,
            "user_id": user_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": True,
        }
        if metadata:
            meta.update(metadata)

        try:
            self._collection.add(
                ids=[case_id],
                documents=[document],
                metadatas=[meta],
            )
            return case_id
        except Exception:
            return None

    def search(self, query: str, n_results: int = 5) -> list[dict]:
        """Поиск похожих кейсов по тексту запроса.

        Возвращает список словарей с полями: id, document, metadata, distance.
        """
        if not self._available:
            return []
        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )
            out = []
            if results["ids"] and results["ids"][0]:
                for i in range(len(results["ids"][0])):
                    out.append({
                        "id": results["ids"][0][i],
                        "document": results["documents"][0][i] if results["documents"] else "",
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "distance": results["distances"][0][i] if results["distances"] else None,
                    })
            return out
        except Exception:
            return []

    def search_by_code(self, error_code: str, n_results: int = 5) -> list[dict]:
        """Поиск кейсов по коду ошибки."""
        query = f"Код ошибки {error_code}. Диагностика и решение."
        return self.search(query, n_results)

    def delete_case(self, case_id: str) -> bool:
        """Удалить кейс по id."""
        if not self._available:
            return False
        try:
            self._collection.delete(ids=[case_id])
            return True
        except Exception:
            return False

    def count(self) -> int:
        """Количество записей в коллекции."""
        if not self._available:
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0


# Глобальный экземпляр
chroma = ChromaMemory()
