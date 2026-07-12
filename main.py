# Импортируем библиотеки
# FastAPI — это фреймворк для создания API (как конструктор для веб-серверов)
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
# Pydantic — проверяет, правильные ли данные пришли к нам в запросе
from pydantic import BaseModel
# httpx — отправляет HTTP-запросы наружу (в DeepSeek)
import httpx
# os — чтобы читать переменные окружения (например, ключ DeepSeek)
import os
import json
from pathlib import Path
# Optional — указывает, что поле может быть пустым
from typing import Optional

# Автономный недельный агент поиска
import auto_search

# ─── ChromaDB — векторная база для семантического поиска ───
_chroma_available = False
_chroma_collection = None
_chroma_path = None

try:
    import chromadb
    from chromadb.utils import embedding_functions

    # Клиент Chroma (данные хранятся в папке chroma_db рядом с main.py)
    _chroma_path = str(Path(__file__).parent / "chroma_db")
    _chroma_client = chromadb.PersistentClient(path=_chroma_path)

    # Функция эмбеддинга: sentence-transformers для русского языка
    _chroma_embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )

    # Коллекция для хранения проверенных решений
    _chroma_collection = _chroma_client.get_or_create_collection(
        name="diagnoses",
        embedding_function=_chroma_embedder,
        metadata={"description": "Проверенные решения диагностики OBD2"}
    )
    _chroma_available = True
    print(f"[ChromaDB] Инициализирована. Путь: {_chroma_path}")
except Exception as e:
    print(f"[ChromaDB] Недоступна. Векторный поиск отключён. Ошибка: {e}")

# Создаём приложение FastAPI
# Это наш "сервер" — он будет слушать запросы
app = FastAPI()

# ─── Middleware: принудительный UTF-8 для всех JSON-ответов ───
# Без явного charset=utf-8 .NET HttpClient декодирует JSON как ISO-8859-1,
# что приводит к кракозябрам вместо русского текста.
from starlette.middleware.base import BaseHTTPMiddleware

class UTF8CharsetMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        ct = response.headers.get("content-type", "")
        if ct and "application/json" in ct and "charset" not in ct:
            response.headers["content-type"] = f"{ct}; charset=utf-8"
        return response

app.add_middleware(UTF8CharsetMiddleware)

# ─── Фоновый планировщик для недельного агента ───
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = BackgroundScheduler(daemon=True)

def _weekly_agent_job():
    """Фоновая задача: недельный прогон агента поиска."""
    try:
        report = auto_search.run_weekly_agent_sync(
            max_codes=8,
            skip_recent_hours=168,  # 7 дней — не повторять одно и то же
            dry_run=False,
        )
        print(f"[WEEKLY-AGENT] Прогон завершён: найдено={report['found']}, "
              f"пропущено={report['skipped']}, ошибок={report['errors']}")
    except Exception as e:
        print(f"[WEEKLY-AGENT] Ошибка: {e}")

# Запуск: каждый понедельник в 03:00 UTC
scheduler.add_job(
    _weekly_agent_job,
    trigger=CronTrigger(day_of_week="mon", hour=3, minute=0),
    id="weekly_agent",
    name="Недельный агент поиска",
    replace_existing=True,
    misfire_grace_time=3600,  # если проспали — запустить в течение часа
)
scheduler.start()
print("[SCHEDULER] Недельный агент запущен (понедельник, 03:00 UTC)")

# ─── In-memory кэш успешных решений ───
# Ключ: (error_code, car_brand), Значение: {"diagnosis": str, "helpful_count": int, "not_helpful_count": int}
# Загружается при старте из knowledge_base.jsonl, обновляется при каждом диагнозе и отзыве.
successful_solutions: dict = {}

@app.on_event("startup")
async def load_successful_solutions():
    """При старте сервера загружаем проверенные решения из базы знаний в память и Chroma."""
    global successful_solutions
    try:
        entries = _load_knowledge()
        for e in entries:
            code = e.get("error_code", "")
            brand = e.get("car_brand", "")
            helpful = e.get("helpful_count", 0)
            not_helpful = e.get("not_helpful_count", 0)
            if not code or not brand:
                continue
            key = (code, brand)
            existing = successful_solutions.get(key)
            # Берём запись с наибольшим helpful_count
            if existing is None or helpful > existing.get("helpful_count", 0):
                successful_solutions[key] = {
                    "diagnosis": e.get("diagnosis", ""),
                    "helpful_count": helpful,
                    "not_helpful_count": not_helpful,
                    "car_brand": brand,
                    "car_model": e.get("car_model", ""),
                    "created_at": e.get("created_at", ""),
                    "updated_at": e.get("updated_at", "")
                }
        # Индексируем проверенные записи в Chroma
        _chroma_index_all()
    except Exception:
        pass  # Не роняем сервер, если файла ещё нет

@app.on_event("shutdown")
async def shutdown_scheduler():
    """Останавливаем планировщик при выключении сервера."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("[SCHEDULER] Остановлен.")

def _update_cache(error_code, car_brand, diagnosis, helpful_count=0, not_helpful_count=0):
    """Обновляет in-memory кэш successful_solutions."""
    global successful_solutions
    key = (error_code, car_brand)
    existing = successful_solutions.get(key)
    if existing is None or helpful_count > existing.get("helpful_count", 0):
        successful_solutions[key] = {
            "diagnosis": diagnosis,
            "helpful_count": helpful_count,
            "not_helpful_count": not_helpful_count,
            "car_brand": car_brand,
            "updated_at": __import__("datetime").datetime.now().isoformat()
        }

def _get_cached_solution(error_code, car_brand=None):
    """Ищет решение в in-memory кэше. Сначала точное совпадение по бренду, затем любое."""
    global successful_solutions
    if car_brand:
        key = (error_code, car_brand)
        entry = successful_solutions.get(key)
        if entry and entry.get("helpful_count", 0) > entry.get("not_helpful_count", 0):
            return [entry]
    # Ищем среди всех брендов
    matches = [
        v for (code, _), v in successful_solutions.items()
        if code == error_code and v.get("helpful_count", 0) > v.get("not_helpful_count", 0)
    ]
    matches.sort(key=lambda e: e.get("helpful_count", 0), reverse=True)
    return matches[:2]

# ─── ChromaDB: векторная долгосрочная память ───

def _chroma_index_all():
    """Индексирует все проверенные записи из knowledge_base.jsonl в Chroma."""
    if not _chroma_available:
        return
    entries = _load_knowledge()
    verified = [
        e for e in entries
        if e.get("helpful_count", 0) > e.get("not_helpful_count", 0)
        and e.get("helpful_count", 0) >= 1
    ]
    if not verified:
        return

    # Удаляем старые и загружаем заново (полная переиндексация)
    try:
        existing_ids = _chroma_collection.get()["ids"]
        if existing_ids:
            _chroma_collection.delete(ids=existing_ids)
    except Exception:
        pass

    ids, docs, metadatas = [], [], []
    for i, e in enumerate(verified):
        doc = f"Ошибка: {e.get('error_code','')}. Марка: {e.get('car_brand','')} {e.get('car_model','')}. {e.get('diagnosis','')[:1500]}"
        ids.append(f"kb_{i}")
        docs.append(doc)
        metadatas.append({
            "error_code": e.get("error_code", ""),
            "car_brand": e.get("car_brand", ""),
            "car_model": e.get("car_model", ""),
            "helpful_count": e.get("helpful_count", 0),
            "not_helpful_count": e.get("not_helpful_count", 0),
            "source": "knowledge_base"
        })

    batch_size = 50
    for start in range(0, len(ids), batch_size):
        end = start + batch_size
        try:
            _chroma_collection.add(
                ids=ids[start:end],
                documents=docs[start:end],
                metadatas=metadatas[start:end]
            )
        except Exception:
            pass

def _chroma_search(query_text, error_code=None, car_brand=None, n_results=3):
    """
    Семантический поиск по векторной базе.
    Возвращает список словарей с diagnosis и метаданными.
    """
    if not _chroma_available:
        return []
    try:
        # Строим поисковый запрос: код ошибки + контекст
        search_query = query_text
        if error_code:
            search_query = f"Код ошибки {error_code}. {query_text}"

        results = _chroma_collection.query(
            query_texts=[search_query],
            n_results=n_results
        )

        matches = []
        if results and results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                dist = results["distances"][0][i] if results["distances"] else 1.0
                matches.append({
                    "diagnosis": doc,
                    "error_code": meta.get("error_code", ""),
                    "car_brand": meta.get("car_brand", ""),
                    "car_model": meta.get("car_model", ""),
                    "helpful_count": meta.get("helpful_count", 0),
                    "not_helpful_count": meta.get("not_helpful_count", 0),
                    "similarity": round(1.0 - dist, 3) if dist <= 1.0 else 0.0
                })
        return matches
    except Exception:
        return []

def _chroma_upsert(error_code, car_brand, car_model, diagnosis, helpful_count=1):
    """Добавляет или обновляет запись в векторной базе."""
    if not _chroma_available:
        return
    try:
        doc = f"Ошибка: {error_code}. Марка: {car_brand} {car_model or ''}. {diagnosis[:1500]}"
        uid = f"kb_{error_code}_{car_brand}"

        # Удаляем старую запись с таким же ID, если есть
        try:
            _chroma_collection.delete(ids=[uid])
        except Exception:
            pass

        _chroma_collection.add(
            ids=[uid],
            documents=[doc],
            metadatas=[{
                "error_code": error_code,
                "car_brand": car_brand,
                "car_model": car_model or "",
                "helpful_count": helpful_count,
                "not_helpful_count": 0,
                "source": "feedback"
            }]
        )
    except Exception:
        pass  # Не роняем сервер из-за ошибок Chroma

def _chroma_delete(error_code, car_brand):
    """Удаляет запись из векторной базы."""
    if not _chroma_available:
        return
    try:
        uid = f"kb_{error_code}_{car_brand}"
        _chroma_collection.delete(ids=[uid])
    except Exception:
        pass

def _save_to_history(error_code, car_brand, car_model, diagnosis_text):
    """Сохраняет запись в diagnoses.jsonl (история диагностик)."""
    history_path = Path(__file__).parent / "diagnoses.jsonl"
    try:
        timestamp = __import__("datetime").datetime.now().isoformat()
        snippet = diagnosis_text[:200].replace("\n", " ").strip()
        record = {
            "error_code": error_code,
            "car_brand": car_brand,
            "car_model": car_model,
            "snippet": snippet,
            "timestamp": timestamp
        }
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass

# Описываем, как должен выглядеть запрос от мобильного приложения
# Это как бланк, который пользователь должен заполнить
class DiagnoseRequest(BaseModel):
    error_code: str          # Например, "P0340"
    car_brand: str           # Например, "ВАЗ"
    car_model: Optional[str] = None  # Например, "2114" (необязательно)
    analytics_context: Optional[str] = None  # Аналитика: повторяемость, связки, риск
    follow_up_context: Optional[str] = None  # Ответ пользователя на уточняющий вопрос

# Берем API-ключ DeepSeek из переменных окружения Render
# Так ключ не светится в коде — это безопасно
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
# Адрес DeepSeek API — куда будем отправлять запросы
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

# ─── Проверка ответа на галлюцинации и запрещённые слова-маркеры ───

HALLUCINATION_LOG = "hallucinations.jsonl"

FORBIDDEN_WORDS = [
    "возможно", "наверное", "может быть", "вероятно", "скорее всего",
    "не исключено", "как правило", "в большинстве случаев", "обычно", "как бы",
]

def _check_hallucinations(text, error_code="", car_brand=""):
    """
    Проверяет ответ DeepSeek на признаки галлюцинаций и слова-маркеры.
    Возвращает (disclaimer, warnings, found_any).
    disclaimer — для вставки в начало ответа; warnings — в конец.
    """
    import re
    import json
    from datetime import datetime, timezone

    warnings = []
    disclaimers = []

    # ── 0. Запрещённые слова-маркеры неуверенности ──
    found_forbidden = []
    text_lower = text.lower()
    for word in FORBIDDEN_WORDS:
        if word.lower() in text_lower:
            found_forbidden.append(word)

    if found_forbidden:
        words_str = ", ".join(f'«{w}»' for w in found_forbidden)
        warnings.append(f"  • Слова-маркеры неуверенности: {words_str}")
        disclaimers.append(
            "⚠️ ВНИМАНИЕ: в ответе найдены слова-маркеры неуверенности "
            f"({words_str}). Информация ниже может быть неточной. "
            "Рекомендуется перепроверить данные в официальных источниках."
        )

    # ── 1. Номера деталей ──
    part_patterns = [
        r'\b\d{4}[-–]\d{6,}',
        r'\b\d{2,4}[-–]\d{2,4}[-–]\d{3,}',
        r'(?:артикул|каталожный номер|catalog)[\s:]*\d',
    ]
    for pattern in part_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            warnings.append(f"  • Возможные номера деталей: {', '.join(matches[:3])}")
            break

    # ── 2. Цены в рублях ──
    price_patterns = [
        r'\b\d[\d\s]*\s*(?:₽|руб|р\.)\b',
        r'(?:цена|стоимость|стоит)[\s:]*\d',
    ]
    for pattern in price_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            warnings.append("  • Упоминание цены (может быть устаревшей)")
            break

    # ── 3. Моменты затяжки ──
    if re.search(r'(?:Н·м|Нм|кгс·м|момент затяжки|затягивать с моментом)', text, re.IGNORECASE):
        warnings.append("  • Указан момент затяжки (требует проверки по мануалу)")

    # ── 4. Точное время ремонта ──
    if re.search(r'(?:ремонт займ[её]т|время ремонта)[\s:]*\d+\s*(?:мин|час)', text, re.IGNORECASE):
        warnings.append("  • Указано точное время ремонта (может отличаться)")

    # ── 5. Модели ЭБУ/датчиков ──
    if re.search(r'(?:датчик|ЭБУ|контроллер)\s+(?:Bosch|Siemens|Delphi|Январь|МИКАС)[\s\d.]*', text, re.IGNORECASE):
        warnings.append("  • Названа модель ЭБУ/датчика (может не соответствовать)")

    # ── 6. Проверка безопасности: опасные процедуры без предупреждения ──
    danger_keywords = [
        "топливн", "бензонасос", "форсунк",  # топливная система
        "подушк", "airbag", "srs",             # подушки безопасности
        "грм", "ремень грм", "цепь грм",      # газораспределительный механизм
        "эбу", "контроллер", "прошивк",       # электроника
        "высоковольт", "катушк", "трамблер",  # зажигание
        "стартер", "генератор",                # электрооборудование
    ]
    found_danger = []
    for kw in danger_keywords:
        if kw.lower() in text.lower():
            found_danger.append(kw)
    if found_danger:
        text_lower_check = text.lower()
        # Проверяем, упомянуты ли метки безопасности
        has_safety_tag = (
            "только специалист" in text_lower_check
            or "[только специалист]" in text_lower_check
            or "⚠" in text
            or "осторожно" in text_lower_check
        )
        if not has_safety_tag:
            warnings.append(
                f"  ⚠️ Опасные процедуры без предупреждения: "
                f"{', '.join(found_danger[:3])}. Добавлена метка безопасности."
            )
            disclaimers.append(
                "⚠️ ВНИМАНИЕ: в ответе найдены советы по опасным процедурам "
                "(топливная система, подушки безопасности, ГРМ, ЭБУ и т.д.) "
                "без явного предупреждения. "
                "ВСЕГДА консультируйтесь со специалистом перед ремонтом!"
            )

    found_any = bool(warnings or disclaimers)

    # ── Запись в лог ──
    if found_any:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error_code": error_code,
            "car_brand": car_brand,
            "forbidden_words": found_forbidden,
            "warning_count": len(warnings),
            "warnings": warnings,
            "response_preview": text[:300]
        }
        try:
            with open(HALLUCINATION_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    disclaimer = "\n\n".join(disclaimers) if disclaimers else ""
    warning_text = "\n".join(warnings) + "\n  Перепроверьте информацию по официальному мануалу." if warnings else ""

    return disclaimer, warning_text, found_any

# Обработчик POST-запроса на адрес /diagnose
# Когда приложение отправит сюда данные, запустится эта функция
@app.post("/diagnose")
async def diagnose(request: DiagnoseRequest) -> JSONResponse:
    # Проверяем, есть ли ключ DeepSeek
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="API ключ не настроен")

    # ── Шаг 1: проверяем in-memory кэш ──
    # Если для этой ошибки+марки уже есть проверенное решение — отдаём сразу
    cached = _get_cached_solution(request.error_code, request.car_brand)
    if cached and not request.analytics_context:
        entry = cached[0]
        diagnosis_text = entry["diagnosis"]
        # Сохраняем в историю
        _save_to_history(request.error_code, request.car_brand, request.car_model, diagnosis_text)
        return JSONResponse(
            content={
                "diagnosis": diagnosis_text,
                "error_code": request.error_code,
                "car": f"{request.car_brand} {request.car_model or ''}".strip(),
                "source": "cache"
            },
            media_type="application/json; charset=utf-8"
        )

    # ── Шаг 2: семантический поиск в векторной базе Chroma ──
    # Ищем похожие решения даже если точного совпадения по коду нет
    chroma_matches = _chroma_search(
        query_text=f"{request.error_code} {request.car_brand} {request.car_model or ''}",
        error_code=request.error_code,
        car_brand=request.car_brand,
        n_results=3
    )
    # Если нашли очень похожее (similarity > 0.85) — отдаём сразу без DeepSeek
    if chroma_matches and chroma_matches[0]["similarity"] >= 0.85 and not request.analytics_context:
        best = chroma_matches[0]
        diagnosis_text = best["diagnosis"]
        _save_to_history(request.error_code, request.car_brand, request.car_model, diagnosis_text)
        return JSONResponse(
            content={
                "diagnosis": diagnosis_text,
                "error_code": request.error_code,
                "car": f"{request.car_brand} {request.car_model or ''}".strip(),
                "source": "chroma"
            },
            media_type="application/json; charset=utf-8"
        )

    # ── Шаг 3: формируем промпт с БЗ + Chroma как референсом ──
    knowledge_block = ""
    verified = cached if cached else _find_verified_knowledge(request.error_code, request.car_brand)
    if verified:
        kb_parts = ["\nПРОВЕРЕННЫЕ ОТВЕТЫ ИЗ БАЗЫ ЗНАНИЙ (используй как референс):"]
        for i, v in enumerate(verified):
            helpful = v.get("helpful_count", 0)
            not_helpful = v.get("not_helpful_count", 0)
            kb_parts.append(f"--- Вариант {i+1} (👍{helpful} / 👎{not_helpful}, {v.get('car_brand', '')} {v.get('car_model', '')}):\n{v.get('diagnosis', '')[:1200]}")
        knowledge_block = "\n".join(kb_parts)

    # Добавляем семантически похожие решения из Chroma (если есть)
    if chroma_matches:
        if not knowledge_block:
            knowledge_block = "\nСЕМАНТИЧЕСКИ ПОХОЖИЕ РЕШЕНИЯ ИЗ ПАМЯТИ:"
        else:
            knowledge_block += "\n\nТАКЖЕ ПОХОЖИЕ СЛУЧАИ (семантический поиск):"
        for i, m in enumerate(chroma_matches):
            sim_pct = int(m["similarity"] * 100)
            knowledge_block += (
                f"\n--- Похожий случай {i+1} (сходство {sim_pct}%, "
                f"код {m.get('error_code','')}, {m.get('car_brand','')}):\n"
                f"{m.get('diagnosis','')[:800]}"
            )

    if knowledge_block:
        knowledge_block += "\n\nТы можешь улучшить эти ответы на основе нового контекста. Не копируй дословно — адаптируй.\n"

    analytics_block = ""
    if request.analytics_context:
        analytics_block = f"""
 ДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ ДЛЯ АНАЛИЗА:
 {request.analytics_context}

 Используй эту аналитику, чтобы выявить корневую причину.
 Если ошибка повторяющаяся — укажи, почему она возвращается после сброса.
 Если есть связки ошибок — объясни, как они связаны причинно-следственно.
 """
    follow_up_block = ""
    if request.follow_up_context:
        follow_up_block = f"""
 ОТВЕТ ПОЛЬЗОВАТЕЛЯ НА УТОЧНЯЮЩИЙ ВОПРОС:
 {request.follow_up_context}

 Уточни диагноз с учётом этой информации. Это продолжение предыдущего диалога —
 пользователь ответил на твой вопрос. Дай более точный ответ, отталкиваясь от новых данных.
 Убери секцию «Уточняющие вопросы» из этого ответа (она была в предыдущем).
 """
    # ─── ЖЁСТКИЙ ПРОМПТ С ЗАПРЕТОМ ГАЛЛЮЦИНАЦИЙ ───

    system_prompt = """ТЫ — ДИАГНОСТИЧЕСКИЙ АССИСТЕНТ С ЖЁСТКИМИ ОГРАНИЧЕНИЯМИ.

ПРАВИЛА. НАРУШЕНИЕ НЕДОПУСТИМО:

 1. ЗАПРЕЩЕНО ВЫДУМЫВАТЬ:
    - номера деталей и каталожные номера
    - артикулы запчастей
    - цены в рублях
    - моменты затяжки (Н·м)
    - точное время ремонта
    - конкретные модели датчиков или ЭБУ

 2. ЗАПРЕЩЕНО ПРИДУМЫВАТЬ ПРИЧИНЫ:
    - Называй только те причины, которые действительно известны для этой ошибки на этой марке.
    - Если информации недостаточно — честно скажи об этом вместо сочинения правдоподобных причин.
    - Не используй общие шаблонные фразы (типа «проверьте проводку») без привязки к конкретной ошибке.

 3. ЗАПРЕЩЕНО ИСПОЛЬЗОВАТЬ СЛОВА-ПАРАЗИТЫ НЕУВЕРЕННОСТИ:
    - «возможно», «наверное», «может быть», «вероятно», «скорее всего», «не исключено»
    - «как правило», «в большинстве случаев», «обычно», «как бы»
    - Вместо них используй маркеры уверенности: [✓] [~] [?]
    - Пиши прямо и утвердительно, без размытых формулировок

 4. КАЖДЫЙ СОВЕТ ДОЛЖЕН СОДЕРЖАТЬ ССЫЛКУ НА ИСТОЧНИК:
     - Указывай конкретную ссылку (URL) после каждого утверждения.
     - Формат: «(источник: https://www.drive2.ru/...)»
     - Если в базе знаний есть решение с URL — используй этот URL.
     - Если точной ссылки нет — укажи домен и путь: «(источник: drive2.ru, поиск: P0300 ВАЗ)»
     - ЗАПРЕЩЕНО выдумывать несуществующие ссылки.
     - Если ни ссылки, ни источника указать невозможно — совет давать НЕЛЬЗЯ.

 5. УРОВНИ УВЕРЕННОСТИ — ставь в начале каждого утверждения:
    [✓] — типовая неисправность, подтверждённая для российских авто
    [~] — вероятная причина (требует проверки)
    [?] — предположение (данных недостаточно)

 6. ЕСЛИ НЕ УВЕРЕН — используй фразу:
    «⚠️ Недостаточно проверенных данных. Рекомендуется ручная диагностика.»
    Если по ошибке/марке нет никаких проверенных данных — напиши:
    «Точной информации нет.»

 7. ПРИЧИНЫ — только для российских автомобилей (ВАЗ, КАМАЗ, УАЗ, ГАЗ, ПАЗ, ЛАЗ, ЛиАЗ, КАвЗ).
    Не используй информацию о зарубежных брендах.

 8. УСЛОВИЯ ЭКСПЛУАТАЦИИ — учитывай: холодный климат, плохие дороги, качество топлива.

 9. ОТВЕТСТВЕННОСТЬ — если ошибка редкая или нестандартная, честно предупреди.

 10. БЕЗОПАСНОСТЬ РЕМОНТА — в секции 3 (способы устранения) каждый пункт
     начинай с метки уровня опасности:
       [Безопасно] — можно сделать самому без риска (проверка проводов, замена свечей)
       [Осторожно] — требует базовых навыков и аккуратности (работа с датчиками, чистка дросселя)
       [Только специалист] — высокое напряжение, топливная система, подушки безопасности,
                             разборка двигателя, ГРМ, ЭБУ
      Если процедура опасна для жизни — добавь ⚠️ в начало строки.
      ЗАПРЕЩЕНО советовать [Только специалист] без ⚠️ и без источника.

 11. УТОЧНЯЮЩИЕ ВОПРОСЫ — если данных недостаточно для уверенного диагноза:
      - Вместо «Точной информации нет.» задай 1–3 конкретных уточняющих вопроса.
      - Вопросы должны помогать сузить круг причин.
      - Формат: добавь секцию «5. Уточняющие вопросы» с нумерованным списком.
      - Примеры хороших вопросов:
        • «Горит ли Check Engine постоянно или мигает?»
        • «Появляется ли ошибка на холодном или на горячем двигателе?»
        • «Была ли недавно замена ремня ГРМ или цепи?»
      - После вопросов продолжай давать доступную информацию — не отказывайся от ответа полностью.

 ДОВЕРЕННЫЕ ИСТОЧНИКИ ДАННЫХ (ссылайся на них в ответах):
  - carerrorcodes.ru — база кодов ошибок OBD2 с расшифровкой
  - autodata.ru — технические регламенты и спецификации
  - drive2.ru — опыт реальных владельцев, бортовые журналы
  - diagnost.ru — профессиональный форум диагностов
  - forum.uazbuka.ru — крупнейший форум владельцев УАЗ, опыт ремонта
  - forum.vaz.ru — форум владельцев ВАЗ, технические обсуждения
  - forum.kamaz.ru — форум владельцев КАМАЗ, опыт ремонта грузовиков
  - auto.ru — обсуждения, ремонт, статьи
  - Регламенты производителей (ВАЗ, ГАЗ, УАЗ, КАМАЗ) — официальные техкарты
  - Данные из нашей базы знаний — проверенные пользователями решения"""

    user_prompt = f"""Код ошибки: {request.error_code}
Марка: {request.car_brand}
Модель: {request.car_model or "не указана"}
{knowledge_block}
 {analytics_block}
 {follow_up_block}
 ФОРМАТ ОТВЕТА (строго):

1. Расшифровка ошибки
   - Краткое описание ошибки [✓]
   - На что влияет, критичность

 2. Вероятные причины (ровно 3, с уровнем уверенности)
    Каждая причина с меткой [✓], [~] или [?]
    Для каждой причины — учёт условий: холод, качество топлива, износ
    ЗАПРЕЩЕНО: придумывать причины, которых нет в базе знаний.
    Если проверенных причин для этой ошибки нет — используй [?] и предупреди.

  3. Способы устранения (от простого к сложному, с уровнем уверенности)
     Каждый способ с меткой безопасности [Безопасно]/[Осторожно]/[Только специалист]
     Каждый способ с источником: (источник: ...)
     Опасные операции помечай ⚠️

 4. Рекомендация
    - Можно ли продолжать движение [✓] или нужен эвакуатор [?]
    - Что проверить в первую очередь

  ФОРМАТ ИСТОЧНИКОВ:
  - В идеале — конкретный URL: «(источник: https://www.drive2.ru/l/12345678/)»
  - Если URL неизвестен — домен и путь: «(источник: drive2.ru, поиск: P0300 ВАЗ)»
  - Ссылки должны быть реальными. Не выдумывай URL.

 ВАЖНО:
- Если нет проверенных данных для {request.car_brand} — начни ответ с: «⚠️ По данной ошибке для {request.car_brand} недостаточно проверенных данных.»
- Не придумывай несуществующие симптомы.
- Не называй конкретные номера деталей."""

    # Отправляем запрос в DeepSeek
    async with httpx.AsyncClient() as client:
        response = await client.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json; charset=utf-8"
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "max_tokens": 1500,
                "temperature": 0.1  # Минимальная температура — строгость, без креативности
            },
            timeout=30.0
        )
    
    # Проверяем, всё ли хорошо
    if response.status_code != 200:
        # Если DeepSeek вернул ошибку — передаём её дальше
        raise HTTPException(status_code=response.status_code, detail=response.text)
    
    # Извлекаем текст ответа
    deepseek_json = response.json()
    diagnosis_text = deepseek_json["choices"][0]["message"]["content"]

    # ─── Валидация: поиск галлюцинаций и запрещённых слов-маркеров ───
    disclaimer, warnings, has_issues = _check_hallucinations(
        diagnosis_text, error_code=request.error_code, car_brand=request.car_brand
    )
    if disclaimer:
        diagnosis_text = disclaimer + "\n\n" + diagnosis_text
    if warnings:
        diagnosis_text += f"\n\n⚠️ АВТОМАТИЧЕСКАЯ ПРОВЕРКА НАШЛА ПРИЗНАКИ ГАЛЛЮЦИНАЦИЙ:\n{warnings}"

    # Сохраняем в историю
    _save_to_history(request.error_code, request.car_brand, request.car_model, diagnosis_text)

    # Возвращаем структурированный JSON
    has_clarifying = "5. Уточняющие вопросы" in diagnosis_text or "5. уточняющие вопросы" in diagnosis_text
    return JSONResponse(
        content={
            "diagnosis": diagnosis_text,
            "error_code": request.error_code,
            "car": f"{request.car_brand} {request.car_model or ''}".strip(),
            "source": "deepseek" if not verified and not chroma_matches else "deepseek+memory",
            "has_clarifying_questions": has_clarifying,
        },
        media_type="application/json; charset=utf-8"
    )

# Обработчик GET-запроса на /car_brands
# Отдаёт список всех марок и моделей из cars.json
@app.get("/car_brands")
async def car_brands():
    # cars.json лежит в той же папке, что и main.py
    cars_path = Path(__file__).parent / "cars.json"
    if not cars_path.exists():
        raise HTTPException(status_code=500, detail="cars.json не найден")
    with open(cars_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data

# Обработчик GET-запроса на корневой адрес /
# Просто чтобы проверить, что сервер жив
@app.get("/")
async def root():
    return {"status": "ok", "message": "Сервер работает"}


@app.get("/health")
async def health():
    """Эндпоинт проверки здоровья — используется клиентом для определения онлайн-статуса."""
    return {"status": "ok"}

# Обработчик POST-запроса на /feedback
# Принимает отзыв пользователя (помогло / не помогло) и сохраняет в JSON-файл,
# а также обновляет базу знаний для самообучения AI
@app.post("/feedback")
async def feedback(request: dict):
    error_code = request.get("error_code", "неизвестно")
    helpful = request.get("helpful", False)
    comment = request.get("comment", None)
    car_brand = request.get("car_brand", "")
    car_model = request.get("car_model", "")
    diagnosis = request.get("diagnosis", "")

    # Записываем отзыв в JSON-файл для последующего анализа
    feedback_path = Path(__file__).parent / "feedback.jsonl"
    try:
        record = {
            "error_code": error_code,
            "helpful": helpful,
            "car_brand": car_brand,
            "timestamp": __import__("datetime").datetime.now().isoformat()
        }
        if comment:
            record["comment"] = comment
        with open(feedback_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Не прерываем работу, если записать не удалось

    # Сохраняем решение в базу знаний ТОЛЬКО если пользователь нажал «Помогло»
    if helpful and car_brand and diagnosis:
        try:
            _upsert_knowledge(error_code, car_brand, car_model, diagnosis)
            # Сразу помечаем как проверенное (helpful_count=1)
            _record_feedback_knowledge(error_code, car_brand, helpful=True)
            # Добавляем в векторную базу Chroma для долгосрочной памяти
            _chroma_upsert(error_code, car_brand, car_model, diagnosis)
        except Exception:
            pass
    elif car_brand:
        # Если «Не помогло» — просто обновляем счётчик
        try:
            _record_feedback_knowledge(error_code, car_brand, helpful)
        except Exception:
            pass

    return JSONResponse(
        content={"status": "ok"},
        media_type="application/json; charset=utf-8"
    )

# Обработчик GET-запроса на /history
# Возвращает последние N записей диагностики (по умолчанию 20)
@app.get("/history")
async def history(limit: int = 20):
    history_path = Path(__file__).parent / "diagnoses.jsonl"
    entries = []

    if history_path.exists():
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # Берём последние N, разворачиваем (сначала новые)
            for line in reversed(lines[-limit:]):
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        except Exception:
            pass

    return entries

# Обработчик DELETE-запроса на /history
# Полностью очищает diagnoses.jsonl
@app.delete("/history")
async def clear_history():
    history_path = Path(__file__).parent / "diagnoses.jsonl"
    status_msg = "ok"
    try:
        if history_path.exists():
            history_path.unlink()
    except Exception:
        status_msg = "error"
    return {"status": status_msg}

# ─── База знаний (самообучение) ───

# GET /knowledge?error_code=P0300&brand=ВАЗ
# Возвращает сохранённые знания по коду ошибки
@app.get("/knowledge")
async def get_knowledge(error_code: str = "", brand: Optional[str] = None):
    entries = _load_knowledge()

    if error_code:
        entries = [e for e in entries if e.get("error_code") == error_code]
    if brand:
        entries = [e for e in entries if e.get("car_brand") == brand]

    # Сортируем: сначала с наибольшим helpful_count
    entries.sort(key=lambda e: e.get("helpful_count", 0), reverse=True)
    return entries[:20]

# GET /knowledge/stats
# Возвращает статистику базы знаний
@app.get("/knowledge/stats")
async def knowledge_stats():
    entries = _load_knowledge()
    total = len(entries)
    verified = sum(1 for e in entries if e.get("helpful_count", 0) > e.get("not_helpful_count", 0))
    total_helpful = sum(e.get("helpful_count", 0) for e in entries)
    total_not = sum(e.get("not_helpful_count", 0) for e in entries)

    codes = {}
    for e in entries:
        code = e.get("error_code", "")
        codes[code] = codes.get(code, 0) + 1
    top_codes = sorted(codes.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "total_entries": total,
        "verified_entries": verified,
        "total_helpful": total_helpful,
        "total_not_helpful": total_not,
        "top_codes": [{"code": c, "count": n} for c, n in top_codes]
    }
# GET /memory/stats
# Возвращает статистику векторной базы Chroma
@app.get("/memory/stats")
async def memory_stats():
    if not _chroma_available:
        return {"engine": "chromadb", "status": "unavailable", "reason": "chromadb not installed"}
    try:
        count = _chroma_collection.count()
        return {
            "engine": "chromadb",
            "collection": "diagnoses",
            "total_vectors": count,
            "model": "paraphrase-multilingual-MiniLM-L12-v2",
            "storage_path": _chroma_path
        }
    except Exception as e:
        return {"error": str(e)}

# ─── Вспомогательные функции для базы знаний ───

def _knowledge_path():
    return Path(__file__).parent / "knowledge_base.jsonl"

def _load_knowledge():
    """Загружает все записи из knowledge_base.jsonl."""
    kp = _knowledge_path()
    entries = []
    if kp.exists():
        with open(kp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    return entries

def _save_knowledge(entries):
    """Перезаписывает knowledge_base.jsonl."""
    kp = _knowledge_path()
    with open(kp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

def _find_verified_knowledge(error_code, car_brand=None):
    """
    Ищет проверенные (helpful > not_helpful) записи знаний.
    Сначала ищет точное совпадение по бренду, затем общие.
    """
    entries = _load_knowledge()
    verified = [
        e for e in entries
        if e.get("error_code") == error_code
        and e.get("helpful_count", 0) > e.get("not_helpful_count", 0)
        and e.get("helpful_count", 0) >= 1
    ]
    if car_brand:
        brand_match = [e for e in verified if e.get("car_brand") == car_brand]
        if brand_match:
            brand_match.sort(key=lambda e: e.get("helpful_count", 0), reverse=True)
            return brand_match[:2]
    verified.sort(key=lambda e: e.get("helpful_count", 0), reverse=True)
    return verified[:2]

def _upsert_knowledge(error_code, car_brand, car_model, diagnosis):
    """Добавляет или обновляет запись в базе знаний."""
    entries = _load_knowledge()
    now = __import__("datetime").datetime.now().isoformat()

    # Ищем точное совпадение
    for e in entries:
        if (e.get("error_code") == error_code
            and e.get("car_brand") == car_brand
            and e.get("diagnosis") == diagnosis):
            e["updated_at"] = now
            _save_knowledge(entries)
            # Обновляем in-memory кэш
            _update_cache(error_code, car_brand, diagnosis,
                          e.get("helpful_count", 0),
                          e.get("not_helpful_count", 0))
            return

    # Новая запись
    entry = {
        "error_code": error_code,
        "car_brand": car_brand,
        "car_model": car_model,
        "diagnosis": diagnosis,
        "helpful_count": 0,
        "not_helpful_count": 0,
        "created_at": now,
        "updated_at": now
    }
    entries.append(entry)
    _save_knowledge(entries)
    _update_cache(error_code, car_brand, diagnosis, 0, 0)

def _record_feedback_knowledge(error_code, car_brand, helpful):
    """Обновляет счётчики helpful/not_helpful в базе знаний."""
    entries = _load_knowledge()
    now = __import__("datetime").datetime.now().isoformat()

    # Ищем самую свежую запись для этого кода+бренда
    best = None
    best_idx = -1
    for i, e in enumerate(entries):
        if e.get("error_code") == error_code and e.get("car_brand") == car_brand:
            if best is None or e.get("updated_at", "") > best.get("updated_at", ""):
                best = e
                best_idx = i

    if best is not None:
        if helpful:
            best["helpful_count"] = best.get("helpful_count", 0) + 1
        else:
            best["not_helpful_count"] = best.get("not_helpful_count", 0) + 1
        best["updated_at"] = now
        _save_knowledge(entries)

        # Обновляем in-memory кэш
        _update_cache(error_code, car_brand,
                      best.get("diagnosis", ""),
                      best.get("helpful_count", 0),
                      best.get("not_helpful_count", 0))

# ========================================================================
# ЗАДАЧА 11: АВТОНОМНЫЙ НЕДЕЛЬНЫЙ АГЕНТ ПОИСКА
# ========================================================================

@app.get("/admin/weekly-agent")
async def run_weekly_agent(dry_run: bool = False, max_codes: int = 5):
    """
    Запускает недельного агента поиска.
    Исследует коды ошибок, ищет новую информацию,
    валидирует через DeepSeek и пополняет базу знаний.

    dry_run=true — только отчёт без сохранения
    max_codes=N  — сколько кодов обработать (1–20)
    """
    if max_codes < 1 or max_codes > 20:
        return {"error": "max_codes должен быть от 1 до 20"}

    try:
        report = await auto_search.run_weekly_agent(
            max_codes=max_codes,
            skip_recent_hours=24,
            dry_run=dry_run,
        )
        return {"status": "ok", "mode": "dry_run" if dry_run else "live", **report}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/admin/agent-log")
async def get_agent_log(limit: int = 20):
    """
    Возвращает последние записи из лога агента.
    """
    log_path = "agent_searches.jsonl"
    if not os.path.exists(log_path):
        return {"entries": [], "total": 0}

    entries = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # Последние сверху
    entries.reverse()
    return {"entries": entries[:limit], "total": len(entries)}


@app.get("/admin/scheduler-status")
async def get_scheduler_status():
    """
    Статус планировщика: когда следующий запуск, последние прогоны.
    """
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
            "trigger": str(job.trigger),
        })

    # Последние прогоны из лога
    log_path = "agent_searches.jsonl"
    last_runs = []
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        ts = entry.get("timestamp", "")
                        mode = "dry_run" if entry.get("dry_run") else "live"
                        last_runs.append({
                            "timestamp": ts,
                            "code": entry.get("error_code"),
                            "brand": entry.get("car_brand"),
                            "mode": mode,
                        })
                    except json.JSONDecodeError:
                        continue

    return {
        "scheduler_running": scheduler.running,
        "jobs": jobs,
        "last_runs": last_runs[-10:],  # последние 10
    }


# ========================================================================
# ЗАДАЧА 12: ОФЛАЙН-РЕЖИМ И АВТООБНОВЛЕНИЕ
# ========================================================================

SYNC_FILES = {
    "diagnoses": "diagnoses.jsonl",
    "knowledge": "knowledge_base.jsonl",
    "feedback_log": "feedback.jsonl",
    "diagrams": "diagrams.jsonl",
}


@app.get("/sync")
async def sync_data(since: str = "", car_brand: str = "", limit: int = 50):
    """
    Эндпоинт для автообновления клиента.
    Возвращает дельту данных с указанной метки времени.

    Параметры:
      since     — ISO timestamp, вернуть только записи новее этого времени
      car_brand — опционально, фильтр по марке
      limit     — макс. количество записей (1–200)

    Ответ:
      {
        "server_time": "2026-07-06T22:00:00+00:00",
        "diagnoses": [...],
        "knowledge": [...],
        "has_more": false
      }
    """
    if limit < 1 or limit > 200:
        limit = 50

    result = {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "diagnoses": [],
        "knowledge": [],
        "has_more": False,
    }

    # ── Диагнозы ──
    diag_path = SYNC_FILES["diagnoses"]
    if os.path.exists(diag_path):
        diag_entries = []
        with open(diag_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = entry.get("timestamp", "")
                    if since and ts <= since:
                        continue
                    if car_brand and entry.get("car_brand", "").upper() != car_brand.upper():
                        continue
                    diag_entries.append(entry)
                except json.JSONDecodeError:
                    continue

        diag_entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        result["diagnoses"] = diag_entries[:limit]
        result["has_more"] = len(diag_entries) > limit

    # ── База знаний (только verified) ──
    kb_path = SYNC_FILES["knowledge"]
    if os.path.exists(kb_path):
        kb_entries = []
        with open(kb_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = entry.get("updated_at", entry.get("created_at", ""))
                    if since and ts <= since:
                        continue
                    if car_brand and entry.get("car_brand", "").upper() != car_brand.upper():
                        continue
                    # Только проверенные или от агента
                    helpful = entry.get("helpful_count", 0)
                    not_helpful = entry.get("not_helpful_count", 0)
                    if helpful > not_helpful or entry.get("source") == "weekly_agent":
                        kb_entries.append(entry)
                except json.JSONDecodeError:
                    continue

        kb_entries.sort(key=lambda e: e.get("updated_at", e.get("created_at", "")), reverse=True)
        result["knowledge"] = kb_entries[:limit]

    return result


class UploadRequest(BaseModel):
    error_code: str
    car_brand: str
    car_model: Optional[str] = None
    diagnosis: str
    source: Optional[str] = "client_offline"


@app.post("/sync/upload")
async def sync_upload(request: UploadRequest):
    """
    Принимает готовый диагноз от клиента (например, найденный офлайн)
    и сохраняет в diagnoses.jsonl + базу знаний без вызова DeepSeek.
    """
    _save_to_history(request.error_code, request.car_brand,
                     request.car_model, request.diagnosis)

    _upsert_knowledge(request.error_code, request.car_brand,
                      request.car_model, request.diagnosis)

    return {
        "status": "ok",
        "error_code": request.error_code,
        "source": request.source,
    }


@app.get("/sync/summary")
async def sync_summary():
    """
    Краткая сводка для клиента: количество новых записей с последней синхронизации.
    Используется для показа бейджа «обновлений» без загрузки всех данных.
    """
    return {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "total_diagnoses": _count_lines(SYNC_FILES["diagnoses"]),
        "total_knowledge": _count_lines(SYNC_FILES["knowledge"]),
        "total_feedback": _count_lines(SYNC_FILES["feedback_log"]),
    }


def _count_lines(path: str) -> int:
    """Считает непустые строки в JSONL-файле."""
    if not os.path.exists(path):
        return 0
    count = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1
    return count


# ═══════════════════════════════════════════════════════════
# ЭТАП 7: Облачная синхронизация между пользователями
# ═══════════════════════════════════════════════════════════


class KnowledgeEntry(BaseModel):
    """Запись базы знаний для синхронизации."""
    error_code: str
    car_brand: str = ""
    car_model: str = ""
    diagnosis: str = ""
    source: str = "client_sync"
    confidence: float = 0.5
    first_seen_at: str = ""
    last_seen_at: str = ""


class BulkKnowledgeUpload(BaseModel):
    entries: list[KnowledgeEntry]
    client_id: str = ""


class DiagnosisEntry(BaseModel):
    """Запись истории диагностики для синхронизации."""
    error_code: str
    car_brand: str = ""
    car_model: str = ""
    error_type: str = ""
    diagnosis_snippet: str = ""
    risk_score: int = 0
    is_recurring: bool = False
    detected_at: str = ""


class BulkDiagnosisUpload(BaseModel):
    entries: list[DiagnosisEntry]
    client_id: str = ""


class DiagramEntry(BaseModel):
    """Метаданные схемы для синхронизации (без изображения)."""
    error_code: str = ""
    car_brand: str = ""
    car_model: str = ""
    title: str = ""
    description: str = ""
    source_url: str = ""
    created_at: str = ""


class BulkDiagramUpload(BaseModel):
    entries: list[DiagramEntry]
    client_id: str = ""


@app.post("/sync/upload-knowledge")
async def sync_upload_knowledge(request: BulkKnowledgeUpload):
    """
    Массовая загрузка базы знаний от клиента.
    Принимает массив KnowledgeEntry и добавляет в knowledge_base.jsonl
    с дедубликацией по (error_code, car_brand, car_model).
    """
    path = SYNC_FILES["knowledge"]
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    skipped = 0

    # Загружаем существующие записи для дедубликации
    existing = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    key = (e.get("error_code", ""), e.get("car_brand", ""), e.get("car_model", ""))
                    existing.add(key)
                except json.JSONDecodeError:
                    continue

    with open(path, "a", encoding="utf-8") as f:
        for entry in request.entries:
            key = (entry.error_code, entry.car_brand, entry.car_model)
            if key in existing:
                skipped += 1
                continue
            record = {
                "error_code": entry.error_code,
                "car_brand": entry.car_brand,
                "car_model": entry.car_model,
                "diagnosis": entry.diagnosis,
                "source": entry.source,
                "confidence": entry.confidence,
                "created_at": entry.first_seen_at or now,
                "updated_at": entry.last_seen_at or now,
                "helpful_count": 0,
                "not_helpful_count": 0,
                "client_id": request.client_id,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            existing.add(key)
            added += 1

    return {"status": "ok", "added": added, "skipped": skipped}


@app.post("/sync/upload-diagnostics")
async def sync_upload_diagnostics(request: BulkDiagnosisUpload):
    """
    Массовая загрузка истории диагностик от клиента.
    Принимает массив DiagnosisEntry и добавляет в diagnoses.jsonl.
    """
    path = SYNC_FILES["diagnoses"]
    added = 0

    with open(path, "a", encoding="utf-8") as f:
        for entry in request.entries:
            record = {
                "error_code": entry.error_code,
                "car_brand": entry.car_brand,
                "car_model": entry.car_model,
                "error_type": entry.error_type,
                "snippet": entry.diagnosis_snippet[:200],
                "risk_score": entry.risk_score,
                "is_recurring": entry.is_recurring,
                "timestamp": entry.detected_at or datetime.now(timezone.utc).isoformat(),
                "client_id": request.client_id,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            added += 1

    return {"status": "ok", "added": added}


@app.post("/sync/upload-diagrams")
async def sync_upload_diagrams(request: BulkDiagramUpload):
    """
    Массовая загрузка метаданных схем от клиента.
    Принимает массив DiagramEntry и добавляет в diagrams.jsonl.
    """
    path = SYNC_FILES["diagrams"]
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    skipped = 0

    existing = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    key = (e.get("source_url", ""),)
                    existing.add(key)
                except json.JSONDecodeError:
                    continue

    with open(path, "a", encoding="utf-8") as f:
        for entry in request.entries:
            if (entry.source_url,) in existing:
                skipped += 1
                continue
            record = {
                "error_code": entry.error_code,
                "car_brand": entry.car_brand,
                "car_model": entry.car_model,
                "title": entry.title,
                "description": entry.description,
                "source_url": entry.source_url,
                "created_at": entry.created_at or now,
                "client_id": request.client_id,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            existing.add((entry.source_url,))
            added += 1

    return {"status": "ok", "added": added, "skipped": skipped}


@app.get("/sync/knowledge")
async def sync_knowledge(since: str = "", limit: int = 50):
    """
    Загрузка только базы знаний (без диагнозов).
    Используется клиентом для синхронизации общих знаний.

    Параметры:
      since — ISO timestamp, вернуть только записи новее этого времени
      limit — макс. количество (1–200)
    """
    if limit < 1 or limit > 200:
        limit = 50

    path = SYNC_FILES["knowledge"]
    result = {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "entries": [],
        "has_more": False,
    }

    if not os.path.exists(path):
        return result

    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("updated_at", entry.get("created_at", ""))
                if since and ts <= since:
                    continue
                entries.append(entry)
            except json.JSONDecodeError:
                continue

    entries.sort(key=lambda e: e.get("updated_at", e.get("created_at", "")), reverse=True)
    result["entries"] = entries[:limit]
    result["has_more"] = len(entries) > limit

    return result


@app.get("/sync/diagrams")
async def sync_diagrams(since: str = "", limit: int = 50):
    """
    Загрузка метаданных схем из облака.

    Параметры:
      since — ISO timestamp, вернуть только записи новее этого времени
      limit — макс. количество (1–200)
    """
    if limit < 1 or limit > 200:
        limit = 50

    path = SYNC_FILES["diagrams"]
    result = {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "entries": [],
        "has_more": False,
    }

    if not os.path.exists(path):
        return result

    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("created_at", "")
                if since and ts <= since:
                    continue
                entries.append(entry)
            except json.JSONDecodeError:
                continue

    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    result["entries"] = entries[:limit]
    result["has_more"] = len(entries) > limit

    return result


# ─── Модель для анализа живых данных ───

class LivePidItem(BaseModel):
    """Один PID для анализа."""
    name: str           # Название (RPM, Coolant, ...)
    value: float        # Текущее значение
    unit: str = ""      # Единица измерения
    min_val: float = 0  # Минимум диапазона
    max_val: float = 0  # Максимум диапазона
    severity: int = 0   # 0=норма, 1=внимание, 2=опасно


class LiveAnalyzeRequest(BaseModel):
    """Запрос на AI-анализ живых данных."""
    car_brand: str = ""
    car_model: str = ""
    pids: list[LivePidItem]


# ══════════════════════════════════════════════════════════════
#  ЭТАП 3: ПОИСК СХЕМ В ИНТЕРНЕТЕ
# ══════════════════════════════════════════════════════════════
#
#  Приоритет поиска:
#    1. Google Custom Search API (searchType=image) — если заданы ключи
#    2. DuckDuckGo Lite (веб-поиск) — fallback
#    3. Прямые ссылки на Yandex.Картинки + форумы — если поиск не дал результатов
#  API-ключи для Google CSE читаются из переменных окружения:
#    GOOGLE_CSE_KEY  — API key (из Google Cloud Console)
#    GOOGLE_CSE_CX   — Search Engine ID (из Programmable Search Engine)

import re
import urllib.parse
import os

# Ключи Google Custom Search (из переменных окружения)
_GOOGLE_CSE_KEY = os.environ.get("GOOGLE_CSE_KEY", "")
_GOOGLE_CSE_CX   = os.environ.get("GOOGLE_CSE_CX", "")

_GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"


async def _search_google_cse(query: str, max_results: int = 8, search_images: bool = True) -> list[dict]:
    """
    Поиск через Google Custom Search API.
    Если search_images=True — ищет картинки (searchType=image).
    Возвращает: [{"title", "url", "snippet", "thumbnail", "source"}]
    """
    if not _GOOGLE_CSE_KEY or not _GOOGLE_CSE_CX:
        return []

    results = []
    try:
        params = {
            "key": _GOOGLE_CSE_KEY,
            "cx": _GOOGLE_CSE_CX,
            "q": query,
            "num": min(max_results, 10),
            "lr": "lang_ru",        # русскоязычные результаты
            "gl": "ru",             # регион Россия
        }
        if search_images:
            params["searchType"] = "image"
            params["imgSize"] = "medium"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(_GOOGLE_CSE_URL, params=params)

        if resp.status_code != 200:
            print(f"[SCHEME-SEARCH] Google CSE error {resp.status_code}: {resp.text[:200]}")
            return results

        data = resp.json()

        for item in data.get("items", [])[:max_results]:
            img = item.get("image", {})
            result = {
                "title": item.get("title", ""),
                "url": item.get("link", ""),                     # URL страницы с картинкой
                "snippet": item.get("snippet", ""),
                "thumbnail": img.get("thumbnailLink", ""),       # миниатюра для предпросмотра
                "image_url": img.get("thumbnailLink", ""),       # копия (совместимость)
                "source": "google_cse",
            }

            if search_images:
                # Для картинок добавляем ссылку на полноразмерное изображение
                result["full_image_url"] = item.get("link", "")  # полная картинка
                # Контекстная ссылка (страница, где найдена картинка)
                ctx = item.get("image", {}).get("contextLink", "")
                result["page_url"] = ctx if ctx else item.get("link", "")
                if ctx:
                    result["snippet"] = f"📷 {item.get('title', '')} — {ctx}"

            # Чистим title
            title = result["title"]
            if title and len(title) > 120:
                title = title[:120] + "…"
            result["title"] = re.sub(r'\s+', ' ', title).strip()

            if result["url"]:
                results.append(result)

    except Exception as e:
        print(f"[SCHEME-SEARCH] Google CSE fetch error: {e}")

    return results


async def _fetch_ddg_results(query: str, max_results: int = 8) -> list[dict]:
    """
    Ищет через DuckDuckGo Lite (без JS) и возвращает список результатов.
    Каждый результат: {"title": str, "url": str, "snippet": str, "thumbnail": "", "source": "ddg"}
    """
    results = []
    try:
        # DDG Lite URL — минимальный HTML без JavaScript
        url = "https://lite.duckduckgo.com/lite/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            )
        }
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(url, params={"q": query}, headers=headers)

        if resp.status_code != 200:
            return results

        html = resp.text

        # Парсим ссылки из HTML таблицы результатов DDG Lite
        link_pattern = re.compile(
            r'<a\s+(?:[^>]*?\s+)?href="(https?://[^"]+)"[^>]*>'
            r'(.*?)</a>',
            re.IGNORECASE | re.DOTALL
        )
        snippet_pattern = re.compile(
            r'<td[^>]*class="[^"]*result-snippet[^"]*"[^>]*>'
            r'\s*(.*?)\s*</td>',
            re.IGNORECASE | re.DOTALL
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        for i, (url_found, title_raw) in enumerate(links):
            if i >= max_results:
                break
            if "duckduckgo.com" in url_found or url_found.startswith("//"):
                continue
            title = re.sub(r'<[^>]+>', '', title_raw).strip()
            if not title:
                continue
            snippet = ""
            if i < len(snippets):
                snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()
                if len(snippet) > 200:
                    snippet = snippet[:200] + "…"

            results.append({
                "title": title,
                "url": url_found,
                "snippet": snippet,
                "thumbnail": "",
                "source": "ddg",
            })

    except Exception as e:
        print(f"[SCHEME-SEARCH] DDG fetch error: {e}")

    return results


def _build_scheme_queries(error_code: str, car_brand: str, car_model: str) -> list[str]:
    """
    Строит поисковые запросы для поиска схем.
    Запросы различаются для картинок и веб-поиска.
    Возвращает (image_queries, web_queries).
    """
    brand_model = f"{car_brand} {car_model}".strip()
    code_clean = error_code.strip().upper()

    # Запросы для поиска КАРТИНОК (схемы, рисунки, фото)
    image_queries = []
    if brand_model:
        image_queries.append(f'схема двигателя {brand_model} датчик {code_clean}')
        image_queries.append(f'расположение датчиков {brand_model} {code_clean} фото')
    if car_brand:
        image_queries.append(f'схема моторного отсека {car_brand} {code_clean}')
    image_queries.append(f'engine diagram sensor location {code_clean}')

    # Запросы для ВЕБ-поиска (статьи, форумы)
    web_queries = []
    if brand_model:
        web_queries.append(f'{code_clean} {brand_model} где находится датчик схема расположения')
    if car_brand:
        web_queries.append(f'{code_clean} {car_brand} расположение датчика схема двигатель')
    web_queries.append(f'{code_clean} OBD2 где находится датчик схема')

    return image_queries, web_queries


def _make_yandex_image_search_url(error_code: str, car_brand: str, car_model: str) -> str:
    """Генерирует прямую ссылку на поиск в Яндекс.Картинках."""
    query = f"схема двигателя {car_brand} {car_model} датчик {error_code}".strip()
    return f"https://yandex.ru/images/search?text={urllib.parse.quote(query)}"


@app.get("/scheme-search")
async def scheme_search(
    error_code: str = "",
    car_brand: str = "",
    car_model: str = "",
    max_results: int = 8
):
    """
    Ищет схемы расположения компонентов в интернете.

    Стратегия поиска:
      1. Google Custom Search API (картинки) — если настроены ключи
      2. DuckDuckGo Lite (веб-поиск) — fallback
      3. Прямые ссылки на Yandex.Картинки, форумы — гарантированный fallback

    Параметры:
      error_code — код ошибки (обязательно)
      car_brand  — марка авто
      car_model  — модель
      max_results — макс. количество результатов (2–15)
    """
    if not error_code:
        raise HTTPException(status_code=400, detail="error_code обязателен")

    max_results = max(2, min(max_results, 15))

    image_queries, web_queries = _build_scheme_queries(error_code, car_brand, car_model)

    all_results: list[dict] = []
    seen_urls: set[str] = set()

    # ── 1. Google CSE: поиск картинок ──
    if _GOOGLE_CSE_KEY and _GOOGLE_CSE_CX:
        for q in image_queries:
            if len(all_results) >= max_results:
                break
            batch = await _search_google_cse(q, max_results=max_results - len(all_results) + 2)
            for r in batch:
                url_key = r["url"].rstrip("/")
                if url_key not in seen_urls and len(all_results) < max_results:
                    seen_urls.add(url_key)
                    all_results.append(r)

    # ── 2. DDG: веб-поиск (если Google CSE не дал результатов или не настроен) ──
    if len(all_results) < max_results:
        for q in web_queries:
            if len(all_results) >= max_results:
                break
            batch = await _fetch_ddg_results(q, max_results=max_results - len(all_results) + 3)
            for r in batch:
                url_key = r["url"].rstrip("/")
                if url_key not in seen_urls and len(all_results) < max_results:
                    seen_urls.add(url_key)
                    all_results.append(r)

    # ── 3. Гарантированный fallback: прямые ссылки ──
    if not all_results:
        brand_model = f"{car_brand} {car_model or ''}".strip()
        brand_enc = urllib.parse.quote(f"{brand_model} {error_code}".strip())

        # Яндекс.Картинки
        ya_url = _make_yandex_image_search_url(error_code, car_brand, car_model)
        all_results.append({
            "title": f"🔍 Яндекс.Картинки: схема {car_brand} {error_code}",
            "url": ya_url,
            "snippet": "Поиск схем и фотографий расположения датчиков на Яндекс.Картинках",
            "thumbnail": "",
            "source": "yandex",
        })

        # ═══════════════════════════════════════════
        # Русскоязычные (приоритет)
        # ═══════════════════════════════════════════

        # Drive2
        if car_brand:
            all_results.append({
                "title": f"🔍 Поиск на Drive2: {error_code} {car_brand}",
                "url": f"https://www.drive2.ru/search?q={brand_enc}",
                "snippet": "Крупнейшее сообщество автовладельцев — поиск по бортовым журналам",
                "thumbnail": "",
                "source": "direct",
            })

        # Auto.ru
        auto_enc = urllib.parse.quote(f"{error_code} {car_brand} {car_model}".strip())
        all_results.append({
            "title": f"🔍 Auto.ru: {error_code} {car_brand}",
            "url": f"https://auto.ru/catalog/cars/{car_brand.lower()}/all/?search={auto_enc}",
            "snippet": "База автомобилей с характеристиками и отзывами владельцев",
            "thumbnail": "",
            "source": "direct",
        })

        # Форум диагностов
        all_results.append({
            "title": f"🔍 Форум диагностов: {error_code}",
            "url": f"https://diagnost.ru/forum/search.php?keywords={urllib.parse.quote(error_code)}",
            "snippet": "Профессиональный форум диагностов — обсуждения и решения",
            "thumbnail": "",
            "source": "direct",
        })

        # Autodata Online (дистрибьютор РФ)
        all_results.append({
            "title": "📘 Autodata Online",
            "url": f"https://autodata-online.ru/search?q={urllib.parse.quote(error_code)}+{urllib.parse.quote(car_brand)}",
            "snippet": "Профессиональная база данных по ремонту автомобилей (требуется подписка) — схемы, нормы времени, диагностика",
            "thumbnail": "",
            "source": "direct",
        })

        # BmwPost.ru
        all_results.append({
            "title": "🚗 BmwPost",
            "url": f"https://bmwpost.ru/search?q={urllib.parse.quote(error_code)}+{urllib.parse.quote(car_brand)}",
            "snippet": "BmwPost — форум владельцев BMW и других марок: диагностика, ремонт, тюнинг",
            "thumbnail": "",
            "source": "direct",
        })

        # Telegram: Диагносты СНГ
        tg_enc = urllib.parse.quote(f"{error_code} {car_brand}")
        all_results.append({
            "title": "📱 Telegram: Диагносты СНГ",
            "url": f"https://t.me/s/fitlabdia?q={tg_enc}",
            "snippet": "Крупнейшее Telegram-сообщество диагностов СНГ — поиск по постам канала",
            "thumbnail": "",
            "source": "direct",
        })

        # ═══════════════════════════════════════════
        # Международные
        # ═══════════════════════════════════════════

        # OBD-EN.avto.pro (международная база кодов)
        all_results.append({
            "title": "🌐 OBD-EN AvtoPro",
            "url": f"https://obd-en.avto.pro/search?q={urllib.parse.quote(error_code)}+{urllib.parse.quote(car_brand)}",
            "snippet": "Международная энциклопедия OBD2-кодов — расшифровка, причины, решения",
            "thumbnail": "",
            "source": "direct",
        })

        # GeekOBD
        all_results.append({
            "title": "💻 GeekOBD",
            "url": f"https://geekobd.com/search?q={urllib.parse.quote(error_code)}+{urllib.parse.quote(car_brand)}",
            "snippet": "GeekOBD — международная база OBD2-кодов с подробными руководствами по диагностике",
            "thumbnail": "",
            "source": "direct",
        })

        # CarMasters.org
        all_results.append({
            "title": "🔧 CarMasters",
            "url": f"https://carmasters.org/search?q={urllib.parse.quote(error_code)}+{urllib.parse.quote(car_brand)}",
            "snippet": "CarMasters — международный ресурс автомехаников: диагностика, ремонт, руководства",
            "thumbnail": "",
            "source": "direct",
        })

        # SmartLand.am (международный авто-портал)
        all_results.append({
            "title": "🌍 SmartLand",
            "url": f"https://smartland.am/search?q={urllib.parse.quote(error_code)}+{urllib.parse.quote(car_brand)}",
            "snippet": "SmartLand — международный портал: диагностика, ремонт, автоэлектрика",
            "thumbnail": "",
            "source": "direct",
        })

        # Otomotiv-Forum.com (турецкий авто-форум)
        all_results.append({
            "title": "🇹🇷 Otomotiv Forum",
            "url": f"https://otomotiv-forum.com/search?q={urllib.parse.quote(error_code)}+{urllib.parse.quote(car_brand)}",
            "snippet": "Otomotiv Forum — крупнейший турецкий автофорум: диагностика, ремонт, опыт владельцев",
            "thumbnail": "",
            "source": "direct",
        })

        # EngineGuide Wiki
        all_results.append({
            "title": "📚 EngineGuide Wiki",
            "url": f"https://engine-guide.net/wiki/Special:Search?search={urllib.parse.quote(error_code)}+{urllib.parse.quote(car_brand)}",
            "snippet": "EngineGuide Wiki — энциклопедия автомобильных двигателей: схемы, коды ошибок, руководства по ремонту",
            "thumbnail": "",
            "source": "direct",
        })

        # The Automotive Technician (Австралия)
        all_results.append({
            "title": "🌏 The Automotive Technician",
            "url": f"https://tat.net.au/search?q={urllib.parse.quote(error_code)}+{urllib.parse.quote(car_brand)}",
            "snippet": "Международный ресурс автомобильных диагностов — статьи, руководства, техническая документация",
            "thumbnail": "",
            "source": "direct",
        })

        # ═══════════════════════════════════════════
        # Специализированные (чип-тюнинг / ECU)
        # ═══════════════════════════════════════════

        # BinUnlock (чип-тюнинг / ECU)
        all_results.append({
            "title": "🔓 BinUnlock",
            "url": f"https://binunlock.com/search?q={urllib.parse.quote(error_code)}+{urllib.parse.quote(car_brand)}",
            "snippet": "BinUnlock — ресурс по чип-тюнингу, прошивкам ЭБУ и программированию автоэлектроники",
            "thumbnail": "",
            "source": "direct",
        })

        # iProg (программаторы)
        all_results.append({
            "title": "🔌 iProg.pro",
            "url": f"https://iprog.pro/search?q={urllib.parse.quote(error_code)}+{urllib.parse.quote(car_brand)}",
            "snippet": "iProg — программаторы автоэлектроники, прошивки ЭБУ, иммобилайзеры",
            "thumbnail": "",
            "source": "direct",
        })

        # Telegram: каналы по чип-тюнингу и ECU
        all_results.append({
            "title": "📱 Telegram: чип-тюнинг / ECU",
            "url": f"https://t.me/s/autoprogs?q={tg_enc}",
            "snippet": "Telegram-каналы: autoprogs, chiphuip, immonah, odometr — чип-тюнинг, иммобилайзеры, одометры, прошивки",
            "thumbnail": "",
            "source": "direct",
        })

        # Специфичные форумы по маркам
        if car_brand.upper() in ("ВАЗ", "LADA", "ЛАДА"):
            all_results.append({
                "title": f"🔍 Форум ВАЗ: {error_code}",
                "url": f"https://forum.vaz.ru/search/?q={urllib.parse.quote(error_code)}",
                "snippet": "Форум владельцев ВАЗ — опыт ремонта и диагностики",
                "thumbnail": "",
                "source": "direct",
            })

    return JSONResponse(
        content={
            "error_code": error_code,
            "car_brand": car_brand,
            "car_model": car_model,
            "search_engine": "google_cse" if (_GOOGLE_CSE_KEY and _GOOGLE_CSE_CX and any(r["source"] == "google_cse" for r in all_results))
                             else "ddg" if any(r["source"] == "ddg" for r in all_results)
                             else "direct",
            "query_count": len(image_queries) + len(web_queries),
            "results": all_results,
            "total_found": len(all_results),
        },
        media_type="application/json; charset=utf-8"
    )


# ─── Эндпоинт анализа живых данных ───

@app.post("/live-analyze")
async def live_analyze(request: LiveAnalyzeRequest):
    """
    Принимает массив текущих PID-значений и возвращает AI-анализ:
    — найденные аномалии
    — возможные неисправности
    — рекомендации
    """
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="API ключ не настроен")

    if not request.pids:
        raise HTTPException(status_code=400, detail="Нет данных PID для анализа")

    # ── Формируем текстовую сводку параметров ──
    car_info = f"{request.car_brand} {request.car_model or ''}".strip() or "не указан"

    normal_pids = []
    warning_pids = []
    danger_pids = []

    for p in request.pids:
        line = f"• {p.name}: {p.value:.1f} {p.unit} (диапазон {p.min_val:.0f}–{p.max_val:.0f})"
        if p.severity == 2:
            danger_pids.append(f"🔴 {line}")
        elif p.severity == 1:
            warning_pids.append(f"🟡 {line}")
        else:
            normal_pids.append(f"🟢 {line}")

    pid_summary_parts = []
    if danger_pids:
        pid_summary_parts.append("ОПАСНЫЕ ЗНАЧЕНИЯ:")
        pid_summary_parts.extend(danger_pids)
    if warning_pids:
        pid_summary_parts.append("\nПОДОЗРИТЕЛЬНЫЕ ЗНАЧЕНИЯ:")
        pid_summary_parts.extend(warning_pids)
    if normal_pids:
        pid_summary_parts.append("\nНОРМАЛЬНЫЕ ЗНАЧЕНИЯ:")
        pid_summary_parts.extend(normal_pids)

    pid_summary = "\n".join(pid_summary_parts)

    # ── Системный промпт ──
    system = (
        "Ты — опытный диагност российских автомобилей (ВАЗ, ГАЗ, УАЗ, КАМАЗ, иномарки на российском рынке). "
        "Твоя задача: проанализировать живые данные с датчиков OBD2 и выявить возможные неисправности.\n\n"
        "ПРАВИЛА:\n"
        "1. Анализируй логические связи между параметрами (например: высокий RPM + низкий MAF + бедная смесь → подсос воздуха).\n"
        "2. Указывай конкретные причины, а не общие фразы. Каждый вывод должен опираться на значения из данных.\n"
        "3. Если данные в пределах нормы — так и напиши, не выдумывай проблем.\n"
        "4. Если данные явно указывают на неисправность — напиши, какой узел и почему.\n"
        "5. НЕ используй слова: 'возможно', 'наверное', 'может быть', 'вероятно', 'скорее всего'.\n"
        "6. Каждый совет по диагностике сопровождай источником [✓] (источник: ...).\n"
        "7. Если данных недостаточно для вывода — скажи об этом честно.\n\n"
        "ФОРМАТ ОТВЕТА:\n"
        "[ОБЩАЯ ОЦЕНКА] — одна строка: состояние двигателя хорошее/удовлетворительное/тревожное/критическое\n"
        "[АНАЛИЗ] — анализ в свободной форме (коротко, по делу)\n"
        "[ВЫВОДЫ] — список подозрительных параметров или 'все параметры в норме'"
    )

    user = f"Автомобиль: {car_info}\n\nДанные датчиков:\n{pid_summary}"

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json; charset=utf-8"
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 2000
                }
            )

        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Ошибка DeepSeek API: {resp.status_code}")

        data = resp.json()
        raw = data["choices"][0]["message"]["content"]

        # ── Детектор слов-маркеров ──
        forbidden = [
            "возможно", "наверное", "может быть", "вероятно",
            "скорее всего", "не исключено", "как правило",
            "в большинстве случаев", "обычно", "как бы"
        ]
        found = [w for w in forbidden if w in raw.lower()]
        if found:
            prefix = f"⚠️ ВНИМАНИЕ: в ответе найдены слова-маркеры неуверенности: {', '.join(found)}.\n\n"
            raw = prefix + raw

        return {
            "analysis": raw,
            "car": car_info,
            "pid_count": len(request.pids),
            "danger_count": len(danger_pids),
            "warning_count": len(warning_pids)
        }

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Тайм-аут при обращении к AI")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Ошибка: {str(e)}")


# ─── Приём пользовательских решений ───

class SubmitSolutionRequest(BaseModel):
    """Решение, отправленное пользователем вручную."""
    error_code: str
    car_brand: str = ""
    car_model: str = ""
    diagnosis: str              # Текст решения
    source: str = "user_submit" # Откуда решение (user_submit, forum, manual)
    user_id: str = ""           # Опциональный идентификатор пользователя


@app.post("/submit_solution")
async def submit_solution(request: SubmitSolutionRequest):
    """
    Принимает новое решение от пользователя.
    Сохраняет в knowledge_base.jsonl, обновляет кэш и ChromaDB.
    """
    now = datetime.now(timezone.utc).isoformat()

    # 1. Сохраняем в knowledge_base.jsonl
    record = {
        "error_code": request.error_code,
        "car_brand": request.car_brand,
        "car_model": request.car_model,
        "diagnosis": request.diagnosis,
        "source": request.source,
        "confidence": 0.8,  # Ручные решения получают высокий confidence
        "created_at": now,
        "updated_at": now,
        "helpful_count": 1,
        "not_helpful_count": 0,
        "user_id": request.user_id,
    }

    kp = _knowledge_path()
    with open(kp, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # 2. Обновляем in-memory кэш successful_solutions
    _update_cache(
        request.error_code,
        request.car_brand,
        request.diagnosis,
        helpful_count=1,
        not_helpful_count=0
    )

    # 3. Обновляем ChromaDB
    _chroma_upsert(
        request.error_code,
        request.car_brand,
        request.car_model,
        request.diagnosis,
        helpful_count=1
    )

    return {
        "status": "ok",
        "message": "Решение принято и сохранено в базе знаний.",
        "error_code": request.error_code,
        "created_at": now,
    }


@app.get("/get_updates")
async def get_updates(since: str = "", limit: int = 50, type: str = ""):
    """
    Отдаёт все обновления (решения + схемы) с указанной даты.
    Единый эндпоинт для клиентского фонового агента.

    Параметры:
      since — ISO8601 timestamp, вернуть только записи новее этого времени
      limit — макс. количество записей каждого типа (1–200)
      type  — опциональный фильтр: 'solutions', 'diagrams', или '' (все)

    Ответ:
      {
        "server_time": "2026-07-12T00:40:00+00:00",
        "solutions": [...],    # Новые решения из knowledge_base
        "diagrams": [...],     # Новые схемы
        "solutions_count": 5,
        "diagrams_count": 2
      }
    """
    if limit < 1 or limit > 200:
        limit = 50

    result = {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "solutions": [],
        "diagrams": [],
        "solutions_count": 0,
        "diagrams_count": 0,
    }

    # ── Решения (knowledge) ──
    if not type or type == "solutions":
        kb_path = SYNC_FILES["knowledge"]
        if os.path.exists(kb_path):
            kb_entries = []
            with open(kb_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get("updated_at", entry.get("created_at", ""))
                        if since and ts <= since:
                            continue
                        # Отдаём решения от пользователей и обогащённые
                        if entry.get("source") in ("user_submit", "client_sync", "weekly_agent"):
                            kb_entries.append({
                                "error_code": entry.get("error_code", ""),
                                "car_brand": entry.get("car_brand", ""),
                                "car_model": entry.get("car_model", ""),
                                "diagnosis": entry.get("diagnosis", ""),
                                "source": entry.get("source", ""),
                                "confidence": entry.get("confidence", 0.5),
                                "helpful_count": entry.get("helpful_count", 0),
                                "not_helpful_count": entry.get("not_helpful_count", 0),
                                "updated_at": ts,
                            })
                    except json.JSONDecodeError:
                        continue

            kb_entries.sort(key=lambda e: e.get("updated_at", ""), reverse=True)
            result["solutions"] = kb_entries[:limit]
            result["solutions_count"] = len(kb_entries)

    # ── Схемы ──
    if not type or type == "diagrams":
        diag_path = SYNC_FILES["diagrams"]
        if os.path.exists(diag_path):
            diag_entries = []
            with open(diag_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get("created_at", "")
                        if since and ts <= since:
                            continue
                        diag_entries.append({
                            "error_code": entry.get("error_code", ""),
                            "car_brand": entry.get("car_brand", ""),
                            "car_model": entry.get("car_model", ""),
                            "title": entry.get("title", ""),
                            "description": entry.get("description", ""),
                            "source_url": entry.get("source_url", ""),
                            "created_at": ts,
                        })
                    except json.JSONDecodeError:
                        continue

            diag_entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
            result["diagrams"] = diag_entries[:limit]
            result["diagrams_count"] = len(diag_entries)

    return result


@app.get("/sync_status")
async def sync_status():
    """
    Статус синхронизации сервера.
    Возвращает сводку: объёмы данных, время последних обновлений, версии.
    Клиент использует для отображения состояния и принятия решения о синхронизации.

    Ответ:
      {
        "server_time": "...",
        "databases": {
          "diagnoses":    { "file": "diagnoses.jsonl",     "records": 156, "last_updated": "..." },
          "knowledge":    { "file": "knowledge_base.jsonl","records": 89,  "last_updated": "..." },
          "diagrams":     { "file": "diagrams.jsonl",      "records": 12,  "last_updated": "..." },
          "feedback":     { "file": "feedback.jsonl",      "records": 45,  "last_updated": "..." }
        },
        "user_submitted": 14,
        "total_records": 302,
        "chromadb": "ok" | "unavailable",
        "cached_solutions": 67
      }
    """
    def _count_and_last(path: str) -> tuple[int, str]:
        """(records, last_updated_iso)."""
        if not os.path.exists(path):
            return 0, ""
        count = 0
        last_ts = ""
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                count += 1
                try:
                    entry = json.loads(line)
                    ts = entry.get("updated_at", entry.get("created_at", ""))
                    if ts and (not last_ts or ts > last_ts):
                        last_ts = ts
                except json.JSONDecodeError:
                    pass
        return count, last_ts

    def _count_source(path: str, source: str) -> int:
        """Считает записи с определённым source."""
        if not os.path.exists(path):
            return 0
        count = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if source in line:
                    count += 1
        return count

    db_status = {}
    total = 0

    for key, fname in SYNC_FILES.items():
        recs, last = _count_and_last(fname)
        total += recs
        db_status[key] = {
            "file": fname,
            "records": recs,
            "last_updated": last if last else None,
        }

    # Кол-во вручную отправленных решений
    user_submitted = _count_source(SYNC_FILES["knowledge"], "user_submit")

    # Статус ChromaDB
    chroma_status = "unavailable"
    if CHROMADB_AVAILABLE and _chroma_collection is not None:
        try:
            chroma_count = _chroma_collection.count()
            chroma_status = f"ok ({chroma_count} vectors)"
        except Exception:
            chroma_status = "error"

    return {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "databases": db_status,
        "user_submitted": user_submitted,
        "total_records": total,
        "chromadb": chroma_status,
        "cached_solutions": len(successful_solutions),
    }

