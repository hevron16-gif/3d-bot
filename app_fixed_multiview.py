"""
app_fixed_multiview.py — Генерация 3D по чертежам/нескольким ракурсам через
Tencent Hunyuan 3D API.

ЧТО ИСПРАВЛЕНО:
1. API endpoint:    hunyuan.intl.tencentcloudapi.com → ai3d.intl.tencentcloudapi.com
   Service:         hunyuan → ai3d
   Version:         2023-09-01 → 2025-05-13
   (старый endpoint не поддерживает MultiViewImages)

2. MultiViewImages: правильный формат массива ViewImage-объектов
   Каждый объект: {"ViewType": "left|right|back|top|bottom|left_front|right_front",
                   "ViewImageBase64": "..."}

3. Convert3DFormat заменён на ResultFormat (новый API отдаёт STL/GLB/USDZ/FBX напрямую)
   Старая конвертация через Convert3DFormat удалена.

ДОКУМЕНТАЦИЯ: cloud.tencent.com/document/api/1804/123447
"""

import os, json, time, hashlib, hmac, base64
from datetime import datetime
from requests.exceptions import Timeout, ConnectionError

# ============================================================
# 1. КОНФИГУРАЦИЯ API
# ============================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY")

# === НОВЫЙ API (ai3d, v2025-05-13) — поддерживает MultiViewImages ===
AI3D_SERVICE = "ai3d"
AI3D_HOST = "ai3d.intl.tencentcloudapi.com"
AI3D_VERSION = "2025-05-13"
AI3D_REGION = "ap-singapore"

# === СТАРЫЙ API (hunyuan, v2023-09-01) — для обратной совместимости ===
OLD_SERVICE = "hunyuan"
OLD_HOST = "hunyuan.intl.tencentcloudapi.com"
OLD_VERSION = "2023-09-01"

# По умолчанию используем НОВЫЙ API
DEFAULT_SERVICE = AI3D_SERVICE
DEFAULT_HOST = AI3D_HOST
DEFAULT_VERSION = AI3D_VERSION


# ============================================================
# 2. TC3-HMAC-SHA256 ПОДПИСЬ (поддерживает оба API)
# ============================================================
def get_tencent_headers(action, payload,
                        service=None, host=None, version=None, region=None):
    """
    Формирует заголовки для Tencent Cloud API (TC3-HMAC-SHA256).
    По умолчанию — НОВЫЙ API (ai3d, v2025-05-13).
    Передайте service/host/version/region для старого API.
    """
    if service is None:
        service = DEFAULT_SERVICE
    if host is None:
        host = DEFAULT_HOST
    if version is None:
        version = DEFAULT_VERSION
    if region is None:
        region = AI3D_REGION

    algorithm = "TC3-HMAC-SHA256"
    timestamp = int(time.time())
    date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
    ct = "application/json"
    canonical_headers = f"content-type:{ct}\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = (
        f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{hashed_payload}"
    )
    credential_scope = f"{date}/{service}/tc3_request"
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = (
        f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashed_canonical}"
    )
    secret_date = hmac.new(
        ("TC3" + TENCENT_SECRET_KEY).encode("utf-8"),
        date.encode("utf-8"), hashlib.sha256
    ).digest()
    secret_service = hmac.new(
        secret_date, service.encode("utf-8"), hashlib.sha256
    ).digest()
    secret_signing = hmac.new(
        secret_service, b"tc3_request", hashlib.sha256
    ).digest()
    signature = hmac.new(
        secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    authorization = (
        f"{algorithm} Credential={TENCENT_SECRET_ID}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Authorization": authorization, "Content-Type": ct, "Host": host,
        "X-TC-Action": action, "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": version, "X-TC-Region": region
    }


# ============================================================
# 3. HTTP С РЕТРАЯМИ
# ============================================================
import requests as _requests

def safe_request(method, url, max_retries=3, **kwargs):
    for attempt in range(max_retries):
        try:
            if method == "post":
                return _requests.post(url, **kwargs)
            else:
                return _requests.get(url, **kwargs)
        except (Timeout, ConnectionError) as e:
            print(f"⚠️ Сетевая ошибка (попытка {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(15)
                continue
            raise


def find_file_by_type(result_files, target_format):
    """
    Ищет в ResultFile3Ds файл нужного формата.
    Возвращает URL или None.
    Пример: find_file_by_type(files, "GLB") → "https://..."
    """
    target = target_format.upper()
    for f in result_files:
        ftype = (f.get("Type") or "").upper()
        furl = f.get("Url") or ""
        if ftype == target and furl:
            return furl
    return None


def download_file(url, timeout=120):
    """Скачивает файл по URL. Возвращает bytes или вызывает исключение."""
    resp = _requests.get(url, timeout=timeout)
    if resp.status_code != 200:
        raise Exception(f"Не удалось скачать: HTTP {resp.status_code}")
    if len(resp.content) == 0:
        raise Exception("Скачан пустой файл")
    return resp.content


# ============================================================
# 5. СТАНДАРТНАЯ ГЕНЕРАЦИЯ (текст → 3D) — НОВЫЙ API
# ============================================================
def hunyuan_generate_with_retry(prompt, output_format="stl", max_retries=3):
    """
    Генерация по тексту через НОВЫЙ AI3D API (v2025-05-13).

    НОВОЕ:
    - Использует ai3d.intl.tencentcloudapi.com (вместо hunyuan.intl...)
    - ResultFormat вместо Convert3DFormat
    - Ищет нужный формат в ResultFile3Ds по полю Type

    output_format: "stl", "glb", "obj", "usdz", "fbx"
    """
    # Маппинг форматов для ResultFormat
    fmt_map = {"glb": "GLB", "stl": "STL", "obj": "OBJ", "usdz": "USDZ", "fbx": "FBX"}
    result_fmt = fmt_map.get(output_format, "STL")

    for attempt in range(max_retries):
        try:
            print(f"[AI3D] Попытка {attempt+1}, формат: {output_format}")
            action = "SubmitHunyuanTo3DProJob"
            payload_dict = {
                "Model": "3.1",
                "Prompt": prompt[:900],
                "EnablePBR": True,
                "GenerateType": "Normal",
                "FaceCount": 1000000,
                "ResultFormat": result_fmt
            }
            payload = json.dumps(payload_dict)
            headers = get_tencent_headers(action, payload)
            resp = safe_request("post", f"https://{DEFAULT_HOST}", headers=headers, data=payload, timeout=90)
            data = resp.json()

            if "Response" not in data or "JobId" not in data["Response"]:
                error_info = data.get("Response", {}).get("Error", data)
                raise Exception(f"AI3D submit error: {error_info}")

            job_id = data["Response"]["JobId"]
            query_action = "QueryHunyuanTo3DProJob"

            for _ in range(40):
                time.sleep(5)
                payload = json.dumps({"JobId": job_id})
                headers = get_tencent_headers(query_action, payload)
                resp = safe_request("post", f"https://{DEFAULT_HOST}", headers=headers, data=payload, timeout=90)
                data = resp.json()

                if "Response" in data:
                    status = data["Response"].get("Status")
                    if status == "DONE":
                        result_files = data["Response"].get("ResultFile3Ds", [])
                        if not result_files:
                            raise Exception("ResultFile3Ds пуст после DONE")

                        # Ищем файл нужного формата
                        file_url = find_file_by_type(result_files, result_fmt)
                        if not file_url:
                            # Fallback: берём первый доступный
                            print(f"[AI3D] {result_fmt} не найден в ResultFile3Ds, беру первый")
                            file_url = result_files[0].get("Url")
                            if not file_url:
                                raise Exception("Нет URL в ResultFile3Ds")

                        # Скачиваем
                        file_data = download_file(file_url)
                        print(f"[AI3D] ✅ Получен файл: {len(file_data)} байт (формат: {output_format})")
                        return file_data, output_format

                    elif status == "FAIL":
                        error_msg = data["Response"].get("ErrorMessage", "неизвестная ошибка")
                        raise Exception(f"Generation FAILED: {error_msg}")

            if attempt < max_retries - 1:
                time.sleep(15)
                continue
            raise Exception("AI3D: тайм-аут ожидания (200 сек)")

        except Exception as e:
            print(f"[AI3D] Ошибка в попытке {attempt+1}: {e}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(15)

    raise Exception("AI3D: не удалось сгенерировать модель")


# ============================================================
# 6. ГЕНЕРАЦИЯ ПО ОДНОМУ ФОТО (ImageBase64) — НОВЫЙ API
# ============================================================
def hunyuan_generate_from_photo(image_base64, output_format="stl", max_retries=3):
    """Генерация по одному фото через новый AI3D API."""
    fmt_map = {"glb": "GLB", "stl": "STL", "obj": "OBJ"}
    result_fmt = fmt_map.get(output_format, "STL")

    for attempt in range(max_retries):
        try:
            print(f"[AI3D фото] Попытка {attempt+1}, формат: {output_format}")
            action = "SubmitHunyuanTo3DProJob"
            payload_dict = {
                "Model": "3.1",
                "ImageBase64": image_base64,
                "EnablePBR": True,
                "GenerateType": "Normal",
                "FaceCount": 1000000,
                "ResultFormat": result_fmt
            }
            payload = json.dumps(payload_dict)
            headers = get_tencent_headers(action, payload)
            resp = safe_request("post", f"https://{DEFAULT_HOST}", headers=headers, data=payload, timeout=90)
            data = resp.json()

            if "Response" not in data or "JobId" not in data["Response"]:
                error_info = data.get("Response", {}).get("Error", data)
                raise Exception(f"AI3D error: {error_info}")

            job_id = data["Response"]["JobId"]
            query_action = "QueryHunyuanTo3DProJob"

            for _ in range(40):
                time.sleep(5)
                payload = json.dumps({"JobId": job_id})
                headers = get_tencent_headers(query_action, payload)
                resp = safe_request("post", f"https://{DEFAULT_HOST}", headers=headers, data=payload, timeout=90)
                data = resp.json()

                if "Response" in data:
                    status = data["Response"].get("Status")
                    if status == "DONE":
                        result_files = data["Response"].get("ResultFile3Ds", [])
                        file_url = find_file_by_type(result_files, result_fmt)
                        if not file_url:
                            file_url = result_files[0].get("Url")
                        if not file_url:
                            raise Exception("Нет URL в ResultFile3Ds")

                        file_data = download_file(file_url)
                        print(f"[AI3D фото] ✅ Файл: {len(file_data)} байт")
                        return file_data, output_format

                    elif status == "FAIL":
                        raise Exception("Generation FAILED")

            if attempt < max_retries - 1:
                time.sleep(15)
                continue
            raise Exception("AI3D фото: тайм-аут")

        except Exception as e:
            print(f"[AI3D фото] Ошибка в попытке {attempt+1}: {e}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(15)

    raise Exception("AI3D фото: не удалось сгенерировать модель")


# ============================================================
# 7. ★ MULTI-VIEW ГЕНЕРАЦИЯ (по чертежам/ракурсам) ★
# ============================================================

# Допустимые типы ракурсов (из документации Tencent, Model 3.1)
VALID_VIEW_TYPES = {
    "left", "right", "back",           # базовые
    "top", "bottom",                    # 3.1+
    "left_front", "right_front"         # 3.1+ (45°)
}

# Псевдонимы для удобства пользователей
VIEW_ALIASES = {
    "front": None,          # front = основное изображение (ImageBase64)
    "side": "right",        # side → right view
    "слева": "left",
    "справа": "right",
    "сзади": "back",
    "сверху": "top",
    "снизу": "bottom",
    "левый_фасад": "left_front",
    "правый_фасад": "right_front",
}


def build_multiview_images(views_dict):
    """
    Принимает словарь {view_type: base64_string} и строит
    корректный массив ViewImage для Tencent API.

    views_dict: {"left": "base64...", "right": "base64...", "top": "base64..."}
    Возвращает: [{"ViewType": "left", "ViewImageBase64": "base64..."}, ...]
    """
    result = []
    for view_type, image_b64 in views_dict.items():
        # Разрешаем псевдонимы
        canonical = VIEW_ALIASES.get(view_type.lower(), view_type.lower())
        if canonical is None:
            # "front" — пропускаем, это основное изображение
            continue
        if canonical not in VALID_VIEW_TYPES:
            print(f"[MultiView] Предупреждение: неизвестный ракурс '{view_type}' → '{canonical}', пропускаю")
            continue
        if not image_b64 or len(image_b64) < 100:
            print(f"[MultiView] Предупреждение: пустая/слишком короткая base64 для '{canonical}'")
            continue

        result.append({
            "ViewType": canonical,
            "ViewImageBase64": image_b64
        })
    return result


def hunyuan_generate_from_multiview(
    main_image_base64,
    multiview_images,
    prompt="",
    output_format="stl",
    max_retries=3
):
    """
    ★ Генерация 3D-модели по нескольким ракурсам (Multi-View) ★

    Параметры:
      main_image_base64: str — ОСНОВНОЕ изображение (фронтальный вид),
                          передаётся как ImageBase64. ОБЯЗАТЕЛЕН.
      multiview_images: list of dict — массив доп. ракурсов:
                          [{"ViewType": "left", "ViewImageBase64": "..."},
                           {"ViewType": "right", "ViewImageBase64": "..."},
                           {"ViewType": "top", "ViewImageBase64": "..."}]
      prompt: str — опциональное текстовое описание
      output_format: str — "stl", "glb", "obj", "usdz", "fbx"
      max_retries: int

    Возвращает: (bytes_данные, str_формат)

    ВАЖНО (из документации Tencent):
    - MultiViewImages работает ТОЛЬКО на новом API (ai3d, v2025-05-13)
    - Model должен быть "3.1" (3.0 не поддерживает top/bottom/front)
    - Суммарный размер ВСЕХ base64-изображений ≤ 6 МБ (из-за +30% base64-overhead
      исходные файлы ≤ 8 МБ)
    - Каждое изображение: разрешение 128-5000 px, формат jpg/png
    - ImageBase64/ImageUrl обязателен (основной фронтальный вид)
    - MultiViewImages.N — опциональный массив дополнительных ракурсов
    """
    if not main_image_base64:
        raise ValueError("main_image_base64 обязателен (фронтальный вид)")

    fmt_map = {"glb": "GLB", "stl": "STL", "obj": "OBJ", "usdz": "USDZ", "fbx": "FBX"}
    result_fmt = fmt_map.get(output_format, "STL")

    # Проверка суммарного размера (base64, ≤ 6 МБ)
    total_size = len(main_image_base64)
    for img in multiview_images:
        total_size += len(img.get("ViewImageBase64", ""))
    if total_size > 6_000_000:
        print(f"[MultiView] ⚠️ Суммарный размер base64: {total_size / 1e6:.1f} МБ (лимит 6 МБ)")

    for attempt in range(max_retries):
        try:
            print(f"[MultiView] Попытка {attempt+1}, ракурсов: {len(multiview_images)}, формат: {output_format}")

            action = "SubmitHunyuanTo3DProJob"
            payload_dict = {
                "Model": "3.1",
                "ImageBase64": main_image_base64,
                "EnablePBR": True,
                "GenerateType": "Normal",
                "FaceCount": 1000000,
                "ResultFormat": result_fmt
            }

            # ★ Добавляем MultiViewImages ★
            if multiview_images:
                payload_dict["MultiViewImages"] = multiview_images
                print(f"[MultiView] Отправлено {len(multiview_images)} ракурсов: "
                      f"{[v['ViewType'] for v in multiview_images]}")

            if prompt:
                payload_dict["Prompt"] = prompt[:500]

            payload = json.dumps(payload_dict)
            headers = get_tencent_headers(action, payload)
            resp = safe_request("post", f"https://{DEFAULT_HOST}", headers=headers, data=payload, timeout=90)
            data = resp.json()

            # Проверка на ошибки API
            if "Response" in data:
                resp_data = data["Response"]
                if "Error" in resp_data:
                    error = resp_data["Error"]
                    code = error.get("Code", "")
                    msg = error.get("Message", "")
                    # Специфичные ошибки MultiViewImages
                    if "MultiViewImages" in str(msg) or "MultiViewImages" in str(code):
                        raise Exception(
                            f"MultiViewImages error [{code}]: {msg}\n"
                            f"Возможные причины:\n"
                            f"  1. Неверный формат ViewImage (должен быть: "
                            f'{{"ViewType":"left","ViewImageBase64":"..."}})\n'
                            f"  2. Размер изображений > 6 МБ (base64)\n"
                            f"  3. Неподдерживаемый ViewType\n"
                            f"  4. Старый API (нужен ai3d, v2025-05-13)"
                        )
                    raise Exception(f"AI3D error [{code}]: {msg}")
                if "JobId" not in resp_data:
                    raise Exception(f"AI3D error: нет JobId в ответе. Response: {resp_data}")
            else:
                raise Exception(f"AI3D error: нет Response в ответе. {data}")

            job_id = data["Response"]["JobId"]
            query_action = "QueryHunyuanTo3DProJob"

            for _ in range(40):
                time.sleep(5)
                payload = json.dumps({"JobId": job_id})
                headers = get_tencent_headers(query_action, payload)
                resp = safe_request("post", f"https://{DEFAULT_HOST}", headers=headers, data=payload, timeout=90)
                data = resp.json()

                if "Response" in data:
                    status = data["Response"].get("Status")
                    if status == "DONE":
                        result_files = data["Response"].get("ResultFile3Ds", [])
                        file_url = find_file_by_type(result_files, result_fmt)
                        if not file_url:
                            file_url = result_files[0].get("Url")
                        if not file_url:
                            raise Exception("Нет URL в ResultFile3Ds")

                        file_data = download_file(file_url)
                        print(f"[MultiView] ✅ Модель готова: {len(file_data)} байт ({output_format})")
                        return file_data, output_format

                    elif status == "FAIL":
                        error_msg = data["Response"].get("ErrorMessage", "неизвестно")
                        raise Exception(f"Generation FAILED: {error_msg}")

            if attempt < max_retries - 1:
                time.sleep(15)
                continue
            raise Exception("MultiView: тайм-аут ожидания (200 сек)")

        except Exception as e:
            print(f"[MultiView] Ошибка в попытке {attempt+1}: {e}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(15)

    raise Exception("MultiView: не удалось сгенерировать модель")


# ============================================================
# 8. УДОБНАЯ ОБЁРТКА ДЛЯ БОТА (3 ракурса: спереди, сбоку, сверху)
# ============================================================
def hunyuan_generate_from_drawings(
    front_b64, side_b64, top_b64,
    output_format="stl",
    prompt=""
):
    """
    Упрощённый вызов: 3 стандартных ракурса (фронт, бок, верх).

    front_b64: base64 фронтального вида (ОБЯЗАТЕЛЕН — идёт в ImageBase64)
    side_b64:  base64 бокового вида (→ ViewType: "right")
    top_b64:   base64 вида сверху (→ ViewType: "top")

    Возвращает: (bytes, str)
    """
    multiview = []
    if side_b64:
        multiview.append({"ViewType": "right", "ViewImageBase64": side_b64})
    if top_b64:
        multiview.append({"ViewType": "top", "ViewImageBase64": top_b64})

    return hunyuan_generate_from_multiview(
        main_image_base64=front_b64,
        multiview_images=multiview,
        prompt=prompt,
        output_format=output_format
    )


# ============================================================
# 9. ТЕСТ (запустить: python app_fixed_multiview.py)
# ============================================================
if __name__ == "__main__":
    print("=" * 65)
    print("ТЕСТ: Multi-View генерация через Hunyuan3D AI3D API")
    print("=" * 65)

    if not TENCENT_SECRET_ID:
        print("❌ TENCENT_SECRET_ID не установлен.")
        print("   set TENCENT_SECRET_ID=...")
        print("   set TENCENT_SECRET_KEY=...")
        exit(1)

    # --- ТЕСТ 1: проверим, что API доступен (простая текстовая генерация) ---
    print("\n[Тест 1] Проверка API (текст → STL)...")
    try:
        data, fmt = hunyuan_generate_with_retry(
            "a simple cube 50mm with rounded edges", output_format="stl"
        )
        print(f"  ✅ Успех: {len(data)} байт, формат {fmt}")
        with open("test_basic.stl", "wb") as f:
            f.write(data)
        print("  📁 Сохранён: test_basic.stl")
    except Exception as e:
        print(f"  ❌ Ошибка: {e}")
        print("  Проверьте TENCENT_SECRET_ID и TENCENT_SECRET_KEY")

    # --- ТЕСТ 2: формат GLB ---
    print("\n[Тест 2] Проверка формата GLB...")
    try:
        data, fmt = hunyuan_generate_with_retry(
            "a simple sphere 30mm", output_format="glb"
        )
        print(f"  ✅ Успех: {len(data)} байт, формат {fmt}")
        with open("test_glb.glb", "wb") as f:
            f.write(data)
        print("  📁 Сохранён: test_glb.glb")
    except Exception as e:
        print(f"  ❌ Ошибка: {e}")

    # --- ТЕСТ 3: Multi-View (если есть тестовые изображения) ---
    print("\n[Тест 3] Multi-View генерация...")
    print("  Для теста нужны 3 base64-изображения (front, side, top).")
    print("  Пример вызова:")
    print("    hunyuan_generate_from_drawings(front_b64, side_b64, top_b64)")
    print()
    print("  Формат MultiViewImages (отправляется в API):")
    print('  {')
    print('    "ImageBase64": "<front view base64>",')
    print('    "MultiViewImages": [')
    print('      {"ViewType": "right", "ViewImageBase64": "<side view base64>"},')
    print('      {"ViewType": "top",   "ViewImageBase64": "<top view base64>"}')
    print('    ],')
    print('    "Model": "3.1",')
    print('    "ResultFormat": "STL"')
    print('  }')
    print()
    print("=" * 65)
    print("ГОТОВО. Функции готовы к интеграции в бота.")
    print("=" * 65)
