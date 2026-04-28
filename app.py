import requests
import os
from flask import Flask, request

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не задан!")

app = Flask(__name__)
API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

print("🟢 Бот запускается...")

# Установка webhook
def set_webhook():
    webhook_url = "https://threed-bot-824r.onrender.com/webhook"   # ← Если твой URL другой — измени!
    r = requests.post(API_URL + "/setWebhook", json={"url": webhook_url})
    print(f"Webhook установка: статус {r.status_code}")
    print(r.text)

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    print("✅ Получено обновление от Telegram")

    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")

        print(f"Сообщение от пользователя: {text}")

        if text == "/start":
            requests.post(API_URL + "/sendMessage", json={
                "chat_id": chat_id,
                "text": "👋 Бот работает!\n\nОтправь любое сообщение или фото для теста."
            })
        else:
            requests.post(API_URL + "/sendMessage", json={
                "chat_id": chat_id,
                "text": f"Получено: {text}\n\nБот живой ✅"
            })

    return "OK", 200

@app.route('/')
def home():
    return "3D Bot работает (Webhook)"

if __name__ == "__main__":
    set_webhook()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
