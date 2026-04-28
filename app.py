import requests
import json
import time
import os
import base64
from datetime import date
from flask import Flask


TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")


if not TELEGRAM_BOT_TOKEN or not MESHY_API_KEY:
    raise ValueError("Missing environment variables")


app = Flask(__name__)


API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MESHY_API_URL = "https://api.meshy.ai/openapi/v2"
user_states = {}
last_update_id = 0


def send_message(chat_id, text, keyboard=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    requests.post(API_URL + "/sendMessage", json=data)


def send_document(chat_id, content, caption=""):
    files = {"document": (f"model_{int(time.time())}.glb", content)}
    data = {"chat_id": chat_id, "caption": caption}
    requests.post(API_URL + "/sendDocument", files=files, data=data)


menu_keyboard = {
    "inline_keyboard": [
        [{"text": "🎲 Текст → 3D", "callback_data": "gen_text"}],
        [{"text": "🎨 Фото → 3D", "callback_data": "gen_photo"}],
        [{"text": "💎 Premium", "callback_data": "premium"}]
    ]
}


def create_text_task(prompt):
    headers = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "mode": "preview",
        "prompt": prompt,
        "art_style": "realistic",
        "should_remesh": True
    }
    response = requests.post(
        f"{MESHY_API_URL}/text-to-3d",
        headers=headers,
        json=payload,
        timeout=30
    )
    if response.status_code not in (200, 202):
        raise Exception(f"API error: {response.status_code} - {response.text[:100]}")
    return response.json().get("result")


def wait_for_task(task_id, task_type="text"):
    headers = {"Authorization": f"Bearer {MESHY_API_KEY}"}
    endpoint = f"{MESHY_API_URL}/{task_type}-to-3d/{task_id}"
    start_time = time.time()
    while time.time() - start_time < 180:
        time.sleep(5)
        response = requests.get(endpoint, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            status = data.get("status")
            if status == "SUCCEEDED":
                model_url = data.get("model_urls", {}).get("glb")
                if model_url:
                    model = requests.get(model_url, timeout=60)
                    if model.status_code == 200:
                        return model.content
                    raise Exception("Failed to download model")
                raise Exception("No model URL in response")
            elif status == "FAILED":
                raise Exception("Generation failed")
        else:
            print(f"Status check: {response.status_code}")
    raise Exception("Generation timeout")


def create_photo_task(image_base64):
    headers = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "image_base64": image_base64,
        "should_remesh": True
    }
    response = requests.post(
        f"{MESHY_API_URL}/image-to-3d",
        headers=headers,
        json=payload,
        timeout=30
    )
    if response.status_code not in (200, 202):
        raise Exception(f"API error: {response.status_code} - {response.text[:100]}")
    return response.json().get("result")


def poll():
    global last_update_id
    print("🟢 Bot polling started")
    while True:
        try:
            resp = requests.get(API_URL + "/getUpdates", params={"offset": last_update_id + 1, "timeout": 30}, timeout=35)
            if resp.status_code == 200:
                for update in resp.json().get("result", []):
                    last_update_id = update["update_id"]
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        chat_id = cb["message"]["chat"]["id"]
                        data = cb["data"]
                        requests.post(API_URL + "/answerCallbackQuery", json={"callback_query_id": cb["id"]})
                        if data == "menu":
                            send_message(chat_id, "Выбери действие:", keyboard=menu_keyboard)
                        elif data == "premium":
                            send_message(chat_id, "💎 Premium — 299 Telegram Stars\nСкоро появится оплата")
                        elif data == "gen_text":
                            user_states[chat_id] = "text"
                            send_message(chat_id, "📝 Напиши описание модели:")
                        elif data == "gen_photo":
                            user_states[chat_id] = "photo"
                            send_message(chat_id, "📸 Отправь фото:")
                    elif "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        if "text" in msg and msg["text"] == "/start":
                            send_message(chat_id, "Привет! Я создаю 3D-модели!", keyboard=menu_keyboard)
                        elif "text" in msg and user_states.get(chat_id) == "text":
                            del user_states[chat_id]
                            send_message(chat_id, "⏳ Генерирую 3D-модель (1-2 минуты)...")
                            try:
                                task_id = create_text_task(msg["text"])
                                model = wait_for_task(task_id, "text")
                                send_document(chat_id, model, "✅ Готово!")
                            except Exception as e:
                                send_message(chat_id, f"❌ Ошибка: {str(e)}")
                        elif "photo" in msg and user_states.get(chat_id) == "photo":
                            del user_states[chat_id]
                            send_message(chat_id, "⏳ Обрабатываю фото...")
                            try:
                                file_id = msg["photo"][-1]["file_id"]
                                file_info = requests.get(API_URL + f"/getFile?file_id={file_id}").json()
                                file_path = file_info["result"]["file_path"]
                                file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                                img_data = requests.get(file_url).content
                                img_base64 = base64.b64encode(img_data).decode('utf-8')
                                task_id = create_photo_task(img_base64)
                                model = wait_for_task(task_id, "image")
                                send_document(chat_id, model, "✅ Готово!")
                            except Exception as e:
                                send_message(chat_id, f"❌ Ошибка: {str(e)}")
            time.sleep(1)
        except Exception as e:
            print(f"Poll error: {e}")
            time.sleep(5)


@app.route('/')
def home():
    return "Bot is running"


@app.route('/health')
def health():
    return "OK"


if __name__ == "__main__":
    import threading
    threading.Thread(target=poll, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
