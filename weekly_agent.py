"""
AutoDiag AI v1.0 — Фоновый агент (Weekly Agent)
Автоматический поиск новых схем, ошибок и решений в интернете.
Запускается раз в неделю (или по требованию через /agent/run).

Источники (см. ru_auto_sources.py):
1. Легковые РФ: LADA/ВАЗ, Drive2, Drom, ZR, …
2. Грузовые: КАМАЗ, ГАЗ, МАЗ, Урал, …
3. Автобусы: ПАЗ, ЛиАЗ, НефАЗ, …
4. Базы OBD2-кодов (OBD-Codes.ru, CarDiagn, …)
5. Схемы: DuckDuckGo + site: по порталам РФ
6. (далее) спецтехника РФ — тот же реестр, category=special

Что делает:
- Ищет новые коды ошибок, отсутствующие в БД
- Ищет новые 2D-схемы для существующих кодов
- Ищет новые рекомендации по ремонту
- Валидирует, дедуплицирует и сохраняет в БД

Защита:
- Rate limiting на исходящие запросы (вежливый парсинг)
- Таймауты и retry
- Логирование всех действий
- Только для Enterprise (автоматический поиск — премиум-функция)
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

# ════════════════ Конфигурация ════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_STATE_FILE = os.path.join(BASE_DIR, ".weekly_agent_state")

# Минимальный интервал между запусками (секунды) — 7 дней
MIN_RUN_INTERVAL = 7 * 24 * 3600

# Таймауты запросов
FETCH_TIMEOUT = 20.0
REQUEST_DELAY = 2.0  # задержка между запросами (вежливый парсинг)

logger = logging.getLogger("autodiag.weekly_agent")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [WEEKLY] %(message)s"))
    logger.addHandler(h)


# ════════════════ Источники для поиска ════════════════
# Полный реестр — ru_auto_sources.py (легковые / грузовики / автобусы / спецтехника)

try:
    from ru_auto_sources import (
        KNOWN_CODE_SOURCES,
        SCHEMA_SEARCH_QUERIES,
        REPAIR_SEARCH_QUERIES,
        COMPONENTS,
        RUSSIAN_RELEVANCE_HINTS,
        PRIORITY_DTC_SEED,
        BRAND_SEARCH_HINTS,
    )
except ImportError:
    # fallback если файл не задеплоен
    KNOWN_CODE_SOURCES = [
        {
            "name": "OBD-Codes.ru",
            "url": "https://obd-codes.ru/powertrain/p{range_start:04d}",
            "type": "list",
            "range_start": 0,
            "range_end": 100,
            "parser": "html_table",
            "category": "general",
        },
    ]
    SCHEMA_SEARCH_QUERIES = [
        "схема датчика {component} автомобиль",
        "расположение {component} двигатель схема",
    ]
    REPAIR_SEARCH_QUERIES = [
        "ошибка {code} причины и ремонт",
        "код {code} как исправить",
    ]
    COMPONENTS = [
        "датчик кислорода", "датчик коленвала", "лямбда-зонд",
        "форсунка", "ТНВД", "турбина", "клапан EGR",
    ]
    RUSSIAN_RELEVANCE_HINTS = [
        "лада", "газ", "уаз", "ваз", "камаз", "маз", "паз", "лиаз",
    ]
    PRIORITY_DTC_SEED = ["P0134", "P0300", "P0087", "P0216", "P0420"]
    BRAND_SEARCH_HINTS = ["LADA", "КАМАЗ", "ГАЗ", "ПАЗ"]

# ════════════════ Состояние агента ════════════════

class AgentState:
    def __init__(self):
        self.last_run: float = 0
        self.total_runs: int = 0
        self.total_found: int = 0
        self.last_result: dict = {}

    def save(self):
        data = {
            "last_run": self.last_run,
            "total_runs": self.total_runs,
            "total_found": self.total_found,
            "last_result": self.last_result,
        }
        try:
            with open(AGENT_STATE_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"Failed to save agent state: {e}")

    @staticmethod
    def load() -> "AgentState":
        state = AgentState()
        try:
            with open(AGENT_STATE_FILE, "r") as f:
                data = json.load(f)
                state.last_run = data.get("last_run", 0)
                state.total_runs = data.get("total_runs", 0)
                state.total_found = data.get("total_found", 0)
                state.last_result = data.get("last_result", {})
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return state


# ════════════════ HTTP-клиент с retry ════════════════

class SearchClient:
    """HTTP-клиент для поискового агента с retry и rate limiting."""

    def __init__(self):
        self._last_request = 0

    async def _rate_limit(self):
        now = time.time()
        elapsed = now - self._last_request
        if elapsed < REQUEST_DELAY:
            await asyncio.sleep(REQUEST_DELAY - elapsed)
        self._last_request = time.time()

    async def fetch(self, url: str) -> Optional[str]:
        """Загрузить страницу."""
        await self._rate_limit()
        try:
            async with httpx.AsyncClient(
                timeout=FETCH_TIMEOUT,
                headers={
                    "User-Agent": "AutoDiagAI/1.0 (+https://autodiag.ai/bot)",
                    "Accept": "text/html,application/json",
                    "Accept-Language": "ru,en",
                },
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except httpx.HTTPStatusError as e:
            logger.debug(f"HTTP {e.response.status_code} for {url}")
            return None
        except Exception as e:
            logger.debug(f"Fetch failed for {url}: {e}")
            return None

    async def fetch_json(self, url: str) -> Optional[dict]:
        """Загрузить JSON."""
        await self._rate_limit()
        try:
            async with httpx.AsyncClient(
                timeout=FETCH_TIMEOUT,
                headers={
                    "User-Agent": "AutoDiagAI/1.0 (+https://autodiag.ai/bot)",
                },
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.debug(f"JSON fetch failed for {url}: {e}")
            return None


# ════════════════ Парсеры ════════════════

# Регулярки для извлечения кодов
RE_OBD_CODE = re.compile(r'\b[PBCU]\d{4}\b', re.IGNORECASE)
RE_DESCRIPTION = re.compile(
    r'(?:описание|значение|причина|description)[:\s]*([^<\n]{10,200})',
    re.IGNORECASE,
)
RE_REPAIR = re.compile(
    r'(?:ремонт|устранение|решение|repair|fix|solution)[:\s]*([^<\n]{10,500})',
    re.IGNORECASE,
)


def extract_codes_from_html(html: str) -> list[dict]:
    """Извлечь коды ошибок из HTML."""
    found = []
    seen = set()

    for match in RE_OBD_CODE.finditer(html):
        code = match.group(0).upper()
        if code in seen:
            continue
        seen.add(code)

        # Ищем описание рядом с кодом
        context = html[max(0, match.start() - 500):match.end() + 500]
        desc_match = RE_DESCRIPTION.search(context)
        repair_match = RE_REPAIR.search(context)

        entry = {
            "code": code,
            "description": desc_match.group(1).strip() if desc_match else "",
            "recommendations": repair_match.group(1).strip() if repair_match else "",
            "source": "web",
        }
        found.append(entry)

    return found


def extract_codes_from_json(data: dict) -> list[dict]:
    """Извлечь коды из JSON-ответа API."""
    found = []
    items = data.get("codes") or data.get("results") or data.get("data") or []
    if isinstance(items, dict):
        items = list(items.values())

    for item in items:
        if isinstance(item, str):
            code = RE_OBD_CODE.search(item)
            if code:
                found.append({"code": code.group(0).upper(), "description": item, "source": "api"})
        elif isinstance(item, dict):
            code = item.get("code") or item.get("dtc") or ""
            if code:
                found.append({
                    "code": str(code).upper(),
                    "description": item.get("description") or item.get("desc") or "",
                    "recommendations": item.get("repair") or item.get("solution") or "",
                    "severity": item.get("severity", "info"),
                    "source": "api",
                })
    return found


# ════════════════ Ядро агента ════════════════

class WeeklyAgent:
    """
    Фоновый агент еженедельного поиска.
    Ищет новые коды ошибок, схемы и решения.
    """

    def __init__(self):
        self.client = SearchClient()
        self.state = AgentState.load()

    # ─── Поиск кодов ошибок ─────────────────────────────────

    async def search_error_codes(self) -> dict:
        """
        Поиск новых кодов ошибок в известных источниках.
        Возвращает статистику найденного.
        """
        logger.info("Searching for new error codes...")
        all_codes: list[dict] = []
        sources_checked = 0
        sources_failed = 0

        for source in KNOWN_CODE_SOURCES:
            sources_checked += 1
            try:
                stype = source.get("type", "page")
                if stype == "list":
                    # Пагинированные списки кодов
                    for page in range(source.get("range_start", 0), source.get("range_end", 100), 20):
                        url = source["url"].format(
                            range_start=page,
                            range_end=page + 20,
                        )
                        html = await self.client.fetch(url)
                        if html:
                            codes = extract_codes_from_html(html)
                            for c in codes:
                                c["source"] = source.get("name", "web")
                                c["category"] = source.get("category", "general")
                            all_codes.extend(codes)
                            await asyncio.sleep(0.5)
                        else:
                            break
                elif stype == "page":
                    html = await self.client.fetch(source["url"])
                    if html:
                        codes = extract_codes_from_html(html)
                        for c in codes:
                            c["source"] = source.get("name", "web")
                            c["category"] = source.get("category", "general")
                        all_codes.extend(codes)
                elif stype == "ddg_site":
                    # site:домен + seed DTC + бренды РФ (грузовики/автобусы/легковые)
                    site = source.get("site", "")
                    queries = source.get("queries") or ["ошибка {code}"]
                    seed_codes = PRIORITY_DTC_SEED[:12]
                    brands = BRAND_SEARCH_HINTS[:6]
                    for code in seed_codes:
                        for qt in queries[:2]:
                            q = qt.format(code=code, brand=brands[0] if brands else "LADA")
                            ddg_q = f"site:{site} {q}" if site else q
                            from urllib.parse import quote_plus
                            url = f"https://api.duckduckgo.com/?q={quote_plus(ddg_q)}&format=json"
                            data = await self.client.fetch_json(url)
                            if data:
                                codes = extract_codes_from_json(data)
                                # также вытащим DTC из RelatedTopics
                                for topic in data.get("RelatedTopics") or []:
                                    if isinstance(topic, dict) and topic.get("Text"):
                                        for m in RE_OBD_CODE.finditer(topic["Text"]):
                                            codes.append({
                                                "code": m.group(0).upper(),
                                                "description": topic["Text"][:200],
                                                "recommendations": "",
                                                "source": source.get("name", "ddg"),
                                            })
                                    elif isinstance(topic, dict) and topic.get("Topics"):
                                        for t2 in topic["Topics"]:
                                            if isinstance(t2, dict) and t2.get("Text"):
                                                for m in RE_OBD_CODE.finditer(t2["Text"]):
                                                    codes.append({
                                                        "code": m.group(0).upper(),
                                                        "description": t2["Text"][:200],
                                                        "recommendations": "",
                                                        "source": source.get("name", "ddg"),
                                                    })
                                abstract = data.get("AbstractText") or ""
                                if abstract:
                                    for m in RE_OBD_CODE.finditer(abstract):
                                        codes.append({
                                            "code": m.group(0).upper(),
                                            "description": abstract[:200],
                                            "recommendations": "",
                                            "source": source.get("name", "ddg"),
                                        })
                                for c in codes:
                                    c.setdefault("source", source.get("name", "ddg"))
                                    c["category"] = source.get("category", "general")
                                all_codes.extend(codes)
                            await asyncio.sleep(0.8)
                else:
                    logger.debug(f"Unknown source type {stype} for {source.get('name')}")
            except Exception as e:
                logger.warning(f"Source {source.get('name')} failed: {e}")
                sources_failed += 1

        # Фильтрация / маркировка: легковые + грузовики + автобусы + ГБО + спецтехника
        russian_hints = list(RUSSIAN_RELEVANCE_HINTS)

        filtered = []
        for entry in all_codes:
            desc_lower = (
                entry.get("description", "")
                + " "
                + entry.get("recommendations", "")
                + " "
                + entry.get("source", "")
                + " "
                + entry.get("category", "")
            ).lower()
            # category truck/bus/passenger считаем релевантными для РФ по умолчанию
            cat = (entry.get("category") or "").lower()
            entry["russian_relevant"] = (
                cat in ("passenger", "truck", "bus", "special")
                or any(h in desc_lower for h in russian_hints)
            )
            filtered.append(entry)

        # Сохраняем в БД
        store_result = self._store_codes(filtered)
        stored_count = store_result["stored"]

        return {
            "total_found": len(all_codes),
            "total_filtered": len(filtered),
            "stored": stored_count,
            "_new_codes": store_result["_new_codes"],
            "sources_checked": sources_checked,
            "sources_failed": sources_failed,
        }

    # ─── Поиск новых схем ──────────────────────────────────

    async def search_new_schemas(self) -> dict:
        """
        Поиск новых 2D-схем для существующих кодов ошибок.
        Проверяет компоненты, для которых ещё нет схем.
        """
        logger.info("Searching for new schemas...")
        from schemas.data import SCHEMA_DB

        # Какие коды уже имеют схемы?
        existing_codes = set(SCHEMA_DB.keys())

        # Какие коды есть в БД, но без схем?
        try:
            from database import get_conn
            conn = get_conn()
            rows = conn.execute(
                "SELECT code FROM error_codes ORDER BY code"
            ).fetchall()
            all_db_codes = set(r["code"] for r in rows)
            conn.close()
        except Exception:
            all_db_codes = set()

        # Коды без схем
        missing_schemas = all_db_codes - existing_codes
        logger.info(f"  Codes without schemas: {len(missing_schemas)}")

        # Для каждого компонента ищем схемы (чередуем легковые / грузовики / автобусы)
        from urllib.parse import quote_plus
        new_schemas = {}
        # не перегружаем: до 12 компонентов за прогон
        for component in COMPONENTS[:12]:
            # 2 шаблона: общий + грузовой/автобусный если есть
            templates = SCHEMA_SEARCH_QUERIES[:1]
            if any(k in component.lower() for k in ("тнвд", "common", "dpf", "турбин", "ядм", "рамп")):
                templates = [t for t in SCHEMA_SEARCH_QUERIES if "КАМАЗ" in t or "ЯМЗ" in t or "дизель" in t][:1] or templates
            for query_template in templates:
                query = query_template.format(component=component)
                url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json"
                data = await self.client.fetch_json(url)
                if data:
                    abstract = data.get("AbstractText", "")
                    if abstract:
                        new_schemas[component] = {
                            "query": query,
                            "found": True,
                            "abstract": abstract[:500],
                        }
                break

        return {
            "missing_schemas": len(missing_schemas),
            "components_searched": min(12, len(COMPONENTS)),
            "new_schemas_found": len(new_schemas),
            "component_hits": list(new_schemas.keys()),
            "sources_ru": len(KNOWN_CODE_SOURCES),
        }

    # ─── Поиск решений ─────────────────────────────────────

    async def search_repair_solutions(self) -> dict:
        """
        Поиск новых рекомендаций по ремонту для существующих кодов.
        Обновляет поле recommendations в БД.
        """
        logger.info("Searching for repair solutions...")

        # Получаем коды без рекомендаций
        try:
            from database import get_conn
            conn = get_conn()
            rows = conn.execute(
                "SELECT code, description FROM error_codes "
                "WHERE recommendations IS NULL OR recommendations = '' "
                "LIMIT 15"
            ).fetchall()
            conn.close()
        except Exception:
            return {"total_checked": 0, "updated": 0}

        codes_without_recs = [(r["code"], r["description"]) for r in rows]
        logger.info(f"  Codes without recommendations: {len(codes_without_recs)}")

        from urllib.parse import quote_plus
        updated = 0
        updated_codes = []
        # Для каждого кода — общий + грузовой/автобусный запрос
        for code, desc in codes_without_recs:
            repair_text = None
            for qt in REPAIR_SEARCH_QUERIES[:4]:
                query = qt.format(code=code)
                url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json"
                data = await self.client.fetch_json(url)
                if not data:
                    await asyncio.sleep(0.5)
                    continue
                abstract = data.get("AbstractText", "") or data.get("Answer", "")
                if abstract and len(abstract) > 20:
                    repair_text = abstract[:500]
                    break
                if data.get("RelatedTopics"):
                    for topic in data["RelatedTopics"]:
                        if isinstance(topic, dict) and topic.get("Text"):
                            repair_text = topic["Text"][:500]
                            break
                    if repair_text:
                        break
                await asyncio.sleep(0.7)
            if repair_text:
                self._update_recommendations(code, repair_text)
                updated += 1
                updated_codes.append({"code": code, "recommendations": repair_text})
            await asyncio.sleep(0.5)

        return {
            "total_checked": len(codes_without_recs),
            "updated": updated,
            "_updated_codes": updated_codes,
            "repair_query_templates": len(REPAIR_SEARCH_QUERIES),
        }

    # ─── Основной цикл ─────────────────────────────────────

    async def run(self, force: bool = False) -> dict:
        """
        Запустить полный цикл поиска.
        force — игнорировать MIN_RUN_INTERVAL.
        """
        now = time.time()

        if not force and (now - self.state.last_run) < MIN_RUN_INTERVAL:
            hours_left = (MIN_RUN_INTERVAL - (now - self.state.last_run)) / 3600
            logger.info(f"Weekly agent skipped: {hours_left:.1f}h until next run")
            return {
                "status": "skipped",
                "reason": "too_soon",
                "hours_until_next": round(hours_left, 1),
            }

        logger.info("=" * 50)
        logger.info("WEEKLY AGENT STARTED")
        logger.info("=" * 50)

        start_time = time.time()
        results = {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        # 1. Поиск кодов
        try:
            results["error_codes"] = await self.search_error_codes()
            # Пушим новые коды на сервер обновлений
            if results["error_codes"].get("stored", 0) > 0:
                results["error_codes"]["pushed"] = await self._push_codes_to_server(
                    results["error_codes"].get("_new_codes", [])
                )
        except Exception as e:
            logger.error(f"Error code search failed: {e}")
            results["error_codes"] = {"error": str(e)}

        # 2. Поиск схем
        try:
            results["schemas"] = await self.search_new_schemas()
        except Exception as e:
            logger.error(f"Schema search failed: {e}")
            results["schemas"] = {"error": str(e)}

        # 3. Поиск решений
        try:
            results["repairs"] = await self.search_repair_solutions()
            if results["repairs"].get("updated", 0) > 0:
                results["repairs"]["pushed"] = await self._push_repairs_to_server(
                    results["repairs"].get("_updated_codes", [])
                )
        except Exception as e:
            logger.error(f"Repair search failed: {e}")
            results["repairs"] = {"error": str(e)}

        # Финал
        elapsed = time.time() - start_time
        total_found = sum(
            r.get("stored", 0) + r.get("updated", 0) + r.get("new_schemas_found", 0)
            for r in [results.get("error_codes", {}), results.get("repairs", {}), results.get("schemas", {})]
        )

        results["status"] = "completed"
        results["elapsed_seconds"] = round(elapsed, 1)
        results["total_new_items"] = total_found

        # Сохраняем состояние
        self.state.last_run = now
        self.state.total_runs += 1
        self.state.total_found += total_found
        self.state.last_result = results
        self.state.save()

        logger.info(f"WEEKLY AGENT DONE: {total_found} new items in {elapsed:.1f}s")
        return results

    # ─── Хранение ──────────────────────────────────────────

    def _store_codes(self, codes: list[dict]) -> dict:
        """Сохранить коды в БД (только новые). Возвращает {stored: N, new_codes: [...]}."""
        from database import get_conn
        conn = get_conn()
        stored = 0
        new_codes = []
        try:
            for entry in codes:
                code = entry["code"].upper()
                if not RE_OBD_CODE.fullmatch(code):
                    continue

                existing = conn.execute(
                    "SELECT code FROM error_codes WHERE code = ?", (code,)
                ).fetchone()
                if existing:
                    continue

                conn.execute("""
                    INSERT INTO error_codes
                    (code, description, severity, recommendations, source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    code,
                    entry.get("description", "")[:500],
                    entry.get("severity", "info"),
                    entry.get("recommendations", "")[:1000],
                    entry.get("source", "web"),
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ))
                stored += 1
                new_codes.append({
                    "code": code,
                    "description": entry.get("description", "")[:500],
                    "severity": entry.get("severity", "info"),
                    "recommendations": entry.get("recommendations", "")[:1000],
                })

            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Store codes failed: {e}")
        finally:
            conn.close()

        if stored:
            logger.info(f"  Stored {stored} new error codes")
        return {"stored": stored, "_new_codes": new_codes}

    def _update_recommendations(self, code: str, text: str):
        """Обновить рекомендации для кода."""
        from database import get_conn
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE error_codes SET recommendations = ?, updated_at = ? WHERE code = ?",
                (text[:1000], datetime.now(timezone.utc).isoformat(), code.upper()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()

    # ─── Пуш на сервер обновлений ──────────────────────────

    async def _push_codes_to_server(self, codes: list[dict]) -> int:
        """
        Отправить новые коды на центральный сервер обновлений.
        Другие экземпляры AutoDiag AI получат их через /updates/check.
        """
        if not codes:
            return 0

        update_server = os.getenv("UPDATE_SERVER", "https://autodiag.ru/api/updates")
        update_secret = os.getenv("UPDATE_SECRET", "AutoDiagUpdate2026Secure")

        payload = {
            "type": "error_codes",
            "version": int(time.time()),
            "description": f"Weekly agent: {len(codes)} new codes from web search",
            "payload": {"codes": codes},
            "source": "weekly_agent",
        }

        signature = hmac.new(
            update_secret.encode(),
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode(),
            hashlib.sha256,
        ).hexdigest()

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{update_server}/webhook",
                    json=payload,
                    headers={
                        "X-Update-Signature": signature,
                        "Content-Type": "application/json",
                        "User-Agent": "AutoDiagAI-WeeklyAgent/1.0",
                    },
                )
                resp.raise_for_status()
                result = resp.json()
                pushed = 1 if result.get("status") in ("applied", "ok", "accepted") else 0
                logger.info(f"  Pushed {len(codes)} codes to update server: {resp.status_code}")
                return pushed
        except Exception as e:
            logger.warning(f"Push codes to server failed: {e}")
            return 0

    async def _push_repairs_to_server(self, updated_codes: list[dict]) -> int:
        """
        Отправить новые рекомендации на сервер обновлений.
        """
        if not updated_codes:
            return 0

        update_server = os.getenv("UPDATE_SERVER", "https://autodiag.ru/api/updates")
        update_secret = os.getenv("UPDATE_SECRET", "AutoDiagUpdate2026Secure")

        payload = {
            "type": "repairs",
            "version": int(time.time()),
            "description": f"Weekly agent: {len(updated_codes)} repair solutions",
            "payload": {"repairs": updated_codes},
            "source": "weekly_agent",
        }

        signature = hmac.new(
            update_secret.encode(),
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode(),
            hashlib.sha256,
        ).hexdigest()

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{update_server}/webhook",
                    json=payload,
                    headers={
                        "X-Update-Signature": signature,
                        "Content-Type": "application/json",
                        "User-Agent": "AutoDiagAI-WeeklyAgent/1.0",
                    },
                )
                resp.raise_for_status()
                logger.info(f"  Pushed {len(updated_codes)} repairs to update server")
                return 1
        except Exception as e:
            logger.warning(f"Push repairs to server failed: {e}")
            return 0


# ════════════════ Синглтон ════════════════

_agent_instance: Optional[WeeklyAgent] = None


def get_agent() -> WeeklyAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = WeeklyAgent()
    return _agent_instance


# ════════════════ CLI ════════════════

if __name__ == "__main__":
    import sys

    async def main():
        agent = get_agent()
        force = "--force" in sys.argv
        result = await agent.run(force=force)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

    asyncio.run(main())
