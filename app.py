import requests
import json
import time
import os
import threading
from flask import Flask

print("🟢 Шаг 1: Начало загрузки модулей...")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")

print(f"🟢 Шаг 2: TELEGRAM_BOT_TOKEN {'установлен' if TELEGRAM_BOT_TOKEN else 'ОТСУТСТВУЕТ'}")
print(f"🟢 Шаг 3: MESHY_API_KEY {'установлен' if MESHY_API_KEY else 'ОТСУТСТВУЕТ'}")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN не задан!")
if not MESHY_API_KEY:
    raise ValueError("❌ MESHY_API_KEY не задан!")

MESHY_API_URL = "https://api.meshy.ai/openapi/v2/text-to-3d"
app = Flask(__name__)
user_data = {}
last_update_id = 0

print("🟢 Шаг 4: Переменные инициализированы")

def send_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Ошибка send_message: {e}")

def send_document(chat_id, file_content, filename, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    files = {"document": (filename, file_content)}
    data = {"chat_id": chat_id, "caption": caption}
    try:
        requests.post(url, files=files, data=data, timeout=30)
    except Exception as e:
        print(f"Ошибка send_document: {e}")

def show_main_menu(chat_id):
    keyboard = {
        "inline_keyboard": [
            [{"text": "🎲 Генерация 3D модели", "callback_data": "generate"}],
            [{"text": "📦 Мои модели", "callback_data": "my_models"}],
            [{"text": "💎 Подписка", "callback_data": "subscription"}]
        ]
    }
    send_message(chat_id, "👋 Привет! Я бот для генерации 3D моделей.\n\n🎁 Бесплатно: 3 модели в день\n💎 Премиум: безлимит за 299₽/мес\n\nНажми кнопку, чтобы начать!", keyboard)

def create_generation_task(prompt):
    headers = {
        "Authorization": f"Bearer {MESHY_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "mode": "preview",
        "prompt": prompt,
        "art_style": "realistic",
        "should_remesh": True
    }
    try:
        response = requests.post(MESHY_API_URL, headers=headers, json=payload, timeout=30)
        if response.status_code == 200 or response.status_code == 202:
            data = response.json()
            if "result" in data:
                return {"task_id": data["result"]}
        return None
    except Exception as e:
        print(f"Ошибка create_task: {e}")
        return None

def check_task_status(task_id):
    headers = {"Authorization": f"Bearer {MESHY_API_KEY}"}
    url = f"{MESHY_API_URL}/{task_id}"
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"Ошибка check_status: {e}")
        return None

def download_model(model_url):
    try:
        response = requests.get(model_url, timeout=60)
        if response.status_code == 200:
            return response.content
        return None
    except Exception as e:
        print(f"Ошибка download: {e}")
        return None

def generate_3d_model(chat_id, prompt, user_id):
    send_message(chat_id, f"🎲 Начинаю генерацию...\n⏳ Обычно это занимает 1-3 минуты.\n\n📝 Промт: {prompt[:100]}")
    
    task = create_generation_task(prompt)
    if not task or "task_id" not in task:
        send_message(chat_id, "❌ Ошибка создания задачи. Попробуй позже.")
        return
    
    task_id = task["task_id"]
    max_wait = 180
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        time.sleep(5)
        status_response = check_task_status(task_id)
        if status_response:
            status = status_response.get("status", "")
            
            if status == "SUCCEEDED":
                model_urls = status_response.get("model_urls", {})
                model_url = model_urls.get("glb")
                
                if model_url:
                    model_content = download_model(model_url)
                    if model_content:
                        send_document(chat_id, model_content, "3d_model.glb", "✅ Готово! Модель сгенерирована.")
                    else:
                        send_message(chat_id, "❌ Не удалось скачать модель")
                else:
                    send_message(chat_id, "❌ Не удалось получить ссылку на модель")
                return
            elif status == "FAILED":
                send_message(chat_id, "❌ Генерация не удалась. Попробуй другой промт.")
                return
    send_message(chat_id, "⏰ Генерация заняла слишком много времени. Попробуй позже.")

def poll_updates():
    global last_update_id
    print("🟢 Шаг 5: poll_updates запущен")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 30}
            response = requests.get(url, params=params, timeout=35)
            
            if response.status_code == 200:
                updates = response.json().get("result", [])
                for update in updates:
                    last_update_id = update["update_id"]
                    
                    if "callback_query" in update:
                        callback = update["callback_query"]
                        chat_id = callback["message"]["chat"]["id"]
                        data = callback["data"]
                        
                        if data == "generate":
                            user_data[chat_id] = {"awaiting_prompt": True}
                            send_message(chat_id, "🎲 Введите описание 3D модели.\n\nПример: 'статуэтка дракона, сидящего на скале, стиль фэнтези'")
                        elif data == "my_models":
                            send_message(chat_id, "📦 История моделей появится позже.")
                        elif data == "subscription":
                            send_message(chat_id, "💎 Премиум подписка — 299₽/мес\n\nФункция оплаты в разработке.")
                        
                        callback_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
                        requests.post(callback_url, json={"callback_query_id": callback["id"]})
                    
                    elif "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        user_id = msg["from"]["id"]
                        
                        if "text" not in msg:
                            continue
                        
                        text = msg["text"]
                        
                        if text == "/start":
                            show_main_menu(chat_id)
                        elif text == "/help":
                            send_message(chat_id, "📖 Помощь:\n\n1. Нажми 'Генерация 3D модели'\n2. Опиши модель\n3. Жди 1-3 минуты\n4. Скачай модель\n\nБесплатно: 3 модели в день")
                        else:
                            if user_data.get(chat_id, {}).get("awaiting_prompt"):
                                user_data[chat_id]["awaiting_prompt"] = False
                                generate_3d_model(chat_id, text, user_id)
                            else:
                                send_message(chat_id, "Нажми кнопку '🎲 Генерация 3D модели' чтобы начать!")
            
            time.sleep(1)
        except Exception as e:
            print(f"Ошибка poll_updates: {e}")
            time.sleep(5)

@app.route('/')
def home():
    return "🤖 Bot is running!"

@app.route('/health')
def health():
    return "OK"

def run_bot():
    poll_updates()

if __name__ == "__main__":
    print("🟢 Шаг 6: Запуск бота...")
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()
    print("🟢 Шаг 7: Бот запущен в потоке")
    port = int(os.environ.get("PORT", 5000))
    print(f"🟢 Шаг 8: Запуск Flask на порту {port}")
    app.run(host="0.0.0.0", port=port)
