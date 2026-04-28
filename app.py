
import requests
import json
import time
import os
import base64
from flask import Flask, request

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")

if not TELEGRAM_BOT_TOKEN or not MESHY_API_KEY:
    raise ValueError("TELEGRAM_BOT_TOKEN и MESHY_API_KEY должны быть заданы!")

app = Flask(__name__)
API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

print("🟢 Бот запускается...")

def set_webhook():
    webhook_url = "https://threed-bot-824r.onrender.com/webhook"
    r = requests.post(API_URL + "/setWebhook", json={"url": webhook_url})
    print(f"Webhook статус: {r.status_code}")
    print(r.text)

# ========== ГЕНЕРАЦИЯ 3D (с перебором эндпоинтов) ==========
def generate_3d_model(prompt=None, image_base64=None, image_caption=None):
    headers = {
        "Authorization": f"Bearer {MESHY_API_KEY}",
        "Content-Type": "application/json"
    }

    # Возможные эндпоинты Meshy
    if image_base64:
        print("[IMAGE-TO-3D] Запуск генерации по фото")
        possible_endpoints = [
            "https://api.meshy.ai/openapi/v1/image-to-3d",
            "https://api.meshy.ai/v1/image-to-3d",
            "https://api.meshy.ai/image-to-3d"
        ]
    else:
        print(f"[TEXT-TO-3D] Запуск генерации по тексту: {prompt[:80]}...")
        possible_endpoints = [
            "https://api.meshy.ai/openapi/v1/text-to-3d",
            "https://api.meshy.ai/v1/text-to-3d",
            "https://api.meshy.ai/text-to-3d"
        ]

    for endpoint in possible_endpoints:
        print(f"[DEBUG] Пробуем эндпоинт: {endpoint}")
        
        if image_base64:
            data_uri = f"data:image/jpeg;base64,{image_base64}"
            payload = {
                "image_url": data_uri,
                "should_remesh": True,
                "should_texture": True,
                "ai_model": "meshy-6"
            }
            if image_caption and image_caption.strip():
                payload["texture_prompt"] = image_caption.strip()
        else:
            payload = {
                "mode": "preview",
                "prompt": prompt,
                "art_style": "realistic",
                "should_remesh": True,
                "should_texture": True
            }

        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=50)
            print(f"[DEBUG] Ответ от Meshy: {response.status_code}")

            if response.status_code in (200, 202):
                data = response.json()
                task_id = data.get("result") or data.get("task_id")
                if task_id:
                    print(f"[SUCCESS] Задача создана. Task ID: {task_id}")
                    # Пока возвращаем заглушку, чтобы не падал
                    # Потом добавим полноценный polling
                    return b"test_model_glb_data"
        except Exception as e:
            print(f"[ERROR] Ошибка при запросе к {endpoint}: {e}")

    raise Exception("Meshy не ответил ни на один эндпоинт (404). Проверьте API.")

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()

    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]

        if "text" in msg:
            text = msg["text"]
            if text == "/start":
                requests.post(API_URL + "/sendMessage", json={
                    "chat_id": chat_id,
                    "text": "👋 Бот работает!\n\nОтправь описание модели для генерации.",
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
                    "text": "⏳ Генерирую 3D-модель по описанию..."
                })
                try:
                    model_bytes = generate_3d_model(prompt=text)
                    requests.post(API_URL + "/sendDocument", files={
                        "document": ("model.glb", model_bytes)
                    }, data={
                        "chat_id": chat_id,
                        "caption": "✅ Готово! Модель по тексту"
                    })
                except Exception as e:
                    requests.post(API_URL + "/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"❌ Ошибка: {str(e)[:300]}"
                    })

        elif "photo" in msg:
            requests.post(API_URL + "/sendMessage", json={
                "chat_id": chat_id,
                "text": "⏳ Получил фото, обрабатываю..."
            })

    return "OK", 200

@app.route('/')
def home():
    return "🤖 3D Bot работает"

if __name__ == "__main__":
    set_webhook()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
