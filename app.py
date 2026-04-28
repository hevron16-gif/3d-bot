import requests
import json
import time
import os
import base64
from datetime import date, timedelta
from flask import Flask
import threading


# ========== ТОКЕНЫ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ НА RENDER ==========
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")
# =================================================================


if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN не задан!")
if not MESHY_API_KEY:
    raise ValueError("❌ MESHY_API_KEY не задан!")


app = Flask(__name__)


API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MESHY_API_URL = "https://api.meshy.ai/openapi/v2"
USAGE_FILE = "user_usage.json"
user_states = {}  # Хранит состояния пользователей (ждём текст или фото)


print("🟢 Бот запускается...")


# ========== РАБОТА С БАЗОЙ ДАННЫХ (ЛИМИТЫ) ==========
def load_usage():
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_usage(usage):
    try:
        with open(USAGE_FILE, 'w') as f:
            json.dump(usage, f)
    except:
        pass


def get_user(user_id):
    usage = load_usage()
    uid = str(user_id)
    today_str = date.today().isoformat()
    
    if uid not in usage:
        return "free", 3
    
    user = usage[uid]
    if user.get("premium_until") and user["premium_until"] >= today_str:
        return "premium", float('inf')
    
    if user.get("date") == today_str:
        return "free", max(0, 3 - user.get("count", 0))
    else:
        return "free", 3


def decrement_limit(user_id):
    usage = load_usage()
    uid = str(user_id)
    today_str = date.today().isoformat()
    
    if uid not in usage or usage[uid].get("date") != today_str:
        usage[uid] = {"date": today_str, "count": 1}
    else:
        usage[uid]["count"] = usage[uid].get("count", 0) + 1
    save_usage(usage)


def set_premium(user_id, days=30):
    usage = load_usage()
    uid = str(user_id)
    until = (date.today() + timedelta(days=days)).isoformat()
    usage[uid] = {"premium_until": until}
    save_usage(usage)


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def send_message(chat_id, text, reply_markup=None):
    """Отправляет текстовое сообщение"""
    url = API_URL + "/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Ошибка send_message: {e}")


def send_document(chat_id, file_content, filename, caption=""):
    """Отправляет файл"""
    url = API_URL + "/sendDocument"
    files = {"document": (filename, file_content)}
    data = {"chat_id": chat_id, "caption": caption}
    try:
        requests.post(url, files=files, data=data, timeout=60)
    except Exception as e:
        print(f"Ошибка send_document: {e}")


def get_main_keyboard():
    """Главное меню с кнопками"""
    return {
        "inline_keyboard": [
            [{"text": "🎲 Генерация по тексту", "callback_data": "gen_text"}],
            [{"text": "🎨 Генерация по фото", "callback_data": "gen_photo"}],
            [{"text": "📦 Мои модели", "callback_data": "history"}],
            [{"text": "💎 Премиум", "callback_data": "premium"}]
        ]
    }


def get_premium_keyboard():
    """Меню премиум-подписки"""
    return {
        "inline_keyboard": [
            [{"text": "💎 Купить Premium (299⭐)", "callback_data": "buy_premium"}],
            [{"text": "🔙 Назад", "callback_data": "menu"}]
        ]
    }


# ========== ОСНОВНАЯ ЛОГИКА ГЕНЕРАЦИИ 3D ==========
def generate_3d_from_text(prompt):
    """Генерация по тексту"""
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
    
    # Создаём задачу
    response = requests.post(f"{MESHY_API_URL}/text-to-3d", headers=headers, json=payload, timeout=30)
    if response.status_code not in (200, 202):
        raise Exception(f"API error: {response.status_code}")
    
    task_id = response.json().get("result")
    if not task_id:
        raise Exception("No task_id")
    
    # Ожидаем завершения
    start_time = time.time()
    while time.time() - start_time < 180:
        time.sleep(5)
        status_response = requests.get(f"{MESHY_API_URL}/text-to-3d/{task_id}", headers=headers, timeout=30)
        if status_response.status_code == 200:
            data = status_response.json()
            if data.get("status") == "SUCCEEDED":
                model_url = data.get("model_urls", {}).get("glb")
                if model_url:
                    model_data = requests.get(model_url, timeout=60)
                    if model_data.status_code == 200:
                        return model_data.content
                raise Exception("Model not found")
            elif data.get("status") == "FAILED":
                raise Exception("Generation failed")
    raise Exception("Timeout")


def generate_3d_from_photo(image_base64):
    """Генерация по фото (самый надёжный способ — через base64)"""
    headers = {
        "Authorization": f"Bearer {MESHY_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "image_base64": image_base64,
        "should_remesh": True
    }
    
    # Создаём задачу
    response = requests.post(f"{MESHY_API_URL}/image-to-3d", headers=headers, json=payload, timeout=30)
    if response.status_code not in (200, 202):
        raise Exception(f"API error: {response.status_code}")
    
    task_id = response.json().get("result")
    if not task_id:
        raise Exception("No task_id")
    
    # Ожидаем завершения
    start_time = time.time()
    while time.time() - start_time < 180:
        time.sleep(5)
        status_response = requests.get(f"{MESHY_API_URL}/image-to-3d/{task_id}", headers=headers, timeout=30)
        if status_response.status_code == 200:
            data = status_response.json()
            if data.get("status") == "SUCCEEDED":
                model_url = data.get("model_urls", {}).get("glb")
                if model_url:
                    model_data = requests.get(model_url, timeout=60)
                    if model_data.status_code == 200:
                        return model_data.content
                raise Exception("Model not found")
            elif data.get("status") == "FAILED":
                raise Exception("Generation failed")
    raise Exception("Timeout")


# ========== ОСНОВНОЙ ЦИКЛ ОБРАБОТКИ СООБЩЕНИЙ ==========
def poll_updates():
    last_update_id = 0
    print("🟢 Прослушивание Telegram запущено...")
    
    while True:
        try:
            url = API_URL + "/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 30}
            response = requests.get(url, params=params, timeout=35)
            
            if response.status_code == 200:
                updates = response.json().get("result", [])
                for update in updates:
                    last_update_id = update["update_id"]
                    
                    # Обработка нажатий на кнопки (CallbackQuery)
                    if "callback_query" in update:
                        callback = update["callback_query"]
                        chat_id = callback["message"]["chat"]["id"]
                        data = callback["data"]
                        
                        # Закрываем уведомление о нажатии
                        requests.post(API_URL + "/answerCallbackQuery", json={"callback_query_id": callback["id"]})
                        
                        if data == "menu":
                            send_message(chat_id, "👋 Выбери действие:", reply_markup=get_main_keyboard())
                        elif data == "premium":
                            send_message(chat_id, "💎 Премиум-подписка — 299 Telegram Stars\n\n✅ Безлимит генераций", reply_markup=get_premium_keyboard())
                        elif data == "buy_premium":
                            send_message(chat_id, "🚀 Оплата через Telegram Stars появится в ближайшее время.")
                        elif data == "history":
                            send_message(chat_id, "📦 История генераций появится в следующей версии.")
                        elif data == "gen_text":
                            user_states[chat_id] = "awaiting_text"
                            send_message(chat_id, "🎲 Напиши описание модели (например: 'готический замок на скале'):")
                        elif data == "gen_photo":
                            user_states[chat_id] = "awaiting_photo"
                            send_message(chat_id, "📸 Отправь фото объекта. Я создам 3D-модель.")
                    
                    # Обработка текстовых сообщений и фото
                    elif "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        user_id = msg["from"]["id"]
                        
                        # Команда /start
                        if "text" in msg and msg["text"] == "/start":
                            send_message(chat_id, "👋 Привет! Я создаю 3D-модели по тексту и фото.\n\n🎁 Бесплатно: 3 модели в день", reply_markup=get_main_keyboard())
                            continue
                        
                        # Генерация по тексту
                        if "text" in msg and user_states.get(chat_id) == "awaiting_text":
                            del user_states[chat_id]
                            tier, left = get_user(user_id)
                            
                            if tier != "premium" and left <= 0:
                                send_message(chat_id, "❌ Бесплатные лимиты закончились. Купи Premium!", reply_markup=get_premium_keyboard())
                                continue
                            
                            send_message(chat_id, "⏳ Генерирую 3D-модель (1-2 минуты)...")
                            try:
                                model = generate_3d_from_text(msg["text"])
                                if tier != "premium":
                                    decrement_limit(user_id)
                                send_document(chat_id, model, "model.glb", "✅ Готово! Твоя 3D-модель по тексту:")
                            except Exception as e:
                                send_message(chat_id, f"❌ Ошибка: {str(e)}")
                        
                        # Генерация по фото (ИСПРАВЛЕНА!)
                        elif "photo" in msg and user_states.get(chat_id) == "awaiting_photo":
                            del user_states[chat_id]
                            tier, left = get_user(user_id)
                            
                            if tier != "premium" and left <= 0:
                                send_message(chat_id, "❌ Бесплатные лимиты закончились. Купи Premium!", reply_markup=get_premium_keyboard())
                                continue
                            
                            send_message(chat_id, "⏳ Обрабатываю фото (1-2 минуты)...")
                            try:
                                # Получаем file_id самого большого фото
                                file_id = msg["photo"][-1]["file_id"]
                                
                                # Получаем путь к файлу
                                file_info = requests.get(API_URL + f"/getFile?file_id={file_id}").json()
                                if not file_info.get("ok"):
                                    raise Exception("Не удалось получить информацию о файле")
                                
                                file_path = file_info["result"]["file_path"]
                                file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                                
                                # Скачиваем файл
                                photo_response = requests.get(file_url)
                                if photo_response.status_code != 200:
                                    raise Exception("Не удалось скачать фото")
                                
                                # Кодируем в base64
                                encoded_image = base64.b64encode(photo_response.content).decode('utf-8')
                                
                                # Запускаем генерацию
                                model = generate_3d_from_photo(encoded_image)
                                
                                if tier != "premium":
                                    decrement_limit(user_id)
                                send_document(chat_id, model, "model.glb", "✅ Готово! 3D-модель по фото:")
                            except Exception as e:
                                send_message(chat_id, f"❌ Ошибка: {str(e)}")
                        
                        # Если пользователь написал что-то не по делу
                        elif "text" in msg:
                            send_message(chat_id, "Используй, пожалуйста, кнопки меню.", reply_markup=get_main_keyboard())
            
            time.sleep(1)
        except Exception as e:
            print(f"Ошибка в poll_updates: {e}")
            time.sleep(5)


# ========== FLASK ДЛЯ RENDER ==========
@app.route('/')
def home():
    return "🤖 3D Model Bot is running!"


@app.route('/health')
def health():
    return "OK"


if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=poll_updates, daemon=True)
    bot_thread.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
