
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

# ========== ГЕНЕРАЦИЯ 3D (упрощённая версия) ==========
def generate_3d_model(prompt=None, image_base64=None, image_caption=None):
    headers = {
        "Authorization": f"Bearer {MESHY_API_KEY}",
        "Content-Type": "application/json"
    }

    if image_base64:
        print("[IMAGE] Генерация по фото")
        endpoint = "https://api.meshy.ai/openapi/v1/image-to-3d"
        data_uri = f"data:image/jpeg;base64,{image_base64}"
        payload = {
            "image_url": data_uri,
            "should_remesh": True,
            "should_texture": True
        }
        if image_caption:
            payload["texture_prompt"] = image_caption
    else:
        print(f"[TEXT] Генерация по тексту: {prompt[:80]}...")
        endpoint = "https://api.meshy.ai/openapi/v1/text-to-3d"
        payload = {
            "mode": "preview",
            "prompt": prompt,
            "art_style": "realistic",
            "should_remesh": True
        }

    print(f"[DEBUG] Запрос на: {endpoint}")
    response = requests.post(endpoint, headers=headers, json=payload, timeout=50)
    print(f"[DEBUG] Статус: {response.status_code}")

    if response.status_code not in (200, 202):
        raise Exception(f"Meshy ошибка: {response.status_code} - {response.text[:300]}")

    data = response.json()
    task_id = data.get("result") or data.get("task_id")
    if not task_id:
        raise Exception("Не получили task_id")

    print(f"[DEBUG] Task ID: {task_id}")

    # Простой polling
    start_time = time.time()
    status_endpoint = f"{endpoint}/{task_id}"

    while time.time() - start_time < 420:
        time.sleep(10)
        status_resp = requests.get(status_endpoint, headers=headers, timeout=40)
        if status_resp.status_code == 200:
            sdata = status_resp.json()
            status = sdata.get("status")
            print(f"[STATUS] {status}")
            if status == "SUCCEEDED":
                model_url = (sdata.get("model_urls") or sdata.get("result", {})).get("glb")
                if model_url:
                    model_resp = requests.get(model_url, timeout=120)
                    if model_resp.status_code == 200:
                        print("[SUCCESS] Модель получена!")
                        return model_resp.content
            elif status == "FAILED":
                raise Exception("Генерация провалилась")
    raise Exception("Таймаут генерации")

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
                    "text": "👋 Бот работает!\nОтправь описание модели для генерации.",
                    "reply_markup": {
                        "inline_keyboard": [
                            [{"text": "🎲 По тексту", "callback_data": "gen_text"}],
                            [{"text": "🎨 По фото", "callback_data": "gen_photo"}]
                        ]
                    }
                })
            else:
                requests.post(API_URL + "/sendMessage", json={"chat_id": chat_id, "text": "⏳ Генерирую 3D-модель..."})
                try:
                    model_bytes = generate_3d_model(prompt=text)
                    requests.post(API_URL + "/sendDocument", files={"document": ("model.glb", model_bytes)}, data={"chat_id": chat_id, "caption": "✅ Готово!"})
                except Exception as e:
                    requests.post(API_URL + "/sendMessage", json={"chat_id": chat_id, "text": f"❌ Ошибка: {str(e)[:300]}"})

        elif "photo" in msg:
            requests.post(API_URL + "/sendMessage", json={"chat_id": chat_id, "text": "⏳ Обрабатываю фото..."})

    return "OK", 200

@app.route('/')
def home():
    return "🤖 3D Bot работает"

if __name__ == "__main__":
    set_webhook()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
