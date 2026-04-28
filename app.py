
import requests
from flask import Flask, request

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")

app = Flask(__name__)
API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

print("🟢 Бот запускается...")

def set_webhook():
    webhook_url = "https://threed-bot-824r.onrender.com/webhook"
    r = requests.post(API_URL + "/setWebhook", json={"url": webhook_url})
    print(f"Webhook статус: {r.status_code}")

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()

    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "Фото")

        print(f"Получено сообщение: {text}")

        if text == "/start":
            requests.post(API_URL + "/sendMessage", json={
                "chat_id": chat_id,
                "text": "👋 Диагностика бота.\nОтправь любое слово для теста Meshy."
            })
        else:
            requests.post(API_URL + "/sendMessage", json={"chat_id": chat_id, "text": "⏳ Проверяю соединение с Meshy..."})
            
            try:
                headers = {
                    "Authorization": f"Bearer {MESHY_API_KEY}",
                    "Content-Type": "application/json"
                }
                # Простой тестовый запрос
                test_payload = {"prompt": text}
                response = requests.post("https://api.meshy.ai/openapi/v1/text-to-3d", 
                                       headers=headers, 
                                       json=test_payload, 
                                       timeout=30)
                
                result_text = f"Статус Meshy: {response.status_code}\nОтвет: {response.text[:500]}"
                requests.post(API_URL + "/sendMessage", json={"chat_id": chat_id, "text": result_text})
            except Exception as e:
                requests.post(API_URL + "/sendMessage", json={"chat_id": chat_id, "text": f"Ошибка соединения: {str(e)}"})

    return "OK", 200

@app.route('/')
def home():
    return "3D Bot - Диагностика Meshy"

if __name__ == "__main__":
    set_webhook()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
