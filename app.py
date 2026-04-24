
import requests
import json
import time
import os
import threading
from datetime import date
from flask import Flask

# ========== ТОКЕНЫ БЕРУТСЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ НА RENDER ==========
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")
# ====================================================================

# Проверка: если переменные не заданы — бот не запустится
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN не задан в переменных окружения!")
if not MESHY_API_KEY:
    raise ValueError("❌ MESHY_API_KEY не задан в переменных окружения!")

MESHY_API_URL = "https://api.meshy.ai/openapi/v2"
app = Flask(__name__)
user_data = {}
last_update_id = 0
USAGE_FILE = "user_usage.json"

print("🟢 Бот запускается...")

# ========== ЛИМИТЫ ПОЛЬЗОВАТЕЛЕЙ ==========

def load_user_usage():
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_user_usage(usage):
    try:
        with open(USAGE_FILE, 'w') as f:
            json.dump(usage, f)
    except:
        pass

def get_user_generations_today(user_id):
    usage = load_user_usage()
    today_str = date.today().isoformat()
    user_key = str(user_id)
    if user_key in usage and usage[user_key].get("date") == today_str:
        return usage[user_key].get("count", 0)
    return 0

def increment_user_generations(user_id):
    usage = load_user_usage()
    today_str = date.today().isoformat()
    user_key = str(user_id)
    if user_key not in usage or usage[user_key].get("date") != today_str:
        usage[user_key] = {"date": today_str, "count": 0}
    usage[user_key]["count"] += 1
    save_user_usage(usage)
    return usage[user_key]["count"]

def can_generate(user_id):
    return get_user_generations_today(user_id) < 3

# ========== ФУНКЦИИ ОТПРАВКИ СООБЩЕНИЙ ==========

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

# ========== МЕНЮ ==========

def show_main_menu(chat_id):
    keyboard = {
        "inline_keyboard": [
            [{"text": "🎲 Генерация по тексту", "callback_data": "generate"}],
            [{"text": "🎨 Генерация по фото", "callback_data": "generate_photo"}],
            [{"text": "📦 Мои модели", "callback_data": "my_models"}],
            [{"text": "💎 Подписка", "callback_data": "subscription"}]
        ]
    }
    send_message(chat_id, "👋 Привет! Я бот для генерации 3D моделей.\n\n🎁 Бесплатно: 3 модели в день\n💎 Премиум: безлимит за 299₽/мес\n\nВыбери, как хочешь создать модель:", reply_markup=keyboard)

# ========== ГЕНЕРАЦИЯ ПО ТЕКСТУ ==========

def create_generation_task_text(prompt):
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
        response = requests.post(f"{MESHY_API_URL}/text-to-3d", headers=headers, json=payload, timeout=30)
        if response.status_code == 200 or response.status_code == 202:
            data = response.json()
            if "result" in data:
                return {"task_id": data["result"]}
        return None
    except Exception as e:
        print(f"Ошибка create_task_text: {e}")
        return None

# ========== ГЕНЕРАЦИЯ ПО ФОТО ==========

def create_generation_task_image(image_url):
    headers = {
        "Authorization": f"Bearer {MESHY_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "mode": "preview",
        "image_url": image_url,
        "should_remesh": True
    }
    try:
        response = requests.post(f"{MESHY_API_URL}/image-to-3d", headers=headers, json=payload, timeout=30)
        if response.status_code == 200 or response.status_code == 202:
            data = response.json()
            if "result" in data:
                return {"task_id": data["result"]}
        return None
    except Exception as e:
        print(f"Ошибка create_task_image: {e}")
        return None

# ========== ОБЩАЯ ФУНКЦИЯ ОЖИДАНИЯ ГЕНЕРАЦИИ ==========

def wait_for_generation(chat_id, task_id, user_id, prompt=""):
    """Ожидает завершения генерации и отправляет модель"""
    max_wait = 180  # 3 минуты
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        time.sleep(5)
        try:
            headers = {"Authorization": f"Bearer {MESHY_API_KEY}"}
            response = requests.get(f"{MESHY_API_URL}/text-to-3d/{task_id}", headers=headers, timeout=30)
            if response.status_code == 200:
                status_data = response.json()
                status = status_data.get("status", "")
                progress = status_data.get("progress", 0)
                print(f"Статус {task_id}: {status}, прогресс: {progress}%")
                
                if status == "SUCCEEDED":
                    model_urls = status_data.get("model_urls", {})
                    model_url = model_urls.get("glb")
                    
                    if model_url:
                        try:
                            model_response = requests.get(model_url, timeout=60)
                            if model_response.status_code == 200:
                                increment_user_generations(user_id)
                                remaining = 3 - get_user_generations_today(user_id)
                                send_document(chat_id, model_response.content, "3d_model.glb", f"✅ Готово! Осталось генераций сегодня: {remaining}/3")
                                return True
                            else:
                                send_message(chat_id, "❌ Не удалось скачать модель")
                        except Exception as e:
                            print(f"Ошибка скачивания: {e}")
                            send_message(chat_id, "❌ Ошибка при скачивании модели")
                    else:
                        send_message(chat_id, "❌ Не удалось получить ссылку на модель")
                    return False
                    
                elif status == "FAILED":
                    send_message(chat_id, "❌ Генерация не удалась. Попробуй другой промт или фото.")
                    return False
        except Exception as e:
            print(f"Ошибка проверки статуса: {e}")
    
    send_message(chat_id, "⏰ Генерация заняла слишком много времени. Попробуй позже.")
    return False

def generate_3d_model_text(chat_id, prompt, user_id):
    if not can_generate(user_id):
        send_message(chat_id, "❌ Лимит на сегодня исчерпан (3 модели). Завтра будут новые!")
        return
    
    if len(prompt.strip()) < 5:
        send_message(chat_id, "📝 Промт слишком короткий. Опиши модель подробнее (минимум 5 символов)")
        return
    
    send_message(chat_id, f"🎲 Начинаю генерацию по тексту...\n⏳ Обычно это занимает 1-3 минуты.\n\n📝 Промт: {prompt[:100]}")
    
    task = create_generation_task_text(prompt)
    if not task or "task_id" not in task:
        send_message(chat_id, "❌ Ошибка создания задачи. Попробуй позже.")
        return
    
    wait_for_generation(chat_id, task["task_id"], user_id, prompt)

def generate_3d_model_image(chat_id, image_url, user_id):
    if not can_generate(user_id):
        send_message(chat_id, "❌ Лимит на сегодня исчерпан (3 модели). Завтра будут новые!")
        return
    
    send_message(chat_id, f"🎨 Начинаю генерацию по фото...\n⏳ Обычно это занимает 1-3 минуты.")
    
    task = create_generation_task_image(image_url)
    if not task or "task_id" not in task:
        send_message(chat_id, "❌ Ошибка создания задачи. Попробуй другое фото.")
        return
    
    wait_for_generation(chat_id, task["task_id"], user_id)

# ========== ОБРАБОТЧИК ОБНОВЛЕНИЙ ==========

def poll_updates():
    global last_update_id
    print("🟢 Прослушивание сообщений запущено...")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 30}
            response = requests.get(url, params=params, timeout=35)
            
            if response.status_code == 200:
                updates = response.json().get("result", [])
                for update in updates:
                    last_update_id = update["update_id"]
                    
                    # Обработка нажатий на кнопки
                    if "callback_query" in update:
                        callback = update["callback_query"]
                        chat_id = callback["message"]["chat"]["id"]
                        data = callback["data"]
                        
                        if data == "generate":
                            user_data[chat_id] = {"awaiting_text": True}
                            send_message(chat_id, "🎲 Введи описание 3D модели.\n\nПример: 'статуэтка дракона, сидящего на скале, стиль фэнтези'")
                        elif data == "generate_photo":
                            user_data[chat_id] = {"awaiting_photo": True}
                            send_message(chat_id, "📸 Отправь мне фото объекта.\n\nЯ создам 3D-модель по твоему фото.\n\nПодойдут фото: игрушек, фигурок, предметов, людей, животных.")
                        elif data == "my_models":
                            send_message(chat_id, "📦 История моделей появится позже. Пока сохраняй модели в Telegram после генерации.")
                        elif data == "subscription":
                            send_message(chat_id, "💎 Премиум подписка — 299₽/мес\n\nДаёт безлимитную генерацию.\n\nСледи за обновлениями — скоро появится возможность оплаты через Telegram Stars!")
                        
                        # Ответ на нажатие кнопки
                        callback_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
                        requests.post(callback_url, json={"callback_query_id": callback["id"]})
                    
                    # Обработка сообщений
                    elif "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        user_id = msg["from"]["id"]
                        
                        # Команды
                        if "text" in msg and msg["text"] == "/start":
                            show_main_menu(chat_id)
                            continue
                        elif "text" in msg and msg["text"] == "/help":
                            send_message(chat_id, "📖 Помощь:\n\n1. Нажми кнопку 'Генерация по тексту' или 'Генерация по фото'\n2. Введи описание или отправь фото\n3. Жди 1-3 минуты\n4. Скачай модель\n\nБесплатно: 3 модели в день")
                            continue
                        
                        # Обработка текста (если ждём текст)
                        if "text" in msg and user_data.get(chat_id, {}).get("awaiting_text"):
                            user_data[chat_id]["awaiting_text"] = False
                            generate_3d_model_text(chat_id, msg["text"], user_id)
                        # Обработка фото (если ждём фото)
                        elif "photo" in msg and user_data.get(chat_id, {}).get("awaiting_photo"):
                            user_data[chat_id]["awaiting_photo"] = False
                            
                            # Получаем самое большое фото
                            photo = msg["photo"][-1]
                            file_id = photo["file_id"]
                            
                            # Получаем URL фото
                            file_info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}").json()
                            if file_info.get("ok"):
                                file_path = file_info["result"]["file_path"]
                                image_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                                generate_3d_model_image(chat_id, image_url, user_id)
                            else:
                                send_message(chat_id, "❌ Не удалось получить фото. Попробуй ещё раз.")
                        else:
                            # Если не ждём ничего — отправляем в меню
                            if "text" in msg and msg["text"]:
                                send_message(chat_id, "Нажми на кнопку в меню, чтобы начать генерацию!")
            
            time.sleep(1)
        except Exception as e:
            print(f"Ошибка poll_updates: {e}")
            time.sleep(5)

# ========== ЗАПУСК FLASK ДЛЯ RENDER ==========

@app.route('/')
def home():
    return "🤖 3D Model Bot is running!"

@app.route('/health')
def health():
    return "OK"

@app.route('/metrics')
def metrics():
    return "OK"

def run_bot():
    poll_updates()

if __name__ == "__main__":
    print("🟢 Запуск бота в потоке...")
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()
    print("🟢 Бот запущен, запускаем Flask...")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
