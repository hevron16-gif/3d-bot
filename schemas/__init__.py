"""
AutoDiag AI v1.0 — Пакет схем (2D)
Экспорт: данные схем, SVG-рендерер, загрузчик изображений.

Тестовая версия — все схемы доступны бесплатно.
"""

from schemas.data import (
    _SCHEMAS,
    get_schema,
    get_schema_or_upgrade,
    list_available_schemas,
)
from schemas.renderer import render_schema_svg
from schemas.downloader import (
    get_schema as downloader_get_schema,
    get_download_stats,
    refresh_all_schemas,
    search_and_download,
)

__all__ = [
    "_SCHEMAS",
    "get_schema",
    "get_schema_or_upgrade",
    "list_available_schemas",
    "render_schema_svg",
    "downloader_get_schema",
    "get_download_stats",
    "refresh_all_schemas",
    "search_and_download",
]
