import os
import json
import time
import threading
import requests
import hashlib
import hmac
import base64
from datetime import datetime
from collections import defaultdict
from flask import Flask
from concurrent.futures import ThreadPoolExecutor
from requests.exceptions import Timeout, ConnectionError




print("🟢 [DEBUG] Загрузка app.py...", flush=True)




TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY")
ADMIN_CHAT_ID = 5193424909




if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN missing")
if not TENCENT_SECRET_ID or not TENCENT_SECRET_KEY:
    raise ValueError("TENCENT_SECRET_ID and TENCENT_SECRET_KEY required")




API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
HUNYUAN_HOST = "hunyuan.intl.tencentcloudapi.com"




print(f"🟢 [DEBUG] ADMIN_CHAT_ID = {ADMIN_CHAT_ID}", flush=True)




# ========== ПУЛ ПОТОКОВ ==========
executor = ThreadPoolExecutor(max_workers=5)


# ========== БЛОКИРОВКА ==========
user_busy = {}


# ========== ОТВЕТЫ АДМИНА ==========
pending_reply = {}
admin_reply_mode = {}


# ========== ЗАЩИТА ОТ СПАМА ==========
user_rate_limit = defaultdict(list)
USER_REQUESTS_PER_MINUTE = 3
USER_REQUESTS_PER_HOUR = 20




def check_rate_limit(user_id):
    now = time.time()
    user_rate_limit[user_id] = [t for t in user_rate_limit[user_id] if t > now - 3600]
    minute_ago = now - 60
    minute_requests = [t for t in user_rate_limit[user_id] if t > minute_ago]
    if len(minute_requests) >= USER_REQUESTS_PER_MINUTE:
        return False, "Слишком много запросов. Подождите минуту."
    if len(user_rate_limit[user_id]) >= USER_REQUESTS_PER_HOUR:
        return False, "Лимит запросов на час исчерпан. Попробуйте позже."
    user_rate_limit[user_id].append(now)
    return True, ""




def send_alert(message):
    if ADMIN_CHAT_ID:
        try:
            requests.post(API_URL + "/sendMessage", json={
                "chat_id": ADMIN_CHAT_ID,
                "text": f"⚠️ АЛЕРТ БОТА:\n{message}",
                "parse_mode": "HTML"
            }, timeout=5)
        except:
            pass




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




# ========== ПЕРЕСЫЛКА И ОТВЕТЫ ==========
def forward_to_admin(user_id, username, first_name, text):
    if not ADMIN_CHAT_ID:
        print("⚠️ ADMIN_CHAT_ID не задан")
        return
    try:
        name = first_name or ""
        user_tag = f"@{username}" if username else f"ID: {user_id}"
        
        pending_reply[ADMIN_CHAT_ID] = user_id
        admin_reply_mode[ADMIN_CHAT_ID] = True
        
        print(f"🟢 [DEBUG] Пересылаю сообщение админу {ADMIN_CHAT_ID} от {user_id}")
        
        reply_keyboard = {
            "inline_keyboard": [
                [{"text": "💬 Ответить", "callback_data": f"reply_to_{user_id}"}],
                [{"text": "❌ Отмена ответа", "callback_data": "cancel_reply"}]
            ]
        }
        
        resp = requests.post(API_URL + "/sendMessage", json={
            "chat_id": ADMIN_CHAT_ID,
            "text": f"📩 *Новое сообщение*\n"
                    f"👤 {name} ({user_tag})\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"💬 {text}\n"
                    f"━━━━━━━━━━━━━━━━\n\n"
                    f"💡 *Нажмите «Ответить» и напишите сообщение*",
            "parse_mode": "Markdown",
            "reply_markup": json.dumps(reply_keyboard)
        }, timeout=10)
        
        print(f"🟢 [DEBUG] Результат отправки админу: {resp.status_code}")
        
        if resp.status_code != 200:
            requests.post(API_URL + "/sendMessage", json={
                "chat_id": ADMIN_CHAT_ID,
                "text": f"📩 Сообщение от {name} ({user_tag}):\n{text}",
                "reply_markup": json.dumps(reply_keyboard)
            }, timeout=10)
    
    except Exception as e:
        print(f"❌ Ошибка пересылки: {e}")




def reply_to_user(admin_message_text):
    if ADMIN_CHAT_ID in pending_reply:
        user_id = pending_reply[ADMIN_CHAT_ID]
        print(f"🟢 [DEBUG] Отправляю ответ пользователю {user_id}")
        
        send_message(user_id, f"📨 *Ответ разработчика:*\n\n{admin_message_text}")
        
        send_keyboard = {
            "inline_keyboard": [
                [{"text": "💬 Ответить ещё", "callback_data": f"reply_to_{user_id}"}],
                [{"text": "❌ Завершить диалог", "callback_data": "cancel_reply"}]
            ]
        }
        send_message(ADMIN_CHAT_ID, f"✅ Ответ отправлен пользователю {user_id}", keyboard=send_keyboard)
    else:
        send_message(ADMIN_CHAT_ID, "ℹ️ Нет активного диалога. Сначала дождитесь сообщения от пользователя.")




# ========== СТАТИСТИКА ==========
STATS_FILE = "stats.json"


def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, 'r') as f:
                return json.load(f)
        except:
            return {"total_generations": 0, "total_users": [], "daily_stats": {}, "user_generations": {}}
    return {"total_generations": 0, "total_users": [], "daily_stats": {}, "user_generations": {}}




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
    
    if "user_generations" not in stats:
        stats["user_generations"] = {}
    stats["user_generations"][str(user_id)] = stats["user_generations"].get(str(user_id), 0) + 1
    
    save_stats(stats)




def send_stats(chat_id):
    stats = load_stats()
    today = datetime.now().strftime("%Y-%m-%d")
    
    text = (
        f"📊 *Статистика бота*\n\n"
        f"👥 Всего пользователей: {len(stats['total_users'])}\n"
        f"🎲 Всего генераций: {stats['total_generations']}\n"
        f"📅 Генераций сегодня: {stats['daily_stats'].get(today, 0)}\n\n"
    )
    
    user_gens = stats.get("user_generations", {})
    if user_gens:
        top_users = sorted(user_gens.items(), key=lambda x: x[1], reverse=True)[:10]
        text += "*🏆 Топ пользователей по генерациям:*\n"
        for uid, count in top_users:
            text += f"  ID: `{uid}` — {count} шт.\n"
    
    send_message(chat_id, text)




# ========== ЛИМИТЫ ПОЛЬЗОВАТЕЛЕЙ ==========
user_free_used = {}
user_paid_one = {}
user_subscription = {}




def can_generate(user_id):
    if user_subscription.get(user_id, 0) > time.time():
        return True, "premium"
    if user_paid_one.get(user_id):
        return True, "paid"
    if user_id == ADMIN_CHAT_ID:
        return True, "free"
    used = user_free_used.get(user_id, 0)
    if used < 5:
        return True, "free"
    return False, ""




def use_generation(user_id, generation_type):
    if generation_type == "free" and user_id != ADMIN_CHAT_ID:
        user_free_used[user_id] = user_free_used.get(user_id, 0) + 1
        if user_free_used[user_id] == 5:
            send_alert(f"Пользователь {user_id} использовал все 5 бесплатных генераций")




# ========== HUNYUAN API ==========
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




def safe_request(method, url, max_retries=3, **kwargs):
    for attempt in range(max_retries):
        try:
            if method == "post":
                return requests.post(url, **kwargs)
            else:
                return requests.get(url, **kwargs)
        except (Timeout, ConnectionError) as e:
            print(f"⚠️ Сетевая ошибка (попытка {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(10)
                continue
            raise
        except Exception as e:
            raise




def hunyuan_generate_with_retry(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            print(f"Генерация, попытка {attempt+1}")
            action = "SubmitHunyuanTo3DProJob"
            payload = json.dumps({"Prompt": prompt[:900]})
            headers = get_tencent_headers(action, payload)
            
            resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=60)
            data = resp.json()
            
            if "Response" not in data or "JobId" not in data["Response"]:
                raise Exception(f"Hunyuan error: {data}")
            
            job_id = data["Response"]["JobId"]
            action = "QueryHunyuanTo3DProJob"
            
            for _ in range(35):
                time.sleep(4)
                payload = json.dumps({"JobId": job_id})
                headers = get_tencent_headers(action, payload)
                
                resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=60)
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
                            
                            conv_resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=conv_headers, data=conv_payload, timeout=90)
                            conv_data = conv_resp.json()
                            
                            if "Response" in conv_data and "ResultFile3D" in conv_data["Response"]:
                                stl_url = conv_data["Response"]["ResultFile3D"]
                                
                                model_resp = safe_request("get", stl_url, timeout=90)
                                if model_resp.status_code == 200:
                                    return model_resp.content
                    elif status == "FAIL":
                        raise Exception("Generation FAILED")
            
            if attempt < max_retries - 1:
                print(f"Попытка {attempt+1} не удалась, жду 10 секунд...")
                time.sleep(10)
                continue
            raise Exception("Timeout")
        except Exception as e:
            print(f"Ошибка в попытке {attempt+1}: {e}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(10)
    raise Exception("Не удалось сгенерировать модель")




def hunyuan_generate_from_photo(image_base64, max_retries=3):
    for attempt in range(max_retries):
        try:
            print(f"Генерация по фото, попытка {attempt+1}")
            action = "SubmitHunyuanTo3DProJob"
            payload = json.dumps({"ImageBase64": image_base64})
            headers = get_tencent_headers(action, payload)
            
            resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=60)
            data = resp.json()
            
            if "Response" not in data or "JobId" not in data["Response"]:
                raise Exception(f"Hunyuan error: {data}")
            
            job_id = data["Response"]["JobId"]
            action = "QueryHunyuanTo3DProJob"
            
            for _ in range(35):
                time.sleep(4)
                payload = json.dumps({"JobId": job_id})
                headers = get_tencent_headers(action, payload)
                
                resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=60)
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
                            
                            conv_resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=conv_headers, data=conv_payload, timeout=90)
                            conv_data = conv_resp.json()
                            
                            if "Response" in conv_data and "ResultFile3D" in conv_data["Response"]:
                                stl_url = conv_data["Response"]["ResultFile3D"]
                                
                                model_resp = safe_request("get", stl_url, timeout=90)
                                if model_resp.status_code == 200:
                                    return model_resp.content
                    elif status == "FAIL":
                        raise Exception("Generation FAILED")
            
            if attempt < max_retries - 1:
                print(f"Попытка {attempt+1} не удалась, жду 10 секунд...")
                time.sleep(10)
                continue
            raise Exception("Timeout")
        except Exception as e:
            print(f"Ошибка в попытке {attempt+1}: {e}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(10)
    raise Exception("Не удалось сгенерировать модель")




# ========== ФОНОВАЯ ГЕНЕРАЦИЯ ==========
def process_text_generation(chat_id, user_id, prompt, gen_type):
    try:
        model = hunyuan_generate_with_retry(prompt)
        if model:
            use_generation(user_id, gen_type)
            update_stats(user_id)
            remaining = max(0, 5 - user_free_used.get(user_id, 0))
            caption = f"✅ Модель готова!\n📝 {prompt[:100]}"
            if gen_type == "free" and user_id != ADMIN_CHAT_ID:
                caption += f"\n🎁 Осталось бесплатно: {remaining} из 5"
            send_document(chat_id, model, caption=caption)
        else:
            send_message(chat_id, "❌ Ошибка генерации. Попробуйте ещё раз.")
    except Exception as e:
        send_message(chat_id, f"❌ Ошибка: {str(e)}")
        send_alert(f"Ошибка генерации у {user_id}: {str(e)}")
    finally:
        user_busy[user_id] = False




def process_photo_generation(chat_id, user_id, image_base64, gen_type):
    try:
        model = hunyuan_generate_from_photo(image_base64)
        if model:
            use_generation(user_id, gen_type)
            update_stats(user_id)
            remaining = max(0, 5 - user_free_used.get(user_id, 0))
            caption = f"✅ Модель по фото готова!"
            if gen_type == "free" and user_id != ADMIN_CHAT_ID:
                caption += f"\n🎁 Осталось бесплатно: {remaining} из 5"
            send_document(chat_id, model, caption=caption)
        else:
            send_message(chat_id, "❌ Ошибка генерации. Попробуйте другое фото.")
    except Exception as e:
        send_message(chat_id, f"❌ Ошибка: {str(e)}")
        send_alert(f"Ошибка генерации у {user_id}: {str(e)}")
    finally:
        user_busy[user_id] = False




# ========== КЛАВИАТУРЫ ==========
main_keyboard = {
    "inline_keyboard": [
        [{"text": "🔧 Генерация по тексту", "callback_data": "gen_text"}],
        [{"text": "🎨 Генерация по фото", "callback_data": "gen_photo"}],
        [{"text": "🎟 Разовая генерация (40⭐)", "callback_data": "buy_one"}],
        [{"text": "💎 Подписка (170⭐/мес)", "callback_data": "subscription"}],
        [{"text": "💰 Баланс и лимиты", "callback_data": "my_balance"}],
        [{"text": "❓ Как пользоваться", "callback_data": "help_info"}],
    ]
}
back_keyboard = {
    "inline_keyboard": [[{"text": "🔙 Главное меню", "callback_data": "menu"}]]
}
cancel_keyboard = {
    "inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel"}]]
}
choice_keyboard = {
    "inline_keyboard": [
        [{"text": "📝 По тексту", "callback_data": "gen_text_confirm"}],
        [{"text": "📸 По фото", "callback_data": "gen_photo_confirm"}],
        [{"text": "🔙 Назад", "callback_data": "menu"}],
    ]
}




user_states = {}




def handle_help_info(chat_id):
    text = (
        "❓ *Как пользоваться ботом:*\n\n"
        "1️⃣ Нажмите «Генерация по тексту» или «Генерация по фото»\n"
        "2️⃣ Выберите способ\n"
        "3️⃣ Отправьте описание или фото\n"
        "4️⃣ Подождите 1-5 минут — модель готова!\n\n"
        "🎁 *Бесплатно:* 5 моделей на первый раз\n"
        "🎟 *Разовая генерация:* 40⭐\n"
        "💎 *Подписка:* 170⭐/мес (безлимит)"
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
    
    text = f"💰 *Ваш баланс:*\n\n{status}"
    send_message(chat_id, text, keyboard=back_keyboard)




# ========== ОСНОВНОЙ ЦИКЛ ==========
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
                    
                    if "message" in update and "successful_payment" in update["message"]:
                        user_id = update["message"]["from"]["id"]
                        chat_id = update["message"]["chat"]["id"]
                        payload = update["message"]["successful_payment"]["invoice_payload"]
                        if payload == "single_generation":
                            user_paid_one[user_id] = True
                            send_message(chat_id, "✅ Разовая генерация активирована!")
                            send_alert(f"💰 Пользователь {user_id} купил разовую генерацию")
                        elif payload == "monthly_subscription":
                            user_subscription[user_id] = time.time() + 30 * 86400
                            send_message(chat_id, "✅ Подписка на месяц активирована!")
                            send_alert(f"💰 Пользователь {user_id} купил подписку на месяц")
                        continue
                    
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        chat_id = cb["message"]["chat"]["id"]
                        user_id = cb["from"]["id"]
                        data = cb["data"]
                        requests.post(API_URL + "/answerCallbackQuery", json={"callback_query_id": cb["id"]})
                        
                        if data == "menu":
                            admin_reply_mode.pop(chat_id, None)
                            send_message(chat_id, "Выбери действие:", keyboard=main_keyboard)
                        elif data == "help_info":
                            handle_help_info(chat_id)
                        elif data == "my_balance":
                            handle_balance(chat_id, user_id)
                        elif data == "buy_one":
                            send_invoice(chat_id, "Разовая генерация 3D-модели", "Одна генерация без подписки", "single_generation", 40)
                        elif data == "subscription":
                            send_invoice(chat_id, "Premium подписка", "Безлимит на месяц", "monthly_subscription", 170)
                        elif data == "gen_text":
                            send_message(chat_id, "🔧 Выберите способ генерации:", keyboard=choice_keyboard)
                        elif data == "gen_photo":
                            send_message(chat_id, "🔧 Выберите способ генерации:", keyboard=choice_keyboard)
                        elif data == "gen_text_confirm":
                            user_states[chat_id] = "awaiting_text"
                            send_message(chat_id, "📝 Отправьте текстовое описание модели.\n\nПример: «шестерня 61 мм, 73 зуба, высота 15 мм»", keyboard=cancel_keyboard)
                        elif data == "gen_photo_confirm":
                            user_states[chat_id] = "awaiting_photo"
                            send_message(chat_id, "📸 Отправьте фото объекта.\n\n⚠️ Генерация по фото может занять до 4-5 минут.", keyboard=cancel_keyboard)
                        elif data == "cancel":
                            if chat_id in user_states:
                                del user_states[chat_id]
                            send_message(chat_id, "❌ Действие отменено.", keyboard=main_keyboard)
                        elif data.startswith("reply_to_"):
                            target_user_id = int(data.replace("reply_to_", ""))
                            pending_reply[chat_id] = target_user_id
                            admin_reply_mode[chat_id] = True
                            requests.post(API_URL + "/sendMessage", json={
                                "chat_id": chat_id,
                                "text": f"✏️ Теперь напишите сообщение для пользователя {target_user_id}.\nНажмите /cancelreply для отмены."
                            })
                        elif data == "cancel_reply":
                            pending_reply.pop(chat_id, None)
                            admin_reply_mode.pop(chat_id, None)
                            send_message(chat_id, "❌ Режим ответа отменён.", keyboard=main_keyboard)
                        continue
                    
                    if "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        user_id = msg["from"]["id"]
                        
                        if "text" in msg and msg["text"] == "/start":
                            used = user_free_used.get(user_id, 0)
                            remain = max(0, 5 - used)
                            send_message(chat_id, f"👋 Привет! Я бот для генерации 3D-моделей по тексту или фото.\n\n🎁 *Бесплатно:* {remain} из 5 генераций.\n🎟 *Разовая:* 40⭐\n💎 *Подписка:* 170⭐/мес\n\nНажмите кнопку, чтобы начать:", keyboard=main_keyboard)
                            continue
                        
                        if "text" in msg and msg["text"] == "/stats" and user_id == ADMIN_CHAT_ID:
                            send_stats(chat_id)
                            continue
                        
                        if "text" in msg and msg["text"] == "/cancelreply" and user_id == ADMIN_CHAT_ID:
                            pending_reply.pop(chat_id, None)
                            admin_reply_mode.pop(chat_id, None)
                            send_message(chat_id, "❌ Режим ответа отменён.")
                            continue
                        
                        if user_id == ADMIN_CHAT_ID and admin_reply_mode.get(chat_id, False):
                            if "text" in msg and msg["text"] and not msg["text"].startswith('/'):
                                reply_to_user(msg["text"])
                            continue
                        
                        if chat_id in user_states:
                            state = user_states[chat_id]
                            
                            if state == "awaiting_text" and "text" in msg:
                                prompt = msg["text"]
                                if prompt.startswith('/'):
                                    continue
                                
                                del user_states[chat_id]
                                
                                rate_ok, rate_msg = check_rate_limit(user_id)
                                if not rate_ok:
                                    send_message(chat_id, f"⏳ {rate_msg}", keyboard=main_keyboard)
                                    continue
                                
                                if user_busy.get(user_id, False):
                                    send_message(chat_id, "⏳ У вас уже выполняется генерация. Дождитесь завершения.", keyboard=main_keyboard)
                                    continue
                                
                                can, gen_type = can_generate(user_id)
                                if not can:
                                    send_message(chat_id, "❌ Бесплатные генерации закончились. Купите разовую (40⭐) или подписку (170⭐/мес).", keyboard=main_keyboard)
                                    continue
                                
                                user_busy[user_id] = True
                                send_message(chat_id, "⏳ Генерирую модель по тексту... (1-4 мин)")
                                executor.submit(process_text_generation, chat_id, user_id, prompt, gen_type)
                                continue
                            
                            elif state == "awaiting_photo" and "photo" in msg:
                                del user_states[chat_id]
                                
                                rate_ok, rate_msg = check_rate_limit(user_id)
                                if not rate_ok:
                                    send_message(chat_id, f"⏳ {rate_msg}", keyboard=main_keyboard)
                                    continue
                                
                                if user_busy.get(user_id, False):
                                    send_message(chat_id, "⏳ У вас уже выполняется генерация. Дождитесь завершения.", keyboard=main_keyboard)
                                    continue
                                
                                can, gen_type = can_generate(user_id)
                                if not can:
                                    send_message(chat_id, "❌ Бесплатные генерации закончились. Купите разовую (40⭐) или подписку (170⭐/мес).", keyboard=main_keyboard)
                                    continue
                                
                                user_busy[user_id] = True
                                send_message(chat_id, "⏳ Генерирую модель по фото... (2-5 мин)")
                                
                                def photo_task(chat_id, user_id, gen_type, msg):
                                    try:
                                        file_id = msg["photo"][-1]["file_id"]
                                        file_info = requests.get(API_URL + f"/getFile?file_id={file_id}").json()
                                        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_info['result']['file_path']}"
                                        photo_data = requests.get(file_url).content
                                        image_base64 = base64.b64encode(photo_data).decode('utf-8')
                                        process_photo_generation(chat_id, user_id, image_base64, gen_type)
                                    except Exception as e:
                                        send_message(chat_id, f"❌ Ошибка загрузки фото: {str(e)}")
                                        user_busy[user_id] = False
                                
                                executor.submit(photo_task, chat_id, user_id, gen_type, msg)
                                continue
                            
                            elif "text" in msg and not msg["text"].startswith('/'):
                                if state == "awaiting_text":
                                    send_message(chat_id, "📝 Отправьте именно текстовое описание модели.", keyboard=cancel_keyboard)
                                elif state == "awaiting_photo":
                                    send_message(chat_id, "📸 Отправьте именно фото объекта.", keyboard=cancel_keyboard)
                                continue
                        
                        if "text" in msg and msg["text"] and not msg["text"].startswith('/') and user_id != ADMIN_CHAT_ID:
                            username = msg["from"].username if "username" in msg["from"] else None
                            first_name = msg["from"].first_name if "first_name" in msg["from"] else ""
                            forward_to_admin(user_id, username, first_name, msg["text"])
                            send_message(chat_id, "📨 Спасибо за сообщение! Я передал его разработчику. Обычно он отвечает в течение нескольких часов.")
                            continue
                        
            time.sleep(1)
        except Exception as e:
            print(f"Poll error: {e}")
            time.sleep(5)




flask_app = Flask(__name__)




@flask_app.route('/')
def home():
    return "Bot running"




@flask_app.route('/health')
def health():
    return "OK"




if __name__ == "__main__":
    print("🟢 Bot polling started", flush=True)
    threading.Thread(target=poll, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
