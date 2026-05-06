import os
import json
import time
import threading
import requests
import hashlib
import hmac
import base64
from datetime import datetime, timedelta
from collections import defaultdict
from flask import Flask


# ========== КОНФИГУРАЦИЯ ==========
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")  # ТВОЙ ID В TELEGRAM (узнать через @userinfobot)


if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN missing")
if not TENCENT_SECRET_ID or not TENCENT_SECRET_KEY:
    raise ValueError("TENCENT_SECRET_ID and TENCENT_SECRET_KEY required")
if not ADMIN_CHAT_ID:
    print("⚠️ ADMIN_CHAT_ID не задан, алерты не будут отправляться")


API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
HUNYUAN_HOST = "hunyuan.intl.tencentcloudapi.com"


# ========== ЗАЩИТА ОТ СПАМА (Rate Limiting) ==========
user_rate_limit = defaultdict(list)  # {user_id: [timestamps]}
USER_REQUESTS_PER_MINUTE = 3
USER_REQUESTS_PER_HOUR = 20


def check_rate_limit(user_id):
    now = time.time()
    # Очистка старых записей
    user_rate_limit[user_id] = [t for t in user_rate_limit[user_id] if t > now - 3600]
    
    # Проверка за минуту
    minute_ago = now - 60
    minute_requests = [t for t in user_rate_limit[user_id] if t > minute_ago]
    if len(minute_requests) >= USER_REQUESTS_PER_MINUTE:
        return False, "Слишком много запросов. Подождите минуту."
    
    # Проверка за час
    if len(user_rate_limit[user_id]) >= USER_REQUESTS_PER_HOUR:
        return False, "Дневной лимит запросов исчерпан. Попробуйте позже."
    
    user_rate_limit[user_id].append(now)
    return True, ""


def send_alert(message):
    """Отправка уведомления админу"""
    if ADMIN_CHAT_ID:
        try:
            requests.post(API_URL + "/sendMessage", json={
                "chat_id": ADMIN_CHAT_ID,
                "text": f"⚠️ АЛЕРТ БОТА:\n{message}",
                "parse_mode": "HTML"
            }, timeout=5)
        except:
            pass


# ========== СТАТИСТИКА ==========
STATS_FILE = "stats.json"
def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, 'r') as f:
                return json.load(f)
        except:
            return {"total_generations": 0, "total_users": [], "daily_stats": {}}
    return {"total_generations": 0, "total_users": [], "daily_stats": {}}


def save_stats(stats):
    try:
        with open(STATS_FILE, 'w') as f:
            json.dump(stats, f)
    except:
        pass


def update_stats(user_id):
    stats = load_stats()
    stats["total_generations"] += 1
    if user_id not in stats["total_users"]:
        stats["total_users"].append(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    if today not in stats["daily_stats"]:
        stats["daily_stats"][today] = 0
    stats["daily_stats"][today] += 1
    save_stats(stats)
    
    # Алерт при достижении 100 генераций за день
    if stats["daily_stats"][today] >= 100:
        send_alert(f"Достигнуто 100 генераций за день! Всего сегодня: {stats['daily_stats'][today]}")


# ========== ЛИМИТЫ ПОЛЬЗОВАТЕЛЕЙ ==========
user_free_used = {}       # сколько бесплатных генераций использовал пользователь
user_paid_one = {}        # активирована ли разовая генерация
user_subscription = {}    # до какого времени активна подписка


def can_generate(user_id):
    if user_subscription.get(user_id, 0) > time.time():
        return True, "premium"
    if user_paid_one.get(user_id):
        return True, "paid"
    used = user_free_used.get(user_id, 0)
    if used < 5:
        return True, "free"
    return False, ""


def use_generation(user_id, generation_type):
    if generation_type == "free":
        user_free_used[user_id] = user_free_used.get(user_id, 0) + 1
        remaining = 5 - user_free_used[user_id]
        if remaining == 0 and generation_type == "free":
            send_alert(f"Пользователь {user_id} использовал все 5 бесплатных генераций")
    # Для платных и премиум ничего не меняем


# ========== ФУНКЦИИ БОТА ==========
def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    requests.post(API_URL + "/sendMessage", json=payload, timeout=10)


def send_document(chat_id, file_content, filename="model.stl", caption=""):
    if not file_content:
        send_message(chat_id, "❌ Ошибка: файл пустой, попробуй ещё раз.")
        return
    files = {"document": (filename, file_content)}
    data = {"chat_id": chat_id, "caption": caption}
    requests.post(API_URL + "/sendDocument", files=files, data=data, timeout=60)


def send_invoice(chat_id, title, description, payload, stars_amount):
    url = API_URL + "/sendInvoice"
    prices = [{"label": "Оплата", "amount": stars_amount}]
    data = {
        "chat_id": chat_id,
        "title": title,
        "description": description,
        "payload": payload,
        "provider_token": "",
        "currency": "XTR",
        "prices": prices,
        "start_parameter": "generate_3d"
    }
    requests.post(url, json=data)


main_keyboard = {
    "inline_keyboard": [
        [{"text": "🔧 Сгенерировать модель", "callback_data": "gen_hunyuan"}],
        [{"text": "🎟 Разовая генерация (40⭐)", "callback_data": "buy_one"}],
        [{"text": "💎 Подписка (170⭐/мес)", "callback_data": "subscription"}],
        [{"text": "💰 Баланс и лимиты", "callback_data": "my_balance"}],
        [{"text": "❓ Как пользоваться", "callback_data": "help_info"}],
    ]
}
back_keyboard = {
    "inline_keyboard": [[{"text": "🔙 Главное меню", "callback_data": "menu"}]]
}


user_states = {}


def get_tencent_headers(action, payload):
    service = "hunyuan"
    host = HUNYUAN_HOST
    region = "ap-singapore"
    version = "2023-09-01"
    algorithm = "TC3-HMAC-SHA256"
    timestamp = int(time.time())
    date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")


    http_method = "POST"
    canonical_uri = "/"
    canonical_querystring = ""
    ct = "application/json"
    canonical_headers = f"content-type:{ct}\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = f"{http_method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{hashed_payload}"


    credential_scope = f"{date}/{service}/tc3_request"
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashed_canonical}"


    secret_date = hmac.new(("TC3" + TENCENT_SECRET_KEY).encode("utf-8"), date.encode("utf-8"), hashlib.sha256).digest()
    secret_service = hmac.new(secret_date, service.encode("utf-8"), hashlib.sha256).digest()
    secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


    authorization = f"{algorithm} Credential={TENCENT_SECRET_ID}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
    return {
        "Authorization": authorization,
        "Content-Type": ct,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": version,
        "X-TC-Region": region
    }


def hunyuan_generate_from_text(prompt):
    if len(prompt) > 900:
        prompt = prompt[:900] + "..."
    action = "SubmitHunyuanTo3DProJob"
    payload = json.dumps({"Prompt": prompt})
    headers = get_tencent_headers(action, payload)
    resp = requests.post(f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=30)
    data = resp.json()
    if "Response" not in data or "JobId" not in data["Response"]:
        raise Exception(f"Hunyuan submit error: {data}")
    job_id = data["Response"]["JobId"]


    action = "QueryHunyuanTo3DProJob"
    for _ in range(50):
        time.sleep(6)
        payload = json.dumps({"JobId": job_id})
        headers = get_tencent_headers(action, payload)
        resp = requests.post(f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=30)
        data = resp.json()
        if "Response" in data:
            status = data["Response"].get("Status")
            if status == "DONE":
                result_files = data["Response"].get("ResultFile3Ds", [])
                if result_files and "Url" in result_files[0]:
                    glb_url = result_files[0]["Url"]
                    conv_action = "Convert3DFormat"
                    conv_payload = json.dumps({"File3D": glb_url, "Format": "STL"})
                    conv_headers = get_tencent_headers(conv_action, conv_payload)
                    conv_resp = requests.post(f"https://{HUNYUAN_HOST}", headers=conv_headers, data=conv_payload, timeout=60)
                    conv_data = conv_resp.json()
                    if "Response" in conv_data and "ResultFile3D" in conv_data["Response"]:
                        stl_url = conv_data["Response"]["ResultFile3D"]
                        model_resp = requests.get(stl_url, timeout=60)
                        if model_resp.status_code == 200:
                            return model_resp.content
                        else:
                            raise Exception("Failed to download STL")
                    else:
                        raise Exception(f"Conversion error: {conv_data}")
                else:
                    raise Exception("No result file URL")
            elif status == "FAIL":
                raise Exception("Generation failed")
    raise Exception("Hunyuan timeout")


def hunyuan_generate_from_photo(image_base64):
    action = "SubmitHunyuanTo3DProJob"
    payload = json.dumps({"ImageBase64": image_base64})
    headers = get_tencent_headers(action, payload)
    resp = requests.post(f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=30)
    data = resp.json()
    if "Response" not in data or "JobId" not in data["Response"]:
        raise Exception(f"Hunyuan submit error: {data}")
    job_id = data["Response"]["JobId"]
    
    action = "QueryHunyuanTo3DProJob"
    for _ in range(50):
        time.sleep(6)
        payload = json.dumps({"JobId": job_id})
        headers = get_tencent_headers(action, payload)
        resp = requests.post(f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=30)
        data = resp.json()
        if "Response" in data:
            status = data["Response"].get("Status")
            if status == "DONE":
                result_files = data["Response"].get("ResultFile3Ds", [])
                if result_files and "Url" in result_files[0]:
                    glb_url = result_files[0]["Url"]
                    conv_action = "Convert3DFormat"
                    conv_payload = json.dumps({"File3D": glb_url, "Format": "STL"})
                    conv_headers = get_tencent_headers(conv_action, conv_payload)
                    conv_resp = requests.post(f"https://{HUNYUAN_HOST}", headers=conv_headers, data=conv_payload, timeout=60)
                    conv_data = conv_resp.json()
                    if "Response" in conv_data and "ResultFile3D" in conv_data["Response"]:
                        stl_url = conv_data["Response"]["ResultFile3D"]
                        model_resp = requests.get(stl_url, timeout=60)
                        if model_resp.status_code == 200:
                            return model_resp.content
                        else:
                            raise Exception("Failed to download STL")
                    else:
                        raise Exception(f"Conversion error: {conv_data}")
                else:
                    raise Exception("No result file URL")
            elif status == "FAIL":
                raise Exception("Generation failed")
    raise Exception("Hunyuan timeout")


def handle_help_info(chat_id):
    text = (
        "❓ *Как пользоваться ботом:*\n\n"
        "1️⃣ Нажмите «Сгенерировать модель»\n"
        "2️⃣ Отправьте текст с описанием или фото\n"
        "3️⃣ Подождите 1-3 минуты — модель готова!\n\n"
        "🎁 *Бесплатно:* 5 моделей на первый раз\n"
        "🎟 *Разовая генерация:* 40⭐\n"
        "💎 *Подписка:* 170⭐/мес (безлимит)\n\n"
        "💰 *Баланс:* показывает оставшиеся бесплатные генерации\n\n"
        "📦 *Мои модели:* история появится позже\n\n"
        "По вопросам: @ваш_контакт"
    )
    send_message(chat_id, text, keyboard=back_keyboard)


def handle_balance(chat_id, user_id):
    used = user_free_used.get(user_id, 0)
    remaining = max(0, 5 - used)
    is_premium = user_subscription.get(user_id, 0) > time.time()
    has_paid = user_paid_one.get(user_id, False)
    
    if is_premium:
        status = "💎 Премиум активна"
    elif has_paid:
        status = "🎟 Есть разовая генерация"
    else:
        status = f"🎁 Бесплатно осталось: {remaining} из 5"
    
    text = f"💰 *Ваш баланс:*\n\n{status}\n\n"
    if not is_premium and not has_paid:
        text += "Когда закончатся бесплатные — купите разовую генерацию или подписку."
    send_message(chat_id, text, keyboard=back_keyboard)


last_update_id = 0


def poll():
    global last_update_id
    print("🟢 Bot polling started")
    send_alert("✅ Бот запущен и готов к работе!")
    
    while True:
        try:
            resp = requests.get(API_URL + "/getUpdates", params={"offset": last_update_id + 1, "timeout": 30}, timeout=35)
            if resp.status_code == 200:
                for update in resp.json().get("result", []):
                    last_update_id = update["update_id"]
                    
                    # Успешная оплата
                    if "message" in update and "successful_payment" in update["message"]:
                        user_id = update["message"]["from"]["id"]
                        payload = update["message"]["successful_payment"]["invoice_payload"]
                        if payload == "single_generation":
                            user_paid_one[user_id] = True
                            send_message(user_id, "✅ Разовая генерация активирована! Теперь выберите «Сгенерировать модель».")
                            send_alert(f"💰 Пользователь {user_id} купил разовую генерацию")
                        elif payload == "monthly_subscription":
                            user_subscription[user_id] = time.time() + 30 * 86400
                            send_message(user_id, "✅ Подписка на месяц активирована! Безлимит до " + time.ctime(user_subscription[user_id]))
                            send_alert(f"💰 Пользователь {user_id} купил подписку на месяц")
                        continue
                    
                    # Нажатие на кнопки
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        chat_id = cb["message"]["chat"]["id"]
                        user_id = cb["from"]["id"]
                        data = cb["data"]
                        requests.post(API_URL + "/answerCallbackQuery", json={"callback_query_id": cb["id"]})
                        
                        if data == "menu":
                            send_message(chat_id, "Выбери действие:", keyboard=main_keyboard)
                        elif data == "help_info":
                            handle_help_info(chat_id)
                        elif data == "my_balance":
                            handle_balance(chat_id, user_id)
                        elif data == "buy_one":
                            send_invoice(chat_id, "Разовая генерация 3D-модели", "Одна генерация без подписки", "single_generation", 40)
                        elif data == "subscription":
                            send_invoice(chat_id, "Premium подписка", "Безлимит на месяц", "monthly_subscription", 170)
                        elif data == "my_models":
                            send_message(chat_id, "📦 История моделей появится в следующей версии.", keyboard=back_keyboard)
                        elif data == "gen_hunyuan":
                            user_states[chat_id] = "hunyuan"
                            send_message(chat_id, "🔧 Выберите способ:\n\n📝 Отправьте ТЕКСТ с описанием модели\n📸 Или отправьте ФОТО объекта", keyboard=back_keyboard)
                    
                    # Обычные сообщения
                    elif "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        user_id = msg["from"]["id"]
                        
                        if "text" in msg and msg["text"] == "/start":
                            used = user_free_used.get(user_id, 0)
                            remain = max(0, 5 - used)
                            send_message(chat_id, f"👋 Привет! Я бот для генерации 3D-моделей по тексту или фото.\n\n🎁 Бесплатно: {remain} из 5 генераций.\n🎟 Разовая — 40⭐\n💎 Подписка — 170⭐/мес\n\nНажмите «Сгенерировать модель», чтобы начать:", keyboard=main_keyboard)
                            continue
                        
                        if chat_id in user_states:
                            engine = user_states.pop(chat_id)
                            
                            # Rate limiting
                            rate_ok, rate_msg = check_rate_limit(user_id)
                            if not rate_ok:
                                send_message(chat_id, f"⏳ {rate_msg}", keyboard=main_keyboard)
                                continue
                            
                            can, gen_type = can_generate(user_id)
                            if not can:
                                send_message(chat_id, f"❌ Бесплатные генерации закончились. Купите разовую (40⭐) или подписку (170⭐/мес).", keyboard=main_keyboard)
                                continue
                            
                            # Генерация
                            if "text" in msg and msg["text"]:
                                prompt = msg["text"]
                                send_message(chat_id, "⏳ Генерирую модель по тексту... (1-3 мин)")
                                try:
                                    model = hunyuan_generate_from_text(prompt)
                                    if model:
                                        use_generation(user_id, gen_type)
                                        update_stats(user_id)
                                        remaining = max(0, 5 - user_free_used.get(user_id, 0))
                                        caption = f"✅ Модель готова!\n📝 {prompt[:100]}"
                                        if gen_type == "free":
                                            caption += f"\n🎁 Осталось бесплатно: {remaining} из 5"
                                        send_document(chat_id, model, caption=caption)
                                    else:
                                        send_message(chat_id, "❌ Ошибка генерации. Попробуйте ещё раз.")
                                except Exception as e:
                                    send_message(chat_id, f"❌ Ошибка: {str(e)}")
                                    send_alert(f"❌ Ошибка генерации у {user_id}: {str(e)}")
                            
                            elif "photo" in msg:
                                send_message(chat_id, "⏳ Генерирую модель по фото... (1-3 мин)")
                                try:
                                    file_id = msg["photo"][-1]["file_id"]
                                    file_info = requests.get(API_URL + f"/getFile?file_id={file_id}").json()
                                    file_path = file_info["result"]["file_path"]
                                    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                                    photo_data = requests.get(file_url).content
                                    image_base64 = base64.b64encode(photo_data).decode('utf-8')
                                    
                                    model = hunyuan_generate_from_photo(image_base64)
                                    if model:
                                        use_generation(user_id, gen_type)
                                        update_stats(user_id)
                                        remaining = max(0, 5 - user_free_used.get(user_id, 0))
                                        caption = f"✅ Модель по фото готова!"
                                        if gen_type == "free":
                                            caption += f"\n🎁 Осталось бесплатно: {remaining} из 5"
                                        send_document(chat_id, model, caption=caption)
                                    else:
                                        send_message(chat_id, "❌ Ошибка генерации. Попробуйте другое фото.")
                                except Exception as e:
                                    send_message(chat_id, f"❌ Ошибка: {str(e)}")
                                    send_alert(f"❌ Ошибка генерации по фото у {user_id}: {str(e)}")
                            else:
                                send_message(chat_id, "Пожалуйста, отправьте текст или фото.", keyboard=back_keyboard)
                        else:
                            send_message(chat_id, "Сначала нажмите «Сгенерировать модель» в меню.", keyboard=main_keyboard)
            time.sleep(1)
        except Exception as e:
            print(f"Poll error: {e}")
            send_alert(f"⚠️ Критическая ошибка в poll(): {str(e)}")
            time.sleep(5)


flask_app = Flask(__name__)


@flask_app.route('/')
def home():
    return "Bot running (Hunyuan only)"


@flask_app.route('/health')
def health():
    return "OK"


if __name__ == "__main__":
    print("🟢 Bot polling started")
    threading.Thread(target=poll, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
