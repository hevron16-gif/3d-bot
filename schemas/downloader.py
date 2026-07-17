"""
Real multi-source schema image downloader.
Sources: Bing Images → Google Images → DuckDuckGo (LibreJS-free) → Yandex JSON endpoint
Saves images to schemas/downloaded/ with metadata.
"""

import re
import json
import asyncio
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("schemas.downloader")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
USER_AGENT_MOBILE = (
    "Mozilla/5.0 (Linux; Android 10; SM-G960U) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/88.0.4324.181 Mobile Safari/537.36"
)

DOWNLOAD_DIR = Path("schemas/downloaded")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Per-code metadata file (JSON) tracking downloaded images and timestamps
META_FILE = DOWNLOAD_DIR / "_meta.json"


def _load_meta() -> dict:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_meta(meta: dict):
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


async def _fetch(client: httpx.AsyncClient, url: str, headers: dict = None) -> Optional[str]:
    """Fetch a URL and return text content, or None on failure."""
    try:
        h = {"User-Agent": USER_AGENT}
        if headers:
            h.update(headers)
        resp = await client.get(url, headers=h)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.debug(f"Fetch failed for {url[:80]}: {exc}")
        return None


# ─── BING IMAGE SEARCH ───────────────────────────────────────────────

async def _search_bing(client: httpx.AsyncClient, query: str, limit: int = 5) -> list[str]:
    """Extract image URLs from Bing Image Search."""
    url = f"https://www.bing.com/images/search?q={query}&first=1&tsc=ImageHoverTitle"
    html = await _fetch(client, url)
    if not html:
        return []

    # Bing stores image data in an inline murl/turl JSON-like pattern
    images = re.findall(r'"murl"\s*:\s*"(https?://[^"]+)"', html, re.IGNORECASE)
    # Deduplicate
    seen = set()
    result = []
    for img in images:
        if img not in seen and not img.endswith(".svg") and _is_valid_image_url(img):
            seen.add(img)
            result.append(img)
            if len(result) >= limit:
                break
    return result


# ─── GOOGLE IMAGE SEARCH ─────────────────────────────────────────────

async def _search_google(client: httpx.AsyncClient, query: str, limit: int = 5) -> list[str]:
    """Extract image URLs from Google Image Search."""
    url = f"https://www.google.com/search?tbm=isch&q={query}"
    html = await _fetch(client, url, {"User-Agent": USER_AGENT_MOBILE})
    if not html:
        return []

    # Google embeds image URLs inside JS arrays: ["https://...jpg", width, height]
    images = []
    for m in re.finditer(r'"(https?://[^"]+\.(?:jpg|jpeg|png|webp))"', html, re.IGNORECASE):
        img_url = m.group(1)
        if _is_valid_image_url(img_url) and "google" not in img_url.lower():
            images.append(img_url)

    seen = set()
    result = []
    for img in images:
        if img not in seen:
            seen.add(img)
            result.append(img)
            if len(result) >= limit:
                break
    return result


# ─── COMMONS / KNOWN SOURCES ─────────────────────────────────────────

async def _search_wikimedia(client: httpx.AsyncClient, query: str, limit: int = 3) -> list[str]:
    """Search Wikimedia Commons API for images."""
    api_url = (
        "https://commons.wikimedia.org/w/api.php"
        f"?action=query&list=search&srsearch={query}+schematic|diagram+filetype:bitmap"
        "&srnamespace=6&format=json&srlimit=10&origin=*"
    )
    try:
        h = {"User-Agent": f"CarDiagnosticApp/1.0 ({USER_AGENT})"}
        resp = await client.get(api_url, headers=h)
        data = resp.json()
        pages = [p["title"] for p in data.get("query", {}).get("search", [])]
        if not pages:
            return []

        # Get actual image URLs
        titles = "|".join(pages[:limit])
        img_api = (
            "https://commons.wikimedia.org/w/api.php"
            f"?action=query&titles={titles}&prop=imageinfo&iiprop=url"
            "&format=json&origin=*"
        )
        resp2 = await client.get(img_api, headers=h)
        data2 = resp2.json()
        urls = []
        for page in data2.get("query", {}).get("pages", {}).values():
            for ii in page.get("imageinfo", []):
                if ii.get("url"):
                    urls.append(ii["url"])
        return urls[:limit]
    except Exception as exc:
        logger.debug(f"Wikimedia search failed: {exc}")
        return []


# ─── HELPERS ─────────────────────────────────────────────────────────

def _is_valid_image_url(url: str) -> bool:
    """Basic check that URL looks like an image."""
    bad_patterns = [
        "/icon", "/logo", "/avatar", "/thumb", "/favicon",
        "google", "bing", "yandex", "/icons/", "pixel",
        "spacer", "1x1", "transparent", "placeholder",
        "yimg.com", "scorecardresearch",
    ]
    low = url.lower()
    for bp in bad_patterns:
        if bp in low:
            return False
    # Must have an image extension
    if not re.search(r'\.(jpg|jpeg|png|gif|webp|bmp)(\?|$)', low):
        return False
    return True


# ─── DOWNLOAD ─────────────────────────────────────────────────────────

async def download_image(client: httpx.AsyncClient, url: str, timeout: int = 30) -> Optional[bytes]:
    """Download image bytes from URL. Returns None on failure."""
    try:
        h = {"User-Agent": USER_AGENT, "Referer": "https://www.google.com/"}
        resp = await client.get(url, headers=h, timeout=timeout)
        resp.raise_for_status()
        if len(resp.content) < 500:  # too small to be a real image
            return None
        return resp.content
    except Exception as exc:
        logger.debug(f"Image download failed for {url[:80]}: {exc}")
        return None


# ─── PUBLIC API ──────────────────────────────────────────────────────

async def search_and_download(
    error_code: str,
    description: str = "",
    max_images: int = 3,
) -> list[Path]:
    """
    Search multiple sources for schemas related to an error code,
    download images, and return saved file paths.
    """
    # Build search query — легковые + грузовики + автобусы РФ
    desc = (description or "")[:50]
    if description:
        query_ru = f"схема {error_code} {desc} двигатель"
    else:
        query_ru = f"схема OBD2 {error_code} двигатель автомобиль"
    query_en = f"OBD2 {error_code} engine diagram schematic"
    # Российские марки / коммерческий транспорт
    query_lada = f"схема {error_code} LADA ВАЗ расположение датчика"
    query_truck = f"схема {error_code} КАМАЗ ГАЗель дизель common rail"
    query_bus = f"схема {error_code} ПАЗ ЛиАЗ автобус датчик"
    query_maz = f"ошибка {error_code} МАЗ ЯМЗ схема"

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        # Try all sources in parallel (общие + РФ грузовики/автобусы)
        tasks = [
            _search_bing(client, query_ru, max_images * 2),
            _search_bing(client, query_en, max_images),
            _search_bing(client, query_lada, max_images),
            _search_bing(client, query_truck, max_images),
            _search_bing(client, query_bus, max_images),
            _search_google(client, query_ru, max_images),
            _search_google(client, query_truck, max_images),
            _search_google(client, query_maz, max_images),
            _search_wikimedia(client, query_en, max_images),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Flatten and deduplicate
        all_urls = []
        seen = set()
        for r in results:
            if isinstance(r, list):
                for u in r:
                    if u not in seen:
                        seen.add(u)
                        all_urls.append(u)

        # Download images
        downloaded = []
        for i, url in enumerate(all_urls):
            if len(downloaded) >= max_images:
                break

            # Determine extension
            ext = "jpg"
            for e in ["png", "webp", "jpeg", "gif", "bmp"]:
                if f".{e}" in url.lower():
                    ext = e
                    break

            filename = f"{error_code}_{len(downloaded) + 1}.{ext}"
            save_path = DOWNLOAD_DIR / filename

            img_bytes = await download_image(client, url)
            if img_bytes and len(img_bytes) > 500:
                save_path.write_bytes(img_bytes)
                downloaded.append(save_path)
                logger.info(f"Downloaded: {filename} ({len(img_bytes)} bytes)")

        # Update metadata
        if downloaded:
            meta = _load_meta()
            meta[error_code] = {
                "images": [str(p.name) for p in downloaded],
                "urls": all_urls[: len(downloaded)],
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "count": len(downloaded),
            }
            _save_meta(meta)

        return downloaded


async def get_schema(
    error_code: str,
    description: str = "",
    force_refresh: bool = False,
) -> Optional[dict]:
    """
    Get schema images for an error code.
    Returns dict with code, image paths, and metadata,
    or None if no images found.
    """
    # Check existing files
    existing = sorted(DOWNLOAD_DIR.glob(f"{error_code}_*"))
    if existing and not force_refresh:
        meta = _load_meta()
        entry = meta.get(error_code, {})
        return {
            "code": error_code,
            "images": [str(p) for p in existing],
            "cached": True,
            "downloaded_at": entry.get("downloaded_at"),
            "count": len(existing),
        }

    # Search and download
    downloaded = await search_and_download(error_code, description)
    if downloaded:
        return {
            "code": error_code,
            "images": [str(p) for p in downloaded],
            "cached": False,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "count": len(downloaded),
        }

    return None


async def refresh_all_schemas(
    schemas_dict: dict,
) -> dict:
    """
    Refresh all schemas — search and download images for every code.
    schemas_dict: {code: {title, description, ...}} from data.py
    Returns summary dict.
    """
    codes = list(schemas_dict.keys())
    total = len(codes)
    success = 0
    failed = 0
    results = {}

    for code in codes:
        desc = schemas_dict[code].get("description", "")
        try:
            dl = await search_and_download(code, desc)
            if dl:
                success += 1
                results[code] = {"count": len(dl)}
            else:
                failed += 1
                results[code] = {"count": 0, "error": "no images found"}
        except Exception as exc:
            failed += 1
            results[code] = {"count": 0, "error": str(exc)}
            logger.error(f"Failed to refresh schema {code}: {exc}")

        # Small delay between codes to avoid rate limiting
        await asyncio.sleep(0.5)

    summary = {
        "total": total,
        "success": success,
        "failed": failed,
        "results": results,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }

    # Save summary
    meta = _load_meta()
    meta["_last_refresh"] = summary
    _save_meta(meta)

    return summary


def get_download_stats() -> dict:
    """Get statistics about downloaded schemas."""
    meta = _load_meta()
    codes = [k for k in meta if not k.startswith("_")]
    total_images = sum(meta[c].get("count", 0) for c in codes)
    return {
        "total_codes_with_images": len(codes),
        "total_images_downloaded": total_images,
        "last_refresh": meta.get("_last_refresh", {}).get("refreshed_at"),
        "codes": {c: meta[c].get("count", 0) for c in codes},
    }
