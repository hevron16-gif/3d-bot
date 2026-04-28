import requests
import os
from flask import Flask, request


TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")


if not TELEGRAM_BOT_TOKEN or not MESHY_API_KEY:
    raise ValueError("Токены не заданы!")


app = Flask(__name__)
API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# Устанавливаем webhook
def set_webhook():
    webhook_url = "https://threed-bot-824r.onrender.com/webhook"   # ← Проверь, что имя проекта правильное!
    response = requests.post(API_URL + "/setWebhook", json={"url": webhook_url})
    print(f"Webhook установлен: {response.status_code}")
    print(response.text)


print("🟢 Бот запускается...")


@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")


        print(f"Получено сообщение: {text}")


        if text == "/start":
            requests.post(API_URL + "/sendMessage", json={
                "chat_id": chat_id,
                "text": "👋 Бот запущен через Webhook!\n\nТеперь отправь любое сообщение или фото для теста."
            })
        else:
            requests.post(API_URL + "/sendMessage", json={
                "chat_id": chat_id,
                "text": f"Я получил: {text}\n\nБот работает ✅"
            })


    return "OK", 200


@app.route('/')
def home():
    return "🤖 3D Bot работает"


if __name__ == "__main__":
    set_webhook()   # Устанавливаем webhook при запуске
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
