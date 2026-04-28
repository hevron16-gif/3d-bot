
import requests
import json
import time
import os
import base64
from flask import Flask, request

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")

if not TELEGRAM_BOT_TOKEN or not MESHY_API_KEY:
    raise ValueError("Токены не заданы!")

app = Flask(__name__)
API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

print("🟢 Бот запущен через Webhook")

# Установка webhook
def set_webhook():
    webhook_url = "https://threed-bot-824r.onrender.com/webhook"   # ← измени, если URL другой
    r = requests.post(API_URL + "/setWebhook", json={"url": webhook_url})
    print(f"Webhook status: {r.status_code}")
    print(r.text)

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]

        # Текст
        if "text" in msg:
            text = msg["text"]
            print(f"[TEXT] Получено сообщение: {text}")

            if text == "/start":
                requests.post(API_URL + "/sendMessage", json={
                    "chat_id": chat_id,
                    "text": "👋 Бот работает!\n\nИспользуй кнопки ниже или отправь текст/фото.",
                    "reply_markup": {
                        "inline_keyboard": [
                            [{"text": "🎲 Генерация по тексту", "callback_data": "gen_text"}],
                            [{"text": "🎨 Генерация по фото", "callback_data": "gen_photo"}]
                        ]
                    }
                })
            else:
                requests.post(API_URL + "/sendMessage", json={
                    "chat_id": chat_id,
                    "text": "⏳ Генерирую модель по тексту..."
                })
                # Здесь позже добавим вызов generate_3d_model

        # Фото
        elif "photo" in msg:
            print(f"[PHOTO] Получено фото от пользователя {user_id}")
            requests.post(API_URL + "/sendMessage", json={
                "chat_id": chat_id,
                "text": "⏳ Получил фото. Обрабатываю..."
            })
            # Здесь позже добавим обработку фото

    return "OK", 200

@app.route('/')
def home():
    return "🤖 3D Bot работает (Webhook)"

if __name__ == "__main__":
    set_webhook()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
