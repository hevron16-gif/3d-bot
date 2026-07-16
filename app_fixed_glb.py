"""
app_fixed_glb.py — Исправление GLB-генерации в Hunyuan API.

КОРЕНЬ ПРОБЛЕМЫ: Hunyuan3D возвращает URL на COS (Tencent Object Storage).
Прямой GET без TC3-подписи может вернуть:
  1. HTML-страницу ошибки (200 OK, но content — HTML)
  2. XML-ошибку COS (403/200 с AccessDenied)
  3. Неполный файл (CDN ещё не прогрузился после DONE)

Всё это проходит проверку status_code==200, но порождает битый GLB.

РЕШЕНИЕ:
  - Валидация GLB по magic bytes (glTF: 0x676C5446)
  - Повторная загрузка через Convert3DFormat (GLB→GLB) для получения
    гарантированно-доступного URL
  - Fallback на STL если GLB трижды битый
"""

import os, json, time, hashlib, hmac, requests
from datetime import datetime
from requests.exceptions import Timeout, ConnectionError

# ============================================================
# ЭТА ФУНКЦИЯ ЗАМЕНЯЕТ hunyuan_generate_with_retry из app.py
# ============================================================
# Остальной код бота (переменные окружения, Telegram-методы,
# клавиатуры, polling) остаётся без изменений из app.py.
# Здесь — только исправленная функция генерации + валидация.
# ============================================================

# Скопировано из app.py для автономности этого файла
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY")
HUNYUAN_HOST = "hunyuan.intl.tencentcloudapi.com"

# --- GLB magic bytes (начало любого валидного бинарного glTF-файла) ---
GLB_MAGIC = b'glTF'        # uint32 magic: 0x46546C67 (little-endian)
GLB_HEADER_SIZE = 12        # 12 байт: magic(4) + version(4) + length(4)

def is_valid_glb(data):
    """
    Проверяет, является ли data валидным GLB-файлом.
    Возвращает (True, size) или (False, причина).
    """
    if not data or len(data) < GLB_HEADER_SIZE:
        return False, f"Файл слишком мал: {len(data)} байт (минимум {GLB_HEADER_SIZE})"
    
    magic = data[:4]
    if magic != GLB_MAGIC:
        # Покажем первые 20 байт для диагностики
        preview = data[:20]
        try:
            text_preview = data[:200].decode('utf-8', errors='replace')
            if text_preview.strip().startswith('<') or text_preview.strip().startswith('{'):
                return False, f"Не GLB (похоже на HTML/JSON): {text_preview[:100]}"
        except:
            pass
        return False, f"Неверная сигнатура: {magic.hex()} (ожидалось {GLB_MAGIC.hex()}). Первые байты: {preview.hex()}"
    
    # Читаем total length из заголовка (little-endian uint32 на смещении 8)
    declared_length = int.from_bytes(data[8:12], 'little')
    actual_length = len(data)
    
    if actual_length < declared_length:
        return False, f"Файл обрезан: объявлено {declared_length} байт, получено {actual_length}"
    
    # Допустимая погрешность (некоторые GLB имеют padding в конце)
    if actual_length > declared_length + 1024:
        return False, f"Файл длиннее ожидаемого: объявлено {declared_length}, получено {actual_length}"
    
    return True, declared_length


def get_tencent_headers(action, payload):
    """TC3-HMAC-SHA256 подпись (копия из app.py)."""
    service = "hunyuan"
    host = HUNYUAN_HOST
    region = "ap-singapore"
    version = "2023-09-01"
    algorithm = "TC3-HMAC-SHA256"
    timestamp = int(time.time())
    date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
    ct = "application/json"
    canonical_headers = f"content-type:{ct}\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{hashed_payload}"
    credential_scope = f"{date}/{service}/tc3_request"
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashed_canonical}"
    secret_date = hmac.new(("TC3" + TENCENT_SECRET_KEY).encode("utf-8"), date.encode("utf-8"), hashlib.sha256).digest()
    secret_service = hmac.new(secret_date, service.encode("utf-8"), hashlib.sha256).digest()
    secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = f"{algorithm} Credential={TENCENT_SECRET_ID}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
    return {
        "Authorization": authorization, "Content-Type": ct, "Host": host,
        "X-TC-Action": action, "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": version, "X-TC-Region": region
    }


def safe_request(method, url, max_retries=3, **kwargs):
    """HTTP с ретраями на сетевые ошибки (копия из app.py)."""
    for attempt in range(max_retries):
        try:
            if method == "post":
                return requests.post(url, **kwargs)
            else:
                return requests.get(url, **kwargs)
        except (Timeout, ConnectionError) as e:
            print(f"⚠️ Сетевая ошибка (попытка {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(15)
                continue
            raise


def download_glb_safe(glb_url, max_retries=3):
    """
    Скачивает GLB-файл с валидацией.
    Пробует несколько стратегий:
      1. Прямой GET (ждёт 2 сек перед скачиванием — CDN-задержка)
      2. GET с заголовком Accept: application/octet-stream
      3. Convert3DFormat GLB→GLB для получения нового URL
    Возвращает (data, "glb") или (data, "stl") при fallback.
    """
    # Стратегия 1: прямой GET с задержкой (CDN может не успеть)
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                time.sleep(3)  # Растущая задержка между попытками
            
            headers = {
                "Accept": "application/octet-stream, */*",
                "User-Agent": "Python-TelegramBot/3.0"
            }
            resp = requests.get(glb_url, headers=headers, timeout=120, stream=False)
            
            if resp.status_code != 200:
                print(f"[GLB download] HTTP {resp.status_code}, пробуем ещё...")
                continue
            
            data = resp.content
            valid, info = is_valid_glb(data)
            if valid:
                print(f"[GLB download] ✅ Валидный GLB, {info} байт (попытка {attempt+1})")
                return data, "glb"
            else:
                print(f"[GLB download] ❌ Битый GLB: {info} (попытка {attempt+1})")
                # Пробуем ещё раз — может, CDN не догрузил файл
                time.sleep(2)
                continue
        except Exception as e:
            print(f"[GLB download] Ошибка загрузки (попытка {attempt+1}): {e}")
            continue
    
    # Стратегия 2: Convert3DFormat GLB→GLB (Tencent сам скачает и отдаст публичный URL)
    print("[GLB download] Попытка через Convert3DFormat GLB→GLB...")
    try:
        conv_action = "Convert3DFormat"
        conv_payload = json.dumps({"File3D": glb_url, "Format": "GLB"})
        conv_headers = get_tencent_headers(conv_action, conv_payload)
        conv_resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=conv_headers, data=conv_payload, timeout=120)
        conv_data = conv_resp.json()
        
        if "Response" in conv_data and "ResultFile3D" in conv_data["Response"]:
            new_url = conv_data["Response"]["ResultFile3D"]
            print(f"[GLB download] Convert3DFormat вернул новый URL")
            
            for retry in range(3):
                time.sleep(2)
                resp = requests.get(new_url, timeout=120)
                if resp.status_code == 200:
                    data = resp.content
                    valid, info = is_valid_glb(data)
                    if valid:
                        print(f"[GLB download] ✅ Валидный GLB через конвертацию, {info} байт")
                        return data, "glb"
                    else:
                        print(f"[GLB download] Convert3DFormat GLB всё ещё битый: {info}")
                time.sleep(2)
    except Exception as e:
        print(f"[GLB download] Convert3DFormat GLB→GLB ошибка: {e}")
    
    # Стратегия 3: fallback на STL (гарантированно работает)
    print("[GLB download] Fallback: Convert3DFormat GLB→STL...")
    try:
        conv_action = "Convert3DFormat"
        conv_payload = json.dumps({"File3D": glb_url, "Format": "STL"})
        conv_headers = get_tencent_headers(conv_action, conv_payload)
        conv_resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=conv_headers, data=conv_payload, timeout=120)
        conv_data = conv_resp.json()
        
        if "Response" in conv_data and "ResultFile3D" in conv_data["Response"]:
            stl_url = conv_data["Response"]["ResultFile3D"]
            stl_resp = safe_request("get", stl_url, timeout=120)
            if stl_resp.status_code == 200 and len(stl_resp.content) > 0:
                print(f"[GLB download] ⚠️ Fallback на STL: {len(stl_resp.content)} байт")
                return stl_resp.content, "stl"
    except Exception as e:
        print(f"[GLB download] STL fallback ошибка: {e}")
    
    raise Exception("Не удалось скачать валидный GLB ни одним способом")


def hunyuan_generate_with_retry(prompt, output_format="stl", max_retries=3):
    """
    Генерация 3D-модели из текста через Hunyuan3D.

    ИСПРАВЛЕНИЕ GLB-БАГА:
    — GLB-файлы проходят валидацию по magic bytes перед возвратом
    — Битые GLB перекачиваются через Convert3DFormat или заменяются на STL
    — Добавлена задержка перед загрузкой (CDN-прогрев)
    — Добавлен заголовок Accept: application/octet-stream

    Для STL: конвертация через Convert3DFormat как раньше.
    """
    for attempt in range(max_retries):
        try:
            print(f"Hunyuan: попытка {attempt+1}, запрошенный формат: {output_format}")
            action = "SubmitHunyuanTo3DProJob"
            payload_dict = {
                "Model": "3.1",
                "Prompt": prompt[:900],
                "EnablePBR": True,
                "GenerateType": "Normal",
                "FaceCount": 1000000
            }
            payload = json.dumps(payload_dict)
            headers = get_tencent_headers(action, payload)
            resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=90)
            data = resp.json()
            
            if "Response" not in data or "JobId" not in data["Response"]:
                raise Exception(f"Hunyuan error: {data}")
            
            job_id = data["Response"]["JobId"]
            query_action = "QueryHunyuanTo3DProJob"
            
            for _ in range(40):  # 40 × 5 сек = 200 сек максимум
                time.sleep(5)
                payload = json.dumps({"JobId": job_id})
                headers = get_tencent_headers(query_action, payload)
                resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=90)
                data = resp.json()
                
                if "Response" in data:
                    status = data["Response"].get("Status")
                    if status == "DONE":
                        result_files = data["Response"].get("ResultFile3Ds", [])
                        if not result_files:
                            raise Exception("ResultFile3Ds пуст")
                        if "Url" not in result_files[0]:
                            raise Exception(f"Url отсутствует в ResultFile3Ds[0]: {list(result_files[0].keys())}")
                        
                        glb_url = result_files[0]["Url"]
                        print(f"[Hunyuan DONE] GLB URL получен: {glb_url[:80]}...")
                        
                        # === ИСПРАВЛЕНИЕ: задержка для CDN-прогрева ===
                        time.sleep(2)
                        
                        if output_format == "glb":
                            # === НОВОЕ: безопасная загрузка GLB с валидацией ===
                            glb_data, actual_fmt = download_glb_safe(glb_url)
                            if actual_fmt == "stl":
                                print("⚠️ GLB→STL fallback: возвращаю STL вместо GLB")
                            return glb_data, actual_fmt
                        
                        # === STL: конвертация как раньше ===
                        conv_action = "Convert3DFormat"
                        conv_payload = json.dumps({"File3D": glb_url, "Format": "STL"})
                        conv_headers = get_tencent_headers(conv_action, conv_payload)
                        conv_resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=conv_headers, data=conv_payload, timeout=120)
                        conv_data = conv_resp.json()
                        
                        if "Response" in conv_data and "ResultFile3D" in conv_data["Response"]:
                            stl_url = conv_data["Response"]["ResultFile3D"]
                            stl_resp = safe_request("get", stl_url, timeout=120)
                            if stl_resp.status_code == 200 and len(stl_resp.content) > 0:
                                print(f"Hunyuan: возвращаю STL, размер: {len(stl_resp.content)} байт")
                                return stl_resp.content, "stl"
                        
                        raise Exception("Не удалось конвертировать в STL")
                    
                    elif status == "FAIL":
                        error_msg = data["Response"].get("ErrorMsg", "неизвестно")
                        raise Exception(f"Generation FAILED: {error_msg}")
            
            if attempt < max_retries - 1:
                time.sleep(15)
                continue
            raise Exception("Hunyuan: тайм-аут ожидания (200 сек)")
            
        except Exception as e:
            print(f"Hunyuan ошибка в попытке {attempt+1}: {e}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(15)
    
    raise Exception("Hunyuan: не удалось сгенерировать модель")


def hunyuan_generate_from_photo(image_base64, output_format="stl", max_retries=3):
    """
    Генерация из фото. Та же логика с валидацией GLB.
    """
    for attempt in range(max_retries):
        try:
            print(f"Hunyuan фото: попытка {attempt+1}, запрошенный формат: {output_format}")
            action = "SubmitHunyuanTo3DProJob"
            payload_dict = {
                "Model": "3.1",
                "ImageBase64": image_base64,
                "EnablePBR": True,
                "GenerateType": "Normal",
                "FaceCount": 1000000
            }
            payload = json.dumps(payload_dict)
            headers = get_tencent_headers(action, payload)
            resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=90)
            data = resp.json()
            
            if "Response" not in data or "JobId" not in data["Response"]:
                raise Exception(f"Hunyuan error: {data}")
            
            job_id = data["Response"]["JobId"]
            query_action = "QueryHunyuanTo3DProJob"
            
            for _ in range(40):
                time.sleep(5)
                payload = json.dumps({"JobId": job_id})
                headers = get_tencent_headers(query_action, payload)
                resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=90)
                data = resp.json()
                
                if "Response" in data:
                    status = data["Response"].get("Status")
                    if status == "DONE":
                        result_files = data["Response"].get("ResultFile3Ds", [])
                        if not result_files or "Url" not in result_files[0]:
                            raise Exception("ResultFile3Ds пуст или без Url")
                        
                        glb_url = result_files[0]["Url"]
                        time.sleep(2)  # CDN-прогрев
                        
                        if output_format == "glb":
                            data_bytes, fmt = download_glb_safe(glb_url)
                            if fmt == "stl":
                                print("⚠️ Фото→GLB fallback: возвращаю STL вместо GLB")
                            return data_bytes, fmt
                        
                        # STL: конвертация
                        conv_action = "Convert3DFormat"
                        conv_payload = json.dumps({"File3D": glb_url, "Format": "STL"})
                        conv_headers = get_tencent_headers(conv_action, conv_payload)
                        conv_resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=conv_headers, data=conv_payload, timeout=120)
                        conv_data = conv_resp.json()
                        
                        if "Response" in conv_data and "ResultFile3D" in conv_data["Response"]:
                            stl_url = conv_data["Response"]["ResultFile3D"]
                            stl_resp = safe_request("get", stl_url, timeout=120)
                            if stl_resp.status_code == 200 and len(stl_resp.content) > 0:
                                print(f"Hunyuan фото: возвращаю STL, размер: {len(stl_resp.content)} байт")
                                return stl_resp.content, "stl"
                        
                        raise Exception("Не удалось конвертировать в STL")
                    
                    elif status == "FAIL":
                        raise Exception("Generation FAILED")
            
            if attempt < max_retries - 1:
                time.sleep(15)
                continue
            raise Exception("Hunyuan фото: тайм-аут ожидания")
            
        except Exception as e:
            print(f"Hunyuan фото ошибка в попытке {attempt+1}: {e}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(15)
    
    raise Exception("Hunyuan фото: не удалось сгенерировать модель")


# ============================================================
# ТЕСТ-ФУНКЦИЯ (запустить: python app_fixed_glb.py)
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("ТЕСТ: генерация GLB-модели через Hunyuan3D")
    print("=" * 60)
    
    if not TENCENT_SECRET_ID:
        print("❌ TENCENT_SECRET_ID не установлен. Укажите переменные окружения.")
        exit(1)
    
    prompt = "a simple 3D cube with rounded edges"
    print(f"Запрос: {prompt}")
    print(f"Формат: GLB")
    print()
    
    try:
        data, fmt = hunyuan_generate_with_retry(prompt, output_format="glb")
        print()
        print(f"✅ Успех! Формат: {fmt}, размер: {len(data)} байт")
        
        if fmt == "glb":
            valid, info = is_valid_glb(data)
            if valid:
                print(f"✅ GLB валиден: объявленный размер {info} байт")
                # Сохраняем для ручной проверки
                test_path = "test_output.glb" if fmt == "glb" else "test_output.stl"
                with open(test_path, "wb") as f:
                    f.write(data)
                print(f"📁 Сохранён в: {test_path}")
            else:
                print(f"❌ GLB НЕВАЛИДЕН: {info}")
        else:
            print(f"⚠️ Получен STL вместо GLB (fallback)")
            test_path = "test_output.stl"
            with open(test_path, "wb") as f:
                f.write(data)
            print(f"📁 Сохранён в: {test_path}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
