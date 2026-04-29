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
MESHY_API_URL = "https://api.meshy.ai/openapi/v1"
user_states = {}
last_update_id = 0


def send_message(chat_id, text, keyboard=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    try:
        requests.post(API_URL + "/sendMessage", json=data, timeout=10)
    except Exception as e:
        print(f"send_message error: {e}")


def send_document(chat_id, content, caption=""):
    files = {"document": (f"model_{int(time.time())}.glb", content)}
    data = {"chat_id": chat_id, "caption": caption}
    try:
        requests.post(API_URL + "/sendDocument", files=files, data=data, timeout=60)
    except Exception as e:
        print(f"send_document error: {e}")


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
    # ✅ ИСПРАВЛЕНО: правильный полный URL для текста
    response = requests.post(
        "https://api.meshy.ai/openapi/v2/text-to-3d",
        headers=headers,
        json=payload,
        timeout=30
    )
    if response.status_code not in (200, 202):
        raise Exception(f"API error {response.status_code}: {response.text}")
    task_id = response.json().get("result")
    if not task_id:
        raise Exception("No task_id in response")
    return task_id


def create_photo_task(image_url):
    headers = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "image_url": image_url,
        "should_remesh": True
    }
    # ✅ ИСПРАВЛЕНО: правильный полный URL для фото
    response = requests.post(
        "https://api.meshy.ai/openapi/v1/image-to-3d",
        headers=headers,
        json=payload,
        timeout=30
    )
    if response.status_code not in (200, 202):
        raise Exception(f"API error {response.status_code}: {response.text}")
    task_id = response.json().get("result")
    if not task_id:
        raise Exception("No task_id in response")
    return task_id


def wait_for_task(task_id, task_type="text"):
    headers = {"Authorization": f"Bearer {MESHY_API_KEY}"}
    if task_type == "text":
        endpoint = f"https://api.meshy.ai/openapi/v2/text-to-3d/{task_id}"
    else:
        endpoint = f"https://api.meshy.ai/openapi/v1/image-to-3d/{task_id}"
    print(f"🟢 Checking status: {endpoint}")
    start_time = time.time()
    while time.time() - start_time < 300:  # 5 минут
        time.sleep(5)
        response = requests.get(endpoint, headers=headers, timeout=30)
        print(f"🟢 Status response: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            status = data.get("status")
            progress = data.get("progress", 0)
            print(f"🟢 Task status: {status}, progress: {progress}%")
            if status == "SUCCEEDED":
                model_url = data.get("model_urls", {}).get("glb")
                if model_url:
                    model = requests.get(model_url, timeout=60)
                    if model.status_code == 200:
                        return model.content
                    raise Exception("Failed to download model")
                raise Exception("No model URL")
            elif status == "FAILED":
                error_msg = data.get("error_message", "Unknown error")
                raise Exception(f"Generation failed: {error_msg}")
        else:
            print(f"⚠️ Status check HTTP {response.status_code}: {response.text[:100]}")
    raise Exception("Generation timeout (5 minutes)")


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
                            send_message(chat_id, "Привет! Я создаю 3D-модели по тексту и фото!", keyboard=menu_keyboard)
                        elif "text" in msg and user_states.get(chat_id) == "text":
                            del user_states[chat_id]
                            send_message(chat_id, "⏳ Генерирую 3D-модель (1-2 минуты)...")
                            try:
                                task_id = create_text_task(msg["text"])
                                model = wait_for_task(task_id, "text")
                                send_document(chat_id, model, "✅ Готово! Твоя 3D-модель по тексту:")
                            except Exception as e:
                                send_message(chat_id, f"❌ Ошибка: {str(e)}")
                        elif "photo" in msg and user_states.get(chat_id) == "photo":
                            del user_states[chat_id]
                            send_message(chat_id, "⏳ Обрабатываю фото (1-2 минуты)...")
                            try:
                                file_id = msg["photo"][-1]["file_id"]
                                file_info = requests.get(API_URL + f"/getFile?file_id={file_id}").json()
                                if not file_info.get("ok"):
                                    raise Exception("File info error")
                                file_path = file_info["result"]["file_path"]
                                file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                                task_id = create_photo_task(file_url)
                                model = wait_for_task(task_id, "image")
                                send_document(chat_id, model, "✅ Готово! 3D-модель по фото:")
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
    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
