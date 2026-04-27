import requests
import json
import time
import os
import base64
from datetime import date, timedelta
from flask import Flask


# ========== ТОКЕНЫ ==========
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")


if not TELEGRAM_BOT_TOKEN or not MESHY_API_KEY:
    raise ValueError("TELEGRAM_BOT_TOKEN и MESHY_API_KEY должны быть заданы!")


app = Flask(__name__)


API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MESHY_BASE = "https://api.meshy.ai/openapi/v1"
USAGE_FILE = "user_usage.json"
user_states = {}


print("🟢 Бот запускается...")


# ========== ЛИМИТЫ И PREMIUM ==========
def load_usage():
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_usage(usage):
    try:
        with open(USAGE_FILE, 'w') as f:
            json.dump(usage, f)
    except:
        pass


def get_user(user_id):
    usage = load_usage()
    uid = str(user_id)
    today = date.today().isoformat()
    if uid not in usage:
        return "free", 3
    if usage[uid].get("premium_until", "") >= today:
        return "premium", float('inf')
    if usage[uid].get("date") == today:
        return "free", max(0, 3 - usage[uid].get("count", 0))
    return "free", 3


def decrement_limit(user_id):
    usage = load_usage()
    uid = str(user_id)
    today = date.today().isoformat()
    if uid not in usage or usage[uid].get("date") != today:
        usage[uid] = {"date": today, "count": 1}
    else:
        usage[uid]["count"] = usage[uid].get("count", 0) + 1
    save_usage(usage)


def activate_premium(user_id, days=30):
    usage = load_usage()
    uid = str(user_id)
    until = (date.today() + timedelta(days=days)).isoformat()
    usage[uid] = usage.get(uid, {})
    usage[uid]["premium_until"] = until
    save_usage(usage)
    print(f"✅ Premium активирован для {user_id} до {until}")


# ========== ОТПРАВКА ==========
def send_message(chat_id, text, keyboard=None):
    url = API_URL + "/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    try:
        requests.post(url, json=payload, timeout=10)
    except:
        pass


def send_document(chat_id, file_bytes, filename, caption=""):
    url = API_URL + "/sendDocument"
    files = {"document": (filename, file_bytes)}
    data = {"chat_id": chat_id, "caption": caption}
    try:
        requests.post(url, files=files, data=data, timeout=120)
    except Exception as e:
        print(f"Ошибка отправки: {e}")


# ========== КЛАВИАТУРЫ ==========
def main_keyboard():
    return {"inline_keyboard": [
        [{"text": "🎲 Генерация по тексту", "callback_data": "gen_text"}],
        [{"text": "🎨 Генерация по фото", "callback_data": "gen_photo"}],
        [{"text": "💎 Купить Premium (299 ⭐)", "callback_data": "buy_premium"}]
    ]}


# ========== ГЕНЕРАЦИЯ 3D (оставляем твою рабочую версию) ==========
def generate_3d_model(prompt=None, image_base64=None, image_caption=None):
    headers = {
        "Authorization": f"Bearer {MESHY_API_KEY}",
        "Content-Type": "application/json"
    }


    if image_base64:
        endpoint = f"{MESHY_BASE}/image-to-3d"
        data_uri = f"data:image/jpeg;base64,{image_base64}"
        payload = {
            "image_url": data_uri,
            "should_remesh": True,
            "should_texture": True,
            "enable_pbr": False,
            "ai_model": "meshy-6"
        }
        if image_caption and image_caption.strip():
            payload["texture_prompt"] = image_caption.strip()
    else:
        endpoint = f"{MESHY_BASE}/text-to-3d"
        payload = {
            "mode": "preview",
            "prompt": prompt,
            "art_style": "realistic",
            "should_remesh": True,
            "should_texture": True
        }


    response = requests.post(endpoint, headers=headers, json=payload, timeout=45)
    if response.status_code not in (200, 202):
        raise Exception(f"Meshy ошибка: {response.status_code}")


    data = response.json()
    task_id = data.get("result") or data.get("task_id")
    if not task_id:
        raise Exception("Не получили task_id")


    # Polling статуса (оставляем как работало с котёнком)
    start_time = time.time()
    status_endpoint = f"{endpoint}/{task_id}"
    
    while time.time() - start_time < 420:  # 7 минут
        time.sleep(10)
        status_resp = requests.get(status_endpoint, headers=headers, timeout=35)
        if status_resp.status_code == 200:
            sdata = status_resp.json()
            status = sdata.get("status")
            if status == "SUCCEEDED":
                model_url = (sdata.get("model_urls") or sdata.get("result", {})).get("glb")
                if model_url:
                    model_resp = requests.get(model_url, timeout=120)
                    if model_resp.status_code == 200:
                        return model_resp.content
            elif status == "FAILED":
                raise Exception("Генерация провалилась")
    raise Exception("Таймаут генерации")


# ========== ОПЛАТА ЧЕРЕЗ TELEGRAM STARS ==========
def handle_buy_premium(chat_id, user_id):
    text = (
        "💎 <b>Premium-подписка</b>\n\n"
        "✅ Безлимит генераций 3D-моделей\n"
        "Срок: 30 дней\n"
        "Цена: <b>299 Telegram Stars</b>\n\n"
        "Нажми кнопку ниже для оплаты."
    )
    
    payload = {
        "chat_id": chat_id,
        "title": "Premium 3D Bot — 30 дней",
        "description": "Безлимит генераций моделей для 3D-печати",
        "payload": f"premium_{user_id}_{int(time.time())}",
        "provider_token": "",           # важно оставить пустым
        "currency": "XTR",
        "prices": [{"label": "Premium на 30 дней", "amount": 299}]
    }
    
    response = requests.post(API_URL + "/createInvoiceLink", json=payload)
    
    if response.status_code == 200:
        link = response.json()["result"]
        keyboard = {"inline_keyboard": [[{"text": "💳 Оплатить 299 ⭐", "url": link}]]}
        send_message(chat_id, text, keyboard)
    else:
        send_message(chat_id, "❌ Не удалось создать ссылку оплаты. Попробуй позже.")


# ========== ОСНОВНОЙ ЦИКЛ ==========
def poll_updates():
    last_update_id = 0
    print("🟢 Бот запущен и слушает обновления...")
    
    while True:
        try:
            resp = requests.get(API_URL + "/getUpdates", 
                               params={"offset": last_update_id + 1, "timeout": 30}, 
                               timeout=45)
            
            if resp.status_code == 200:
                updates = resp.json().get("result", [])
                for update in updates:
                    last_update_id = update["update_id"]


                    # Callback-кнопки
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        chat_id = cb["message"]["chat"]["id"]
                        user_id = cb["from"]["id"]
                        data = cb["data"]
                        requests.post(API_URL + "/answerCallbackQuery", json={"callback_query_id": cb["id"]})


                        if data == "buy_premium":
                            handle_buy_premium(chat_id, user_id)
                        elif data == "gen_text":
                            user_states[chat_id] = "awaiting_text"
                            send_message(chat_id, "🎲 Напиши описание модели:")
                        elif data == "gen_photo":
                            user_states[chat_id] = "awaiting_photo"
                            send_message(chat_id, "📸 Отправь фото (можно с подписью для лучшего качества).")


                    # Сообщения
                    elif "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        user_id = msg["from"]["id"]


                        if msg.get("text") == "/start":
                            send_message(chat_id, "👋 Привет! Я создаю 3D-модели для печати.\nБесплатно — 3 модели в день.", main_keyboard())
                            continue


                        # Успешная оплата Stars
                        if "successful_payment" in msg:
                            payment = msg["successful_payment"]
                            if payment["currency"] == "XTR":
                                activate_premium(user_id, days=30)
                                send_message(chat_id, "🎉 Спасибо! Premium активирован на 30 дней.\nТеперь у тебя безлимит генераций!")
                            continue


                        # Pre-checkout (обязательно для Stars)
                        if "pre_checkout_query" in update:
                            pre = update["pre_checkout_query"]
                            requests.post(API_URL + "/answerPreCheckoutQuery", 
                                        json={"pre_checkout_query_id": pre["id"], "ok": True})
                            continue


                        # Генерация по тексту и фото (оставь свой рабочий код здесь)
                        # ... (вставь сюда обработку gen_text и gen_photo из предыдущей версии)


            time.sleep(1)
        except Exception as e:
            print(f"Ошибка в цикле: {e}")
            time.sleep(5)


# ========== FLASK ==========
@app.route('/')
def home():
    return "3D Bot работает"


if __name__ == "__main__":
    import threading
    threading.Thread(target=poll_updates, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
