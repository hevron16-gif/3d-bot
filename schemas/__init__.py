"""
AutoDiag AI v1.0 — Пакет схем (2D)
Экспорт: данные схем, SVG-рендерер.

Free: заглушка с предложением апгрейда.
Paid (Pro/Enterprise): полные схемы + SVG.
"""

from schemas.data import (
    _SCHEMAS,
    get_schema,
    get_schema_or_upgrade,
    list_available_schemas,
)
from schemas.renderer import render_schema_svg
from schemas.downloader import SchemaDownloader

__all__ = [
    "get_schema",
    "get_schema_or_upgrade",
    "list_available_schemas",
    "render_schema_svg",
    "SchemaDownloader",
]
