import os
import json
import time
import threading
import requests
import hashlib
import hmac
import base64
from datetime import datetime
from flask import Flask


# ========== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ==========
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY")


if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN missing")


API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ TELEGRAM ==========
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


# ========== КЛАВИАТУРЫ ==========
main_keyboard = {
    "inline_keyboard": [
        [{"text": "🎲 Meshy (Быстрая)", "callback_data": "gen_meshy"}],
        [{"text": "🔧 Hunyuan (Точная)", "callback_data": "gen_hunyuan"}],
        [{"text": "🎟 Разовая генерация (40⭐)", "callback_data": "buy_one"}],
        [{"text": "💎 Подписка (170⭐/мес)", "callback_data": "subscription"}],
        [{"text": "📦 Мои модели", "callback_data": "my_models"}],
        [{"text": "❓ Что тут можно сделать", "callback_data": "help_info"}],
    ]
}
back_keyboard = {
    "inline_keyboard": [[{"text": "🔙 Главное меню", "callback_data": "menu"}]]
}


user_states = {}
user_limits = {}
user_paid_one = {}
user_subscription = {}


HUNYUAN_HOST = "hunyuan.intl.tencentcloudapi.com"
MESHY_API_URL = "https://api.meshy.ai/openapi/v2"


# ========== HUNYUAN (без изменений) ==========
def get_tencent_headers(action, payload):
    # ... (здесь весь старый код, без изменений) ...
    # пожалуйста, используй тот заголовок, который был в твоём исходном app.py
    # я сокращаю, чтобы не загромождать ответ, но в твоём файле он должен быть полным
    pass


def hunyuan_generate_from_text(prompt):
    # ... (старый рабочий код) ...
    pass


def hunyuan_generate_from_photo(image_base64):
    # ... (старый рабочий код) ...
    pass


# ========== MESHY — ИСПРАВЛЕННАЯ ЛОГИКА ==========
def meshy_generate_from_text(prompt):
    if not MESHY_API_KEY:
        raise Exception("Meshy API key not configured")


    headers = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
    
    # --- ЭТАП 1: PREVIEW (Черновая модель) ---
    preview_payload = {
        "mode": "preview",
        "prompt": prompt,
        "art_style": "realistic",
        "should_remesh": True
    }
    preview_resp = requests.post(f"{MESHY_API_URL}/text-to-3d", headers=headers, json=preview_payload, timeout=30)
    if preview_resp.status_code not in (200, 202):
        raise Exception(f"Meshy preview error {preview_resp.status_code}: {preview_resp.text}")
    preview_task_id = preview_resp.json().get("result")
    if not preview_task_id:
        raise Exception("No preview_task_id")
    
    # Ожидание завершения PREVIEW
    while True:
        time.sleep(5)
        status_resp = requests.get(f"{MESHY_API_URL}/text-to-3d/{preview_task_id}", headers=headers)
        if status_resp.status_code == 200:
            data = status_resp.json()
            if data.get("status") == "SUCCEEDED":
                break
            elif data.get("status") == "FAILED":
                raise Exception("Meshy preview task failed")
        else:
            print(f"Meshy preview status check: {status_resp.status_code}")


    # --- ЭТАП 2: REFINE (Финальная модель с текстурами) ---
    refine_payload = {
        "mode": "refine",
        "preview_task_id": preview_task_id
    }
    refine_resp = requests.post(f"{MESHY_API_URL}/text-to-3d", headers=headers, json=refine_payload, timeout=30)
    if refine_resp.status_code not in (200, 202):
        raise Exception(f"Meshy refine error {refine_resp.status_code}: {refine_resp.text}")
    refine_task_id = refine_resp.json().get("result")
    if not refine_task_id:
        raise Exception("No refine_task_id")


    # Ожидание завершения REFINE
    while True:
        time.sleep(5)
        status_resp = requests.get(f"{MESHY_API_URL}/text-to-3d/{refine_task_id}", headers=headers)
        if status_resp.status_code == 200:
            data = status_resp.json()
            if data.get("status") == "SUCCEEDED":
                model_url = data.get("model_urls", {}).get("glb")
                if model_url:
                    model_resp = requests.get(model_url, timeout=60)
                    if model_resp.status_code == 200:
                        return model_resp.content
                    else:
                        raise Exception("Failed to download model")
                else:
                    raise Exception("No model URL after refine")
            elif data.get("status") == "FAILED":
                raise Exception("Meshy refine task failed")
        else:
            print(f"Meshy refine status check: {status_resp.status_code}")


def meshy_generate_from_photo(image_base64):
    # Аналогичная логика для фото (через /image-to-3d)
    # Пока оставим заглушку, если нужно — допишем
    raise Exception("Фото через Meshy пока не реализовано")


# ========== ЛИМИТЫ И ПРОВЕРКИ ==========
def can_generate(user_id, engine):
    today = int(time.time() // 86400)
    expiry = user_subscription.get(user_id, 0)
    if expiry > time.time():
        return True
    if user_paid_one.get(user_id) == today:
        return True
    if user_id not in user_limits or user_limits[user_id][0] != today:
        user_limits[user_id] = (today, 0, 0)
    meshy_count, hunyuan_count = user_limits[user_id][1], user_limits[user_id][2]
    if engine == "meshy":
        return meshy_count < 20
    else:
        return hunyuan_count < 10


def use_generation(user_id, engine):
    today = int(time.time() // 86400)
    expiry = user_subscription.get(user_id, 0)
    if expiry > time.time() or user_paid_one.get(user_id) == today:
        return
    if user_id not in user_limits or user_limits[user_id][0] != today:
        user_limits[user_id] = (today, 0, 0)
    meshy_count, hunyuan_count = user_limits[user_id][1], user_limits[user_id][2]
    if engine == "meshy":
        user_limits[user_id] = (today, meshy_count + 1, hunyuan_count)
    else:
        user_limits[user_id] = (today, meshy_count, hunyuan_count + 1)


def handle_help_info(chat_id):
    text = (
        "❓ *Что умеет бот:*\n\n"
        "🎲 *Meshy (Быстрая)* — генерирует 3D-модель по тексту или фото. Подходит для фигурок, органики, быстрых прототипов.\n"
        "Бесплатно: 20 моделей в день.\n\n"
        "🔧 *Hunyuan (Точная)* — генерирует инженерные детали по тексту или фото. Подходит для печати функциональных узлов.\n"
        "Бесплатно: 10 моделей в день.\n\n"
        "🎟 *Разовая генерация* — 40⭐ (≈70₽). Снимает лимит на 1 модель.\n\n"
        "💎 *Подписка* — 170⭐/мес (≈299₽). Безлимит на месяц.\n\n"
        "📦 *История* — скоро.\n\n"
        "Просто выбери движок и отправь текст или фото."
    )
    send_message(chat_id, text, keyboard=back_keyboard)


# ========== ОСНОВНОЙ ЦИКЛ ==========
last_update_id = 0


def poll():
    global last_update_id
    while True:
        try:
            resp = requests.get(API_URL + "/getUpdates", params={"offset": last_update_id + 1, "timeout": 30}, timeout=35)
            if resp.status_code == 200:
                for update in resp.json().get("result", []):
                    last_update_id = update["update_id"]
                    
                    if "message" in update and "successful_payment" in update["message"]:
                        user_id = update["message"]["from"]["id"]
                        payload = update["message"]["successful_payment"]["invoice_payload"]
                        if payload == "single_generation":
                            user_paid_one[user_id] = int(time.time() // 86400)
                            send_message(user_id, "✅ Разовая генерация активирована!")
                        elif payload == "monthly_subscription":
                            user_subscription[user_id] = time.time() + 30 * 86400
                            send_message(user_id, "✅ Подписка на месяц активирована!")
                        continue
                    
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        chat_id = cb["message"]["chat"]["id"]
                        data = cb["data"]
                        requests.post(API_URL + "/answerCallbackQuery", json={"callback_query_id": cb["id"]})
                        
                        if data == "menu":
                            send_message(chat_id, "Выбери действие:", keyboard=main_keyboard)
                        elif data == "help_info":
                            handle_help_info(chat_id)
                        elif data == "buy_one":
                            send_invoice(chat_id, "Разовая генерация 3D-модели", "Одна генерация без подписки", "single_generation", 40)
                        elif data == "subscription":
                            send_invoice(chat_id, "Premium подписка", "Безлимит на месяц", "monthly_subscription", 170)
                        elif data == "my_models":
                            send_message(chat_id, "📦 История появится позже.", keyboard=back_keyboard)
                        elif data == "gen_meshy":
                            user_states[chat_id] = ("meshy", None)
                            send_message(chat_id, "🎲 Выбран Meshy. Теперь отправь текст или фото.", keyboard=back_keyboard)
                        elif data == "gen_hunyuan":
                            user_states[chat_id] = ("hunyuan", None)
                            send_message(chat_id, "🔧 Выбран Hunyuan. Теперь отправь текст или фото.", keyboard=back_keyboard)
                    
                    elif "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        user_id = msg["from"]["id"]
                        
                        if "text" in msg and msg["text"] == "/start":
                            send_message(chat_id, "👋 Привет! Я бот для генерации 3D-моделей.\n\nБесплатно:\n🎲 Meshy — 20/день\n🔧 Hunyuan — 10/день\n🎟 Разовая — 40⭐\n💎 Подписка — 170⭐/мес\n\nВыбери движок:", keyboard=main_keyboard)
                            continue
                        
                        if chat_id in user_states:
                            engine, _ = user_states.pop(chat_id)
                            if not can_generate(user_id, engine):
                                send_message(chat_id, f"❌ Бесплатный лимит для {engine.upper()} на сегодня закончился.", keyboard=main_keyboard)
                                continue
                            
                            if "text" in msg:
                                prompt = msg["text"]
                                send_message(chat_id, f"⏳ Генерирую через {engine.upper()} по тексту...")
                                try:
                                    if engine == "meshy":
                                        model = meshy_generate_from_text(prompt)
                                    else:
                                        model = hunyuan_generate_from_text(prompt)
                                    if model:
                                        use_generation(user_id, engine)
                                        send_document(chat_id, model, caption=f"{engine.upper()}: {prompt[:100]}")
                                        send_message(chat_id, "✅ Готово!")
                                    else:
                                        send_message(chat_id, "❌ Ошибка: модель не получена.")
                                except Exception as e:
                                    send_message(chat_id, f"❌ Ошибка: {str(e)}")
                            
                            elif "photo" in msg:
                                send_message(chat_id, f"⏳ Генерирую через {engine.upper()} по фото...")
                                try:
                                    if engine == "meshy":
                                        model = meshy_generate_from_photo(image_base64)
                                    else:
                                        model = hunyuan_generate_from_photo(image_base64)
                                    if model:
                                        use_generation(user_id, engine)
                                        send_document(chat_id, model, caption=f"{engine.upper()} (по фото)")
                                        send_message(chat_id, "✅ Готово!")
                                    else:
                                        send_message(chat_id, "❌ Ошибка: модель не получена.")
                                except Exception as e:
                                    send_message(chat_id, f"❌ Ошибка: {str(e)}")
                        else:
                            send_message(chat_id, "Сначала выбери движок.", keyboard=main_keyboard)
            time.sleep(1)
        except Exception as e:
            print(f"Poll error: {e}")
            time.sleep(5)


# ========== FLASK ДЛЯ RENDER ==========
flask_app = Flask(__name__)


@flask_app.route('/')
def home():
    return "Bot running (Meshy + Hunyuan + payments)"


@flask_app.route('/health')
def health():
    return "OK"


if __name__ == "__main__":
    print("🟢 Bot polling started")
    threading.Thread(target=poll, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
