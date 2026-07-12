"""
Автономный недельный агент поиска.
Ищет новые решения для кодов ошибок российской автотехники,
валидирует через DeepSeek и пополняет базу знаний.
"""

import httpx
import json
import re
import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
AGENT_LOG = "agent_searches.jsonl"

# Коды ошибок, которые агент исследует (типичные для российских авто)
PRIORITY_CODES = [
    # Зажигание / впрыск
    ("P0300", "ВАЗ"), ("P0301", "ВАЗ"), ("P0302", "ВАЗ"), ("P0303", "ВАЗ"),
    ("P0300", "ГАЗ"), ("P0300", "УАЗ"),
    # Кислородные датчики
    ("P0130", "ВАЗ"), ("P0134", "ВАЗ"), ("P0135", "ВАЗ"),
    # Топливная система
    ("P0171", "ВАЗ"), ("P0172", "ВАЗ"), ("P0230", "ВАЗ"),
    # Катализатор
    ("P0420", "ВАЗ"), ("P0422", "ВАЗ"),
    # Датчики
    ("P0115", "ВАЗ"), ("P0116", "ВАЗ"), ("P0118", "ВАЗ"),
    ("P0325", "ВАЗ"), ("P0335", "ВАЗ"), ("P0340", "ВАЗ"),
    # EGR / клапаны
    ("P0401", "ВАЗ"), ("P0402", "ВАЗ"),
    # Пропуски + система
    ("P0351", "ВАЗ"), ("P0352", "ВАЗ"),
    # ABS / трансмиссия
    ("P0500", "ВАЗ"), ("P0504", "ВАЗ"),
    # ГАЗель / УАЗ
    ("P0300", "ГАЗ"), ("P0171", "ГАЗ"), ("P0335", "УАЗ"),
    ("P0380", "КАМАЗ"), ("P0230", "КАМАЗ"),
]

# Допустимые домены для прямого посещения
ALLOWED_SOURCES = [
    "drive2.ru",
    "auto.ru",
    "diagnost.ru",
    "vaznet.ru",
    "carerrorcodes.ru",
    "chiptuner.ru",
    "ecuteam.ru",
]

# ─── Источники для поиска (drive2.ru, auto.ru, diagnost.ru) ───
SEARCH_SOURCES = [
    {
        "name": "drive2",
        "search_url": "https://www.drive2.ru/search",
        "params": lambda code, brand: {"query": f"{code} {brand} ошибка"},
        "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    },
    {
        "name": "auto_ru",
        "search_url": "https://auto.ru/search/",
        "params": lambda code, brand: {"query": f"{code} {brand} ошибка"},
        "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    },
    {
        "name": "diagnost_ru",
        "search_url": "https://diagnost.ru/forum/search.php",
        "params": lambda code, brand: {"keywords": f"{code} {brand}"},
        "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    },
]


async def search_web(error_code: str, car_brand: str) -> str:
    """
    Поиск информации на drive2.ru, auto.ru, diagnost.ru.
    Возвращает объединённый текст найденных обсуждений.
    """
    results = []

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=12.0,
    ) as client:
        for source in SEARCH_SOURCES:
            try:
                params = source["params"](error_code, car_brand)
                headers = source.get("headers", {})

                resp = await client.get(
                    source["search_url"],
                    params=params,
                    headers=headers,
                )

                if resp.status_code != 200:
                    continue

                text = _extract_text(resp.text)
                if not text or len(text) < 100:
                    continue

                # Вырезаем только фрагменты, содержащие код ошибки
                snippets = _extract_relevant_snippets(
                    text, error_code, car_brand, max_chars=3000
                )

                if snippets:
                    results.append(
                        f"--- {source['name']} ---\n{snippets}"
                    )

            except Exception:
                continue

    return "\n\n".join(results)


def _extract_relevant_snippets(
    text: str, error_code: str, car_brand: str, max_chars: int = 3000
) -> str:
    """
    Извлекает из текста абзацы, содержащие код ошибки или марку авто.
    Ограничивает общий объём до max_chars символов.
    """
    # Разбиваем на предложения по точке, восклицательному и вопросительному знаку
    sentences = re.split(r"(?<=[.!?])\s+", text)

    # Ищем предложения с кодом ошибки или маркой
    error_lower = error_code.lower()
    brand_lower = car_brand.lower()

    relevant = []
    for s in sentences:
        s_lower = s.lower()
        if error_lower in s_lower or brand_lower in s_lower:
            relevant.append(s.strip())

    # Если нашлось слишком мало — берём первые предложения
    if len(relevant) < 3:
        relevant = [s.strip() for s in sentences[:15] if len(s.strip()) > 30]

    # Собираем, не превышая лимит
    result = []
    total = 0
    for s in relevant:
        if total + len(s) > max_chars:
            result.append(s[: max_chars - total] + "…")
            break
        result.append(s)
        total += len(s) + 1

    return "\n".join(result)


def _extract_text(html: str) -> str:
    """Выдирает читаемый текст из HTML."""
    for tag in ("script", "style", "nav", "footer", "header", "aside"):
        html = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>", "", html, flags=re.DOTALL | re.IGNORECASE
        )
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&[a-z]+;", " ", html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()


def _build_research_prompt(error_code: str, car_brand: str, web_text: str, existing_knowledge: str) -> str:
    """Строит промпт для DeepSeek — извлечение кодов, причин, решений."""
    parts = [f"""Ты — агент-аналитик. Извлеки из обсуждений ВСЕ упомянутые коды ошибок,
причины, решения и модели автомобилей для марки {car_brand}.

ИСХОДНЫЙ КОД ДЛЯ ПОИСКА: {error_code}

ПРАВИЛА:
- Извлекай ТОЛЬКО то, что реально упоминается в тексте обсуждений.
- Не выдумывай причины и решения — если в тексте их нет, не добавляй.
- Учитывай особенности {car_brand}: российский климат, качество топлива, износ.
- Каждый пункт — с указанием источника (из какого форума/сайта).
- Игнорируй рекламу, общие фразы, переписку не по делу.
- Если упоминается конкретная модель (например, ВАЗ 2114, ГАЗель Next, УАЗ Патриот) — укажи её.
- Если модель не упоминается — не выдумывай.

ВЕРНИ СТРОГО В ФОРМАТЕ:

[МОДЕЛИ]
- модель 1 (источник: ...)
- модель 2 (источник: ...)
...или НЕТ если модели не указаны

[КОДЫ]
PXXXX — что означает (источник: ...)
PYYYY — что означает (источник: ...)
...или НЕТ если других кодов не найдено

[ПРИЧИНЫ]
- причина 1 [✓/~/?] (источник: ...)
- причина 2 [✓/~/?] (источник: ...)
...или НЕТ если причин не найдено

[РЕШЕНИЯ]
- решение 1 [✓/~/?] (источник: ...)
- решение 2 [✓/~/?] (источник: ...)
...или НЕТ если решений не найдено

[ИСТОЧНИКИ]
- https://... (название сайта)
- https://... (название сайта)
...или НЕТ если источники не определены

МАРКЕРЫ УВЕРЕННОСТИ:
[✓] — подтверждено несколькими источниками
[~] — упоминается, но без подтверждения
[?] — предположение из обсуждения"""]

    if web_text:
        parts.append(f"\n\nОБСУЖДЕНИЯ С ФОРУМОВ:\n{web_text[:5000]}")
    else:
        parts.append("\n\nОБСУЖДЕНИЯ С ФОРУМОВ: (не найдены — используй только существующую базу)")

    if existing_knowledge:
        parts.append(f"\n\nСУЩЕСТВУЮЩАЯ БАЗА ЗНАНИЙ:\n{existing_knowledge[:2000]}")

    parts.append(
        "\n\nЕсли в обсуждениях нет новой информации по {error_code} для {car_brand} — "
        "верни только: НИЧЕГО НОВОГО".format(error_code=error_code, car_brand=car_brand)
    )
    return "\n".join(parts)


async def research_code(
    error_code: str,
    car_brand: str,
    existing_knowledge: str = "",
) -> Optional[dict]:
    """
    Исследует один код ошибки: ищет в вебе, анализирует через DeepSeek.
    Возвращает dict со структурированными данными или None.
    """
    if not DEEPSEEK_API_KEY:
        return None

    # Шаг 1: поиск в вебе
    web_text = await search_web(error_code, car_brand)

    # Шаг 2: анализ через DeepSeek
    prompt = _build_research_prompt(error_code, car_brand, web_text, existing_knowledge)

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                DEEPSEEK_URL,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Ты — агент-аналитик автофорумов. Извлекай коды ошибок, причины "
                                "и решения строго по формату [КОДЫ]/[ПРИЧИНЫ]/[РЕШЕНИЯ]. "
                                "Не выдумывай. Не используй слова «возможно», «наверное», «может быть»."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 1500,
                    "temperature": 0.1,
                },
                timeout=45.0,
            )

            if resp.status_code != 200:
                return None

            data = resp.json()
            result_text = data["choices"][0]["message"]["content"]

            if "НИЧЕГО НОВОГО" in result_text.upper():
                return None

            # Шаг 3: парсим структурированный ответ
            parsed = _parse_findings(result_text)
            web_found = bool(web_text and len(web_text) > 200)

            return {
                "error_code": error_code,
                "car_brand": car_brand,
                "models": parsed["models"],
                "related_codes": parsed["codes"],
                "causes": parsed["causes"],
                "solutions": parsed["solutions"],
                "sources": parsed["sources"],
                "raw_findings": result_text,
                "web_source_used": web_found,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception:
            return None


def _parse_findings(text: str) -> dict:
    """
    Парсит ответ DeepSeek: [МОДЕЛИ] / [КОДЫ] / [ПРИЧИНЫ] / [РЕШЕНИЯ] / [ИСТОЧНИКИ].
    Возвращает: {"models": [...], "codes": [...], "causes": [...], "solutions": [...], "sources": [...]}
    """
    result = {"models": [], "codes": [], "causes": [], "solutions": [], "sources": []}

    # Вырезаем секции
    sections = _split_sections(text)

    # МОДЕЛИ
    if "МОДЕЛИ" in sections:
        models_text = sections["МОДЕЛИ"]
        if "НЕТ" not in models_text.upper() or len(models_text) > 20:
            for line in models_text.strip().split("\n"):
                line = line.strip("-•· \t")
                if line and len(line) > 2 and not line.upper().startswith("НЕТ"):
                    result["models"].append(_clean_line(line))

    # КОДЫ
    if "КОДЫ" in sections:
        codes_text = sections["КОДЫ"]
        if "НЕТ" not in codes_text.upper() or len(codes_text) > 20:
            for line in codes_text.strip().split("\n"):
                line = line.strip("-•· \t")
                if line and not line.upper().startswith("НЕТ"):
                    # Ищем паттерн PXXXX или UXXXX
                    match = re.search(r"\b([PU]\d{4})\b", line, re.IGNORECASE)
                    if match:
                        result["codes"].append({
                            "code": match.group(1).upper(),
                            "description": _clean_line(line),
                        })

    # ПРИЧИНЫ
    if "ПРИЧИНЫ" in sections:
        causes_text = sections["ПРИЧИНЫ"]
        if "НЕТ" not in causes_text.upper() or len(causes_text) > 20:
            for line in causes_text.strip().split("\n"):
                line = line.strip("-•· \t")
                if line and len(line) > 5 and not line.upper().startswith("НЕТ"):
                    confidence = _extract_confidence(line)
                    result["causes"].append({
                        "text": _clean_line(line),
                        "confidence": confidence,
                    })

    # РЕШЕНИЯ
    if "РЕШЕНИЯ" in sections:
        solutions_text = sections["РЕШЕНИЯ"]
        if "НЕТ" not in solutions_text.upper() or len(solutions_text) > 20:
            for line in solutions_text.strip().split("\n"):
                line = line.strip("-•· \t")
                if line and len(line) > 5 and not line.upper().startswith("НЕТ"):
                    confidence = _extract_confidence(line)
                    result["solutions"].append({
                        "text": _clean_line(line),
                        "confidence": confidence,
                    })

    # ИСТОЧНИКИ
    if "ИСТОЧНИКИ" in sections:
        sources_text = sections["ИСТОЧНИКИ"]
        if "НЕТ" not in sources_text.upper() or len(sources_text) > 20:
            for line in sources_text.strip().split("\n"):
                line = line.strip("-•· \t")
                if line and len(line) > 5 and not line.upper().startswith("НЕТ"):
                    url, name = _extract_source(line)
                    if url:
                        result["sources"].append({"url": url, "name": name})

    return result


def _split_sections(text: str) -> dict:
    """Разбивает ответ на секции [МОДЕЛИ], [КОДЫ], [ПРИЧИНЫ], [РЕШЕНИЯ], [ИСТОЧНИКИ]."""
    sections = {}
    current_section = None
    current_lines = []

    for line in text.split("\n"):
        stripped = line.strip()
        upper = stripped.upper().replace(" ", "")

        # Определяем секцию по заголовку
        if upper.startswith("[МОДЕЛИ") or "МОДЕЛИ]" in upper:
            if current_section:
                sections[current_section] = "\n".join(current_lines)
            current_section = "МОДЕЛИ"
            current_lines = []
        elif upper.startswith("[КОДЫ") or "КОДЫ]" in upper:
            if current_section:
                sections[current_section] = "\n".join(current_lines)
            current_section = "КОДЫ"
            current_lines = []
        elif upper.startswith("[ПРИЧИНЫ") or "ПРИЧИНЫ]" in upper:
            if current_section:
                sections[current_section] = "\n".join(current_lines)
            current_section = "ПРИЧИНЫ"
            current_lines = []
        elif upper.startswith("[РЕШЕНИЯ") or "РЕШЕНИЯ]" in upper:
            if current_section:
                sections[current_section] = "\n".join(current_lines)
            current_section = "РЕШЕНИЯ"
            current_lines = []
        elif upper.startswith("[ИСТОЧНИКИ") or "ИСТОЧНИКИ]" in upper:
            if current_section:
                sections[current_section] = "\n".join(current_lines)
            current_section = "ИСТОЧНИКИ"
            current_lines = []
        elif current_section:
            current_lines.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_lines)

    return sections


def _extract_source(line: str) -> tuple:
    """
    Извлекает URL и читаемое имя из строки источника.
    Примеры:
      '- https://drive2.ru/something (Drive2)' -> ('https://drive2.ru/something', 'Drive2')
      '- diagnost.ru (форум диагностов)' -> ('diagnost.ru', 'форум диагностов')
    """
    name = ""
    # Ищем URL в строке
    url_match = re.search(r"(https?://[^\s)]+)", line)
    if url_match:
        url = url_match.group(1).rstrip(".)")
        # Имя из скобок или оставшаяся часть
        name_match = re.search(r"\((.+?)\)", line)
        if name_match:
            name = name_match.group(1).strip()
        else:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                name = parsed.netloc.replace("www.", "")
            except Exception:
                name = url[:50]
        return (url, name)

    # Нет URL — может быть просто "drive2.ru" или "имя сайта"
    domain_match = re.search(r"([\w-]+\.(?:ru|com|org|net|рф))", line)
    if domain_match:
        domain = domain_match.group(1)
        name_match = re.search(r"\((.+?)\)", line)
        name = name_match.group(1).strip() if name_match else domain
        return (domain, name)

    # Просто текст — воспринимаем как название
    clean = re.sub(r"[\(\)]", "", line).strip()
    if clean:
        return (clean.lower().replace(" ", ""), clean)

    return ("", "")


def _extract_confidence(text: str) -> str:
    """Извлекает маркер уверенности [✓], [~] или [?] из строки."""
    if "✓" in text:
        return "high"
    elif "~" in text:
        return "medium"
    elif "?" in text:
        return "low"
    return "unknown"


def _clean_line(line: str) -> str:
    """Убирает мусор из строки: начальные маркеры, лишние пробелы."""
    # Убираем начальные маркеры вроде [✓], [~], [?], но сохраняем их в тексте
    line = line.strip()
    # Убираем только ведущий дефис/точку
    line = re.sub(r"^[-•·]\s*", "", line)
    return line.strip()


def _load_knowledge_base(path: str = "knowledge_base.jsonl"):
    """Загружает все записи из базы знаний."""
    entries = []
    if not os.path.exists(path):
        return entries
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def _get_existing_knowledge(entries: list, error_code: str, car_brand: str) -> str:
    """Извлекает существующие знания по коду+марке."""
    relevant = [
        e
        for e in entries
        if e.get("error_code", "").upper() == error_code.upper()
        and e.get("car_brand", "").upper() == car_brand.upper()
    ]
    if not relevant:
        return ""
    # Берём самую новую запись
    latest = max(relevant, key=lambda e: e.get("timestamp", ""))
    return latest.get("diagnosis", "")[:2000]


def _collect_existing_items(entries: list, error_code: str, car_brand: str) -> dict:
    """
    Собирает все существующие коды/причины/решения из БЗ
    для заданной пары error_code + car_brand.
    Возвращает: {"codes": set(), "causes": set(), "solutions": set()}
    """
    existing = {"models": set(), "codes": set(), "causes": set(), "solutions": set()}

    for entry in entries:
        if entry.get("error_code", "").upper() != error_code.upper():
            continue
        if entry.get("car_brand", "").upper() != car_brand.upper():
            continue

        # Собираем модели
        for m in entry.get("car_models", []):
            if isinstance(m, str):
                existing["models"].add(_normalize_text(m))

        # Собираем коды
        for c in entry.get("related_codes", []):
            if isinstance(c, dict):
                existing["codes"].add(c.get("code", "").upper())
            elif isinstance(c, str):
                existing["codes"].add(c.upper())

        # Собираем причины
        for item in entry.get("causes", []):
            if isinstance(item, dict):
                existing["causes"].add(_normalize_text(item.get("text", "")))
            elif isinstance(item, str):
                existing["causes"].add(_normalize_text(item))

        # Собираем решения
        for item in entry.get("solutions", []):
            if isinstance(item, dict):
                existing["solutions"].add(_normalize_text(item.get("text", "")))
            elif isinstance(item, str):
                existing["solutions"].add(_normalize_text(item))

    return existing


def _normalize_text(text: str) -> str:
    """Нормализует текст для сравнения: нижний регистр, без знаков, ключевые слова."""
    text = text.lower()
    # Убираем маркеры уверенности
    text = re.sub(r"\[[✓~?✓vV]\s*\]", " ", text)
    # Убираем источники в скобках
    text = re.sub(r"\(источник:.*?\)", "", text)
    # Убираем знаки препинания (оставляем буквы, цифры, пробелы)
    text = re.sub(r"[^\w\s]", " ", text)
    # Схлопываем пробелы
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_similar(text1: str, text2: str, threshold: float = 0.55) -> bool:
    """
    Проверяет семантическую схожесть двух фраз.
    Использует коэффициент Жаккара + containment (вложенность).
    """
    words1 = set(_normalize_text(text1).split())
    words2 = set(_normalize_text(text2).split())

    if not words1 or not words2:
        return False

    intersection = words1 & words2
    union = words1 | words2

    # 1) Жаккар
    jaccard = len(intersection) / len(union) if union else 0

    # 2) Containment: сколько слов короткой фразы есть в длинной
    shorter = min(len(words1), len(words2))
    if shorter > 0:
        containment = len(intersection) / shorter
    else:
        containment = 0

    # Схожесть = максимум из двух метрик
    similarity = max(jaccard, containment)

    return similarity >= threshold


def _deduplicate_findings(finding: dict, existing_items: dict) -> dict:
    """
    Отфильтровывает дубликаты из найденных данных.
    Возвращает: {"new_models": [...], "new_codes": [...], "new_causes": [...],
                  "new_solutions": [...], "dup_*": int}
    """
    result = {
        "new_models": [],
        "new_codes": [],
        "new_causes": [],
        "new_solutions": [],
        "dup_models": 0,
        "dup_codes": 0,
        "dup_causes": 0,
        "dup_solutions": 0,
    }

    # МОДЕЛИ (точное совпадение, нормализованное)
    existing_models = existing_items.get("models", set())
    for model in finding.get("models", []):
        norm = _normalize_text(model)
        if norm and norm not in existing_models:
            result["new_models"].append(model)
        else:
            result["dup_models"] += 1

    existing_codes = existing_items.get("codes", set())

    # Проверяем коды (точное совпадение)
    for c in finding.get("related_codes", []):
        code = c.get("code", "").upper() if isinstance(c, dict) else str(c).upper()
        if code and code not in existing_codes:
            result["new_codes"].append(c)
        else:
            result["dup_codes"] += 1

    # Проверяем причины (нечёткое совпадение)
    for item in finding.get("causes", []):
        text = item.get("text", "") if isinstance(item, dict) else str(item)
        norm = _normalize_text(text)
        is_dup = False
        for existing_text in existing_items.get("causes", set()):
            if _is_similar(norm, existing_text):
                is_dup = True
                break
        if is_dup:
            result["dup_causes"] += 1
        else:
            result["new_causes"].append(item)

    # Проверяем решения (нечёткое совпадение)
    for item in finding.get("solutions", []):
        text = item.get("text", "") if isinstance(item, dict) else str(item)
        norm = _normalize_text(text)
        is_dup = False
        for existing_text in existing_items.get("solutions", set()):
            if _is_similar(norm, existing_text):
                is_dup = True
                break
        if is_dup:
            result["dup_solutions"] += 1
        else:
            result["new_solutions"].append(item)

    return result


async def run_weekly_agent(
    max_codes: int = 5,
    skip_recent_hours: int = 24,
    dry_run: bool = False,
) -> dict:
    """
    Запускает недельного агента.
    - max_codes: сколько кодов обработать за один прогон
    - skip_recent_hours: пропускать коды, исследованные недавно
    - dry_run: если True — только отчёт, без сохранения

    Возвращает отчёт: {found, skipped, errors, details: [...]}
    """
    report = {"found": 0, "skipped": 0, "errors": 0, "details": []}

    # Определяем, какие коды уже недавно исследованы
    recent = set()
    if os.path.exists(AGENT_LOG):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=skip_recent_hours)
        with open(AGENT_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = datetime.fromisoformat(entry.get("timestamp", ""))
                    if ts > cutoff:
                        recent.add((entry.get("error_code", ""), entry.get("car_brand", "")))
                except (json.JSONDecodeError, ValueError):
                    continue

    # Загружаем существующую базу знаний
    knowledge = _load_knowledge_base()

    # Обрабатываем приоритетные коды (пропуская недавно исследованные)
    processed = 0
    for error_code, car_brand in PRIORITY_CODES:
        if processed >= max_codes:
            break

        if (error_code, car_brand) in recent:
            report["skipped"] += 1
            continue

        processed += 1

        existing = _get_existing_knowledge(knowledge, error_code, car_brand)

        try:
            finding = await research_code(error_code, car_brand, existing)
        except Exception:
            report["errors"] += 1
            report["details"].append({"code": error_code, "brand": car_brand, "status": "error"})
            continue

        if finding is None:
            report["skipped"] += 1
            report["details"].append(
                {"code": error_code, "brand": car_brand, "status": "nothing_new"}
            )
        else:
            # ─── Дедупликация: проверяем, что из найденного уже есть в БЗ ───
            existing_items = _collect_existing_items(knowledge, error_code, car_brand)
            dedup = _deduplicate_findings(finding, existing_items)

            total_new = (
                len(dedup["new_models"])
                + len(dedup["new_codes"])
                + len(dedup["new_causes"])
                + len(dedup["new_solutions"])
            )
            total_dup = (
                dedup["dup_models"]
                + dedup["dup_codes"]
                + dedup["dup_causes"]
                + dedup["dup_solutions"]
            )

            if total_new == 0:
                # Всё уже есть в базе
                report["skipped"] += 1
                report["details"].append({
                    "code": error_code,
                    "brand": car_brand,
                    "status": "all_duplicates",
                    "duplicates": total_dup,
                })
                # Логируем дубликат
                log_entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "error_code": error_code,
                    "car_brand": car_brand,
                    "status": "all_duplicates",
                    "duplicates_removed": total_dup,
                    "web_source_used": finding.get("web_source_used", False),
                    "dry_run": dry_run,
                }
                with open(AGENT_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
                continue

            report["found"] += 1
            report["details"].append({
                "code": error_code,
                "brand": car_brand,
                "status": "found_new",
                "new_models": len(dedup["new_models"]),
                "new_codes": len(dedup["new_codes"]),
                "new_causes": len(dedup["new_causes"]),
                "new_solutions": len(dedup["new_solutions"]),
                "duplicates": total_dup,
            })

            # Логируем
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error_code": error_code,
                "car_brand": car_brand,
                "car_models": dedup["new_models"],
                "related_codes": [c.get("code", "") for c in dedup["new_codes"] if isinstance(c, dict)],
                "causes_count": len(dedup["new_causes"]),
                "solutions_count": len(dedup["new_solutions"]),
                "sources_count": len(finding.get("sources", [])),
                "duplicates_removed": total_dup,
                "web_source_used": finding.get("web_source_used", False),
                "dry_run": dry_run,
            }
            with open(AGENT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

            if not dry_run:
                # Сохраняем только НОВОЕ в knowledge_base.jsonl
                kb_entry = {
                    "error_code": error_code,
                    "car_brand": car_brand,
                    "car_models": dedup["new_models"],
                    "car_model": "",  # совместимость со старым форматом
                    "diagnosis": finding.get("raw_findings", ""),
                    "related_codes": dedup["new_codes"],
                    "causes": dedup["new_causes"],
                    "solutions": dedup["new_solutions"],
                    "sources": finding.get("sources", []),
                    "duplicates_removed": total_dup,
                    "helpful_count": 0,
                    "not_helpful_count": 0,
                    "timestamp": finding["timestamp"],
                    "source": "weekly_agent",
                }
                with open("knowledge_base.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(kb_entry, ensure_ascii=False) + "\n")

    return report


# Совместимость с синхронным вызовом из FastAPI
def run_weekly_agent_sync(**kwargs):
    """Синхронная обёртка для вызова из FastAPI."""
    return asyncio.run(run_weekly_agent(**kwargs))
