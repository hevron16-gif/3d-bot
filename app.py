import os
import json
import time
import threading
import requests
import hashlib
import hmac
import base64
from datetime import datetime
from collections import defaultdict
from flask import Flask
from concurrent.futures import ThreadPoolExecutor
from requests.exceptions import Timeout, ConnectionError




print("🟢 [DEBUG] Загрузка app.py...", flush=True)




TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")
ADMIN_CHAT_ID = 5193424909




if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN missing")
if not TENCENT_SECRET_ID or not TENCENT_SECRET_KEY:
    raise ValueError("TENCENT_SECRET_ID and TENCENT_SECRET_KEY required")
if MESHY_API_KEY:
    print("🟢 [DEBUG] Meshy API Key: установлен", flush=True)
else:
    print("⚠️ [DEBUG] Meshy API Key: НЕ УСТАНОВЛЕН", flush=True)




API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
HUNYUAN_HOST = "hunyuan.intl.tencentcloudapi.com"
MESHY_API_URL = "https://api.meshy.ai/v2"




print(f"🟢 [DEBUG] ADMIN_CHAT_ID = {ADMIN_CHAT_ID}", flush=True)




# ========== ПУЛ ПОТОКОВ ==========
executor = ThreadPoolExecutor(max_workers=5)
user_busy = {}
pending_reply = {}
admin_reply_mode = {}
user_service_choice = {}
user_format_choice = {}
user_language = {}
user_rate_limit = defaultdict(list)
USER_REQUESTS_PER_MINUTE = 3
USER_REQUESTS_PER_HOUR = 20




# ========== ФАЙЛЫ ДЛЯ СОХРАНЕНИЯ ПОДПИСОК ==========
SUBSCRIPTIONS_FILE = "subscriptions.json"




def load_subscriptions():
    if os.path.exists(SUBSCRIPTIONS_FILE):
        try:
            with open(SUBSCRIPTIONS_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}




def save_subscriptions(subs):
    try:
        with open(SUBSCRIPTIONS_FILE, 'w') as f:
            json.dump(subs, f)
    except:
        pass




user_subscription = load_subscriptions()




# ========== ПЕРЕВОДЫ ==========
TEXTS = {
    "ru": {
        "start": "👋 Привет! Я бот для генерации 3D-моделей по тексту или фото.\n\n🎁 *Бесплатно:* {remain} из {limit} генераций.\n🎟 *Разовая:* 40⭐\n💎 *Подписка:* 170⭐/мес\n\nНажмите кнопку, чтобы начать:",
        "choose_action": "Выбери действие:",
        "help_text": "❓ *Как пользоваться ботом:*\n\n1️⃣ Нажмите «Генерация по тексту» или «Генерация по фото»\n2️⃣ Выберите способ\n3️⃣ Отправьте описание или фото\n4️⃣ Подождите 1-5 минут — модель готова!\n\n🎁 *Бесплатно:* {limit} модели на первый раз\n🎟 *Разовая генерация:* 40⭐\n💎 *Подписка:* 170⭐/мес (безлимит)\n\nВыберите сервис: Hunyuan, Meshy или VARCO\n📦 *Формат:* STL для печати, GLB для игр",
        "balance": "💰 *Ваш баланс:*\n\n{status}\n⚡ Сервис: {service}\n📦 Формат: {fmt}",
        "premium_active": "💎 Премиум активна",
        "paid_one": "🎟 Есть разовая генерация",
        "free_left": "🎁 Бесплатно осталось: {remaining} из {limit}",
        "generating": "⏳ Генерирую модель ({service}, {fmt})... (1-4 мин)",
        "model_ready": "✅ Модель готова! ({service}, {fmt})\n📝 {prompt}",
        "free_remaining": "\n🎁 Осталось бесплатно: {remaining} из {limit}",
        "no_free": "❌ Бесплатные генерации закончились.",
        "already_generating": "⏳ У вас уже выполняется генерация.",
        "rate_limit": "⏳ {msg}",
        "banned_words": "⚠️ Ваш запрос содержит запрещённые слова.",
        "error_generation": "❌ Ошибка генерации. Попробуйте ещё раз.",
        "error": "❌ Ошибка: {error}",
        "cancelled": "❌ Действие отменено.",
        "send_text": "📝 Отправьте текстовое описание модели.\n⚡ Сервис: {service}\n📦 Формат: {fmt}\n\nПример: «шестерня 61 мм, 73 зуба, высота 15 мм»",
        "send_photo": "📸 Отправьте фото объекта.\n⚡ Сервис: {service}\n📦 Формат: {fmt}\n\n⚠️ Генерация по фото может занять до 4-5 минут.",
        "thanks_message": "📨 Спасибо за сообщение! Я передал его разработчику.",
        "subscription_activated": "✅ Подписка на месяц активирована!",
        "one_time_activated": "✅ Разовая генерация активирована!",
        "service_set": "✅ Выбран сервис: {service}",
        "meshy_unavailable": "⚠️ Meshy временно недоступен.",
        "varco_unavailable": "⚠️ VARCO временно недоступен.",
        "choose_format": "📦 Текущий формат: {fmt}\n\nSTL — для 3D-печати\nGLB — для игр и 3D-редакторов\n\nВыберите формат:",
        "format_set": "✅ Выбран формат: {fmt_name}",
        "lang_set": "✅ Язык изменён на Русский",
        "choose_lang": "🌐 Выберите язык / Choose language:",
        "grant_sub_ok": "✅ Подписка активирована для пользователя {uid}",
        "grant_sub_fail": "❌ Использование: /grant_sub <user_id>",
        "grant_sub_user": "✅ Ваша подписка активирована администратором вручную.",
        "refine_prompt": "📝 Ваш последний запрос: *{prompt}*\n\nОпишите, что нужно изменить:",
        "no_last_prompt": "⚠️ Нет последнего запроса. Сначала сгенерируйте модель.",
        "part_split_start": "🧩 Отправляю модель на разбивку с шипами и пазами...\n\nЭтот процесс занимает 2-4 минуты.",
        "part_split_no_model": "⚠️ Нет последней модели для разбивки. Сначала сгенерируйте модель.",
        "part_split_done": "✅ Разбивка завершена! {count} частей с шипами/пазами.",
        "part_split_error": "❌ Ошибка разбивки: {error}",
        "tolerances_title": "📏 <b>Допуски для 3D-печати</b>\n\nВыберите материал:",
        "tolerances_pla": "📏 <b>PLA</b>\n\n🔄 Подвижные соединения: <b>0.3 мм</b> зазора\n🔒 Неподвижные соединения: <b>0.15 мм</b> зазора\n\n💡 PLA — самый простой в печати, даёт точные допуски, но при нагрузке может деформироваться.",
        "tolerances_petg": "📏 <b>PETG</b>\n\n🔄 Подвижные соединения: <b>0.25 мм</b> зазора\n🔒 Неподвижные соединения: <b>0.12 мм</b> зазора\n\n💡 PETG — прочнее PLA, допуски чуть меньше, но требует хорошей калибровки потока.",
        "tolerances_abs": "📏 <b>ABS</b>\n\n🔄 Подвижные соединения: <b>0.35 мм</b> зазора\n🔒 Неподвижные соединения: <b>0.2 мм</b> зазора\n\n💡 ABS — самый прочный, но даёт усадку при печати, поэтому зазоры больше.",
        "connectors_info": "🔗 <b>Соединители для 3D-моделей</b>\n\nДля добавления соединений (шип-паз, клипсы, защёлки) к вашим моделям рекомендуем:\n\n• <b>Tinkercad</b> — бесплатный, интуитивный, прямо в браузере\n• <b>Blender</b> — профессиональный, бесплатный, Boolean-модификаторы\n• <b>Fusion 360</b> — точные параметрические соединения\n\n💡 Сначала сгенерируйте модель в боте, затем импортируйте STL в один из этих редакторов.",
    },
    "en": {
        "start": "👋 Hello! I'm a bot for generating 3D models from text or photo.\n\n🎁 *Free:* {remain} of {limit} generations.\n🎟 *One-time:* 40⭐\n💎 *Subscription:* 170⭐/month\n\nPress a button to start:",
        "choose_action": "Choose an action:",
        "help_text": "❓ *How to use the bot:*\n\n1️⃣ Press «Text Generation» or «Photo Generation»\n2️⃣ Choose a method\n3️⃣ Send a description or photo\n4️⃣ Wait 1-5 minutes — model is ready!\n\n🎁 *Free:* {limit} models first time\n🎟 *One-time generation:* 40⭐\n💎 *Subscription:* 170⭐/month (unlimited)\n\nChoose service: Hunyuan, Meshy or VARCO\n📦 *Format:* STL for printing, GLB for games",
        "balance": "💰 *Your balance:*\n\n{status}\n⚡ Service: {service}\n📦 Format: {fmt}",
        "premium_active": "💎 Premium active",
        "paid_one": "🎟 One-time generation available",
        "free_left": "🎁 Free left: {remaining} of {limit}",
        "generating": "⏳ Generating model ({service}, {fmt})... (1-4 min)",
        "model_ready": "✅ Model ready! ({service}, {fmt})\n📝 {prompt}",
        "free_remaining": "\n🎁 Free left: {remaining} of {limit}",
        "no_free": "❌ Free generations exhausted.",
        "already_generating": "⏳ You already have a generation in progress.",
        "rate_limit": "⏳ {msg}",
        "banned_words": "⚠️ Your request contains prohibited words.",
        "error_generation": "❌ Generation error. Try again.",
        "error": "❌ Error: {error}",
        "cancelled": "❌ Action cancelled.",
        "send_text": "📝 Send a text description of the model.\n⚡ Service: {service}\n📦 Format: {fmt}\n\nExample: «gear 61 mm, 73 teeth, 15 mm height»",
        "send_photo": "📸 Send a photo of the object.\n⚡ Service: {service}\n📦 Format: {fmt}\n\n⚠️ Photo generation may take up to 4-5 minutes.",
        "thanks_message": "📨 Thank you for your message! I forwarded it to the developer.",
        "subscription_activated": "✅ Monthly subscription activated!",
        "one_time_activated": "✅ One-time generation activated!",
        "service_set": "✅ Service selected: {service}",
        "meshy_unavailable": "⚠️ Meshy is temporarily unavailable.",
        "varco_unavailable": "⚠️ VARCO is temporarily unavailable.",
        "choose_format": "📦 Current format: {fmt}\n\nSTL — for 3D printing\nGLB — for games and editors\n\nChoose format:",
        "format_set": "✅ Format selected: {fmt_name}",
        "lang_set": "✅ Language changed to English",
        "choose_lang": "🌐 Выберите язык / Choose language:",
        "grant_sub_ok": "✅ Subscription activated for user {uid}",
        "grant_sub_fail": "❌ Usage: /grant_sub <user_id>",
        "grant_sub_user": "✅ Your subscription has been manually activated by the admin.",
        "refine_prompt": "📝 Your last prompt: *{prompt}*\n\nDescribe what to change:",
        "no_last_prompt": "⚠️ No last prompt. Generate a model first.",
        "part_split_start": "🧩 Splitting the model into parts with pins and sockets...\n\nThis takes 2-4 minutes.",
        "part_split_no_model": "⚠️ No previous model to split. Generate a model first.",
        "part_split_done": "✅ Split complete! {count} parts with pins/sockets.",
        "part_split_error": "❌ Split error: {error}",
        "tolerances_title": "📏 <b>3D Printing Tolerances</b>\n\nChoose material:",
        "tolerances_pla": "📏 <b>PLA</b>\n\n🔄 Moving joints: <b>0.3 mm</b> gap\n🔒 Fixed joints: <b>0.15 mm</b> gap\n\n💡 PLA — easiest to print, accurate tolerances, but may deform under load.",
        "tolerances_petg": "📏 <b>PETG</b>\n\n🔄 Moving joints: <b>0.25 mm</b> gap\n🔒 Fixed joints: <b>0.12 mm</b> gap\n\n💡 PETG — stronger than PLA, slightly tighter tolerances, requires good flow calibration.",
        "tolerances_abs": "📏 <b>ABS</b>\n\n🔄 Moving joints: <b>0.35 mm</b> gap\n🔒 Fixed joints: <b>0.2 mm</b> gap\n\n💡 ABS — the strongest but shrinks during printing, so gaps are larger.",
        "connectors_info": "🔗 <b>Connectors for 3D Models</b>\n\nTo add connectors (pin-socket, clips, snaps) to your models we recommend:\n\n• <b>Tinkercad</b> — free, intuitive, browser-based\n• <b>Blender</b> — professional, free, Boolean modifiers\n• <b>Fusion 360</b> — precise parametric connections\n\n💡 First generate the model in the bot, then import the STL into one of these editors.",
    }
}




def t(user_id, key, **kwargs):
    lang = user_language.get(user_id, "ru")
    text = TEXTS.get(lang, TEXTS["ru"]).get(key, key)
    if kwargs:
        text = text.format(**kwargs)
    return text




# ========== ФИЛЬТР ==========
BANNED_WORDS = [
    "наркотик", "наркотики", "порно", "секс", "оружие", "бомба", "теракт",
    "убийство", "суицид", "самоубийство", "педофил", "детское порно",
    "экстремизм", "фашизм", "нацизм", "насилие", "жестокость",
    "пистолет", "автомат", "винтовка", "взрывчатка", "граната",
    "naked", "nude", "porn", "sex", "weapon", "bomb", "terrorist",
    "kill", "murder", "suicide", "child", "abuse", "violence",
    "extremism", "nazi", "drug", "cocaine", "heroin",
    "gun", "pistol", "rifle", "grenade", "explosive"
]




def check_content(text):
    if not text:
        return True
    text_lower = text.lower()
    return all(word not in text_lower for word in BANNED_WORDS)




def check_rate_limit(user_id):
    now = time.time()
    user_rate_limit[user_id] = [t for t in user_rate_limit[user_id] if t > now - 3600]
    if len([t for t in user_rate_limit[user_id] if t > now - 60]) >= USER_REQUESTS_PER_MINUTE:
        return False, "Слишком много запросов. Подождите минуту."
    if len(user_rate_limit[user_id]) >= USER_REQUESTS_PER_HOUR:
        return False, "Лимит на час исчерпан."
    user_rate_limit[user_id].append(now)
    return True, ""




def send_alert(message):
    if ADMIN_CHAT_ID:
        try:
            requests.post(API_URL + "/sendMessage", json={
                "chat_id": ADMIN_CHAT_ID,
                "text": f"⚠️ АЛЕРТ БОТА:\n{message}",
                "parse_mode": "HTML"
            }, timeout=5)
        except:
            pass




def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    requests.post(API_URL + "/sendMessage", json=payload, timeout=10)




def send_document(chat_id, file_content, filename="model.stl", caption=""):
    if not file_content:
        send_message(chat_id, "❌ Ошибка: файл пустой, попробуй ещё раз.")
        return
    files = {"document": (filename, file_content)}
    data = {"chat_id": chat_id, "caption": caption}
    requests.post(API_URL + "/sendDocument", files=files, data=data, timeout=60)




def send_invoice(chat_id, title, description, payload, stars_amount):
    url = API_URL + "/sendInvoice"
    prices = [{"label": "Оплата", "amount": stars_amount}]
    data = {
        "chat_id": chat_id,
        "title": title,
        "description": description,
        "payload": payload,
        "provider_token": "",
        "currency": "XTR",
        "prices": prices,
        "start_parameter": "generate_3d"
    }
    requests.post(url, json=data)




def forward_to_admin(user_id, username, first_name, text):
    if not ADMIN_CHAT_ID:
        return
    try:
        name = first_name or ""
        user_tag = f"@{username}" if username else f"ID: {user_id}"
        pending_reply[ADMIN_CHAT_ID] = user_id
        admin_reply_mode[ADMIN_CHAT_ID] = True
        reply_keyboard = {
            "inline_keyboard": [
                [{"text": "💬 Ответить", "callback_data": f"reply_to_{user_id}"}],
                [{"text": "❌ Отмена ответа", "callback_data": "cancel_reply"}]
            ]
        }
        resp = requests.post(API_URL + "/sendMessage", json={
            "chat_id": ADMIN_CHAT_ID,
            "text": f"📩 *Новое сообщение*\n👤 {name} ({user_tag})\n━━━━━━━━━━━━━━━━\n💬 {text}\n━━━━━━━━━━━━━━━━\n\n💡 *Нажмите «Ответить» и напишите сообщение*",
            "parse_mode": "Markdown",
            "reply_markup": json.dumps(reply_keyboard)
        }, timeout=10)
        if resp.status_code != 200:
            requests.post(API_URL + "/sendMessage", json={
                "chat_id": ADMIN_CHAT_ID,
                "text": f"📩 Сообщение от {name} ({user_tag}):\n{text}",
                "reply_markup": json.dumps(reply_keyboard)
            }, timeout=10)
    except Exception as e:
        print(f"❌ Ошибка пересылки: {e}", flush=True)




def reply_to_user(admin_message_text):
    if ADMIN_CHAT_ID in pending_reply:
        user_id = pending_reply[ADMIN_CHAT_ID]
        send_message(user_id, f"📨 *Ответ разработчика:*\n\n{admin_message_text}")
        send_keyboard = {
            "inline_keyboard": [
                [{"text": "💬 Ответить ещё", "callback_data": f"reply_to_{user_id}"}],
                [{"text": "❌ Завершить диалог", "callback_data": "cancel_reply"}]
            ]
        }
        send_message(ADMIN_CHAT_ID, f"✅ Ответ отправлен пользователю {user_id}", keyboard=send_keyboard)
    else:
        send_message(ADMIN_CHAT_ID, "ℹ️ Нет активного диалога.")




# ========== СТАТИСТИКА ==========
STATS_FILE = "stats.json"


def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"total_generations": 0, "total_users": [], "daily_stats": {}, "user_generations": {}}




def save_stats(stats):
    try:
        with open(STATS_FILE, 'w') as f:
            json.dump(stats, f)
    except:
        pass




def update_stats(user_id):
    stats = load_stats()
    stats["total_generations"] += 1
    if user_id not in stats["total_users"]:
        stats["total_users"].append(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    if today not in stats["daily_stats"]:
        stats["daily_stats"][today] = 0
    stats["daily_stats"][today] += 1
    if "user_generations" not in stats:
        stats["user_generations"] = {}
    stats["user_generations"][str(user_id)] = stats["user_generations"].get(str(user_id), 0) + 1
    save_stats(stats)




def send_stats(chat_id):
    stats = load_stats()
    today = datetime.now().strftime("%Y-%m-%d")
    text = f"📊 *Статистика бота*\n\n👥 Всего пользователей: {len(stats['total_users'])}\n🎲 Всего генераций: {stats['total_generations']}\n📅 Генераций сегодня: {stats['daily_stats'].get(today, 0)}\n\n"
    user_gens = stats.get("user_generations", {})
    if user_gens:
        top_users = sorted(user_gens.items(), key=lambda x: x[1], reverse=True)[:10]
        text += "*🏆 Топ пользователей по генерациям:*\n"
        for uid, count in top_users:
            text += f"  ID: `{uid}` — {count} шт.\n"
    send_message(chat_id, text)




# ========== ЛИМИТЫ ==========
FREE_LIMIT = 3
user_free_used = {}
user_paid_one = {}
user_last_prompt = {}
user_last_glb_url = {}  # user_id -> GLB URL последней модели (для PartSplit)




def can_generate(user_id):
    global user_subscription
    if user_subscription.get(str(user_id), 0) > time.time():
        return True, "premium"
    if user_paid_one.get(user_id):
        return True, "paid"
    if user_id == ADMIN_CHAT_ID:
        return True, "free"
    if user_free_used.get(user_id, 0) < FREE_LIMIT:
        return True, "free"
    return False, ""




def use_generation(user_id, generation_type):
    if generation_type == "free" and user_id != ADMIN_CHAT_ID:
        user_free_used[user_id] = user_free_used.get(user_id, 0) + 1
        if user_free_used[user_id] >= FREE_LIMIT:
            send_alert(f"Пользователь {user_id} использовал все {FREE_LIMIT} бесплатные генерации")




# ========== HUNYUAN API ==========
def get_tencent_headers(action, payload):
    service = "hunyuan"
    host = HUNYUAN_HOST
    region = "ap-singapore"
    version = "2023-09-01"
    algorithm = "TC3-HMAC-SHA256"
    timestamp = int(time.time())
    date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
    ct = "application/json"
    canonical_headers = f"content-type:{ct}\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{hashed_payload}"
    credential_scope = f"{date}/{service}/tc3_request"
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashed_canonical}"
    secret_date = hmac.new(("TC3" + TENCENT_SECRET_KEY).encode("utf-8"), date.encode("utf-8"), hashlib.sha256).digest()
    secret_service = hmac.new(secret_date, service.encode("utf-8"), hashlib.sha256).digest()
    secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = f"{algorithm} Credential={TENCENT_SECRET_ID}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
    return {
        "Authorization": authorization, "Content-Type": ct, "Host": host,
        "X-TC-Action": action, "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": version, "X-TC-Region": region
    }




def safe_request(method, url, max_retries=3, **kwargs):
    for attempt in range(max_retries):
        try:
            if method == "post":
                return requests.post(url, **kwargs)
            else:
                return requests.get(url, **kwargs)
        except (Timeout, ConnectionError) as e:
            print(f"⚠️ Сетевая ошибка (попытка {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(15)
                continue
            raise




# ===== ИСПРАВЛЕННАЯ ФУНКЦИЯ ГЕНЕРАЦИИ (STL/GLB) =====
def hunyuan_generate_with_retry(prompt, output_format="stl", max_retries=3, user_id=None):
    """
    Генерация по тексту.
    ВАЖНО: Hunyuan ВСЕГДА возвращает GLB.
    Если нужен STL — конвертируем через Convert3DFormat.
    Если нужен GLB — отдаём как есть.
    Если передан user_id — сохраняем GLB URL для PartSplit.
    """
    for attempt in range(max_retries):
        try:
            print(f"Hunyuan: попытка {attempt+1}, запрошенный формат: {output_format}")
            action = "SubmitHunyuanTo3DProJob"
            payload_dict = {
                "Model": "3.1",
                "Prompt": prompt[:900],
                "EnablePBR": True,
                "GenerateType": "Normal",
                "FaceCount": 1000000
            }
            payload = json.dumps(payload_dict)
            headers = get_tencent_headers(action, payload)
            resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=90)
            data = resp.json()
            
            if "Response" not in data or "JobId" not in data["Response"]:
                raise Exception(f"Hunyuan error: {data}")
            
            job_id = data["Response"]["JobId"]
            action = "QueryHunyuanTo3DProJob"
            
            for _ in range(40):
                time.sleep(5)
                payload = json.dumps({"JobId": job_id})
                headers = get_tencent_headers(action, payload)
                resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=90)
                data = resp.json()
                
                if "Response" in data:
                    status = data["Response"].get("Status")
                    if status == "DONE":
                        result_files = data["Response"].get("ResultFile3Ds", [])
                        if result_files and "Url" in result_files[0]:
                            glb_url = result_files[0]["Url"]


                            # ★ Сохраняем GLB URL для PartSplit ★
                            if user_id:
                                user_last_glb_url[user_id] = glb_url


                            # === ВАЖНО: Всегда скачиваем GLB ===
                            glb_resp = safe_request("get", glb_url, timeout=120)
                            if glb_resp.status_code != 200:
                                raise Exception("Не удалось скачать GLB")
                            glb_data = glb_resp.content
                            
                            # Если пользователь хочет GLB — возвращаем сразу
                            if output_format == "glb":
                                print(f"Hunyuan: возвращаю GLB, размер: {len(glb_data)} байт")
                                return glb_data, "glb"
                            
                            # Если пользователь хочет STL — конвертируем
                            conv_action = "Convert3DFormat"
                            conv_payload = json.dumps({"File3D": glb_url, "Format": "STL"})
                            conv_headers = get_tencent_headers(conv_action, conv_payload)
                            conv_resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=conv_headers, data=conv_payload, timeout=120)
                            conv_data = conv_resp.json()
                            
                            if "Response" in conv_data and "ResultFile3D" in conv_data["Response"]:
                                stl_url = conv_data["Response"]["ResultFile3D"]
                                stl_resp = safe_request("get", stl_url, timeout=120)
                                if stl_resp.status_code == 200:
                                    print(f"Hunyuan: возвращаю STL, размер: {len(stl_resp.content)} байт")
                                    return stl_resp.content, "stl"
                            
                            raise Exception("Не удалось конвертировать в STL")
                    elif status == "FAIL":
                        raise Exception("Generation FAILED")
            
            if attempt < max_retries - 1:
                time.sleep(15)
                continue
            raise Exception("Timeout")
            
        except Exception as e:
            print(f"Hunyuan ошибка в попытке {attempt+1}: {e}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(15)
    
    raise Exception("Hunyuan: не удалось сгенерировать модель")




def hunyuan_generate_from_photo(image_base64, output_format="stl", max_retries=3, user_id=None):
    """Генерация по фото (поддерживает STL и GLB).
    Если передан user_id — сохраняем GLB URL для PartSplit."""
    for attempt in range(max_retries):
        try:
            print(f"Hunyuan фото: попытка {attempt+1}, запрошенный формат: {output_format}")
            action = "SubmitHunyuanTo3DProJob"
            payload_dict = {
                "Model": "3.1",
                "ImageBase64": image_base64,
                "EnablePBR": True,
                "GenerateType": "Normal",
                "FaceCount": 1000000
            }
            payload = json.dumps(payload_dict)
            headers = get_tencent_headers(action, payload)
            resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=90)
            data = resp.json()
            
            if "Response" not in data or "JobId" not in data["Response"]:
                raise Exception(f"Hunyuan error: {data}")
            
            job_id = data["Response"]["JobId"]
            action = "QueryHunyuanTo3DProJob"
            
            for _ in range(40):
                time.sleep(5)
                payload = json.dumps({"JobId": job_id})
                headers = get_tencent_headers(action, payload)
                resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=90)
                data = resp.json()
                
                if "Response" in data:
                    status = data["Response"].get("Status")
                    if status == "DONE":
                        result_files = data["Response"].get("ResultFile3Ds", [])
                        if result_files and "Url" in result_files[0]:
                            glb_url = result_files[0]["Url"]


                            # ★ Сохраняем GLB URL для PartSplit ★
                            if user_id:
                                user_last_glb_url[user_id] = glb_url


                            # Скачиваем GLB
                            glb_resp = safe_request("get", glb_url, timeout=120)
                            if glb_resp.status_code != 200:
                                raise Exception("Не удалось скачать GLB")
                            glb_data = glb_resp.content
                            
                            # Если нужен GLB — возвращаем
                            if output_format == "glb":
                                print(f"Hunyuan фото: возвращаю GLB, размер: {len(glb_data)} байт")
                                return glb_data, "glb"
                            
                            # Конвертируем в STL
                            conv_action = "Convert3DFormat"
                            conv_payload = json.dumps({"File3D": glb_url, "Format": "STL"})
                            conv_headers = get_tencent_headers(conv_action, conv_payload)
                            conv_resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=conv_headers, data=conv_payload, timeout=120)
                            conv_data = conv_resp.json()
                            
                            if "Response" in conv_data and "ResultFile3D" in conv_data["Response"]:
                                stl_url = conv_data["Response"]["ResultFile3D"]
                                stl_resp = safe_request("get", stl_url, timeout=120)
                                if stl_resp.status_code == 200:
                                    print(f"Hunyuan фото: возвращаю STL, размер: {len(stl_resp.content)} байт")
                                    return stl_resp.content, "stl"
                            
                            raise Exception("Не удалось конвертировать в STL")
                    elif status == "FAIL":
                        raise Exception("Generation FAILED")
            
            if attempt < max_retries - 1:
                time.sleep(15)
                continue
            raise Exception("Timeout")
            
        except Exception as e:
            print(f"Hunyuan фото ошибка: {e}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(15)
    
    raise Exception("Hunyuan фото: не удалось сгенерировать модель")




# ========== HUNYUAN 3D PART API ==========
def submit_hunyuan_part_job(fbx_url):
    """Разбивка модели на части с шипами и пазами.
    Принимает FBX URL, возвращает JobId."""
    action = "SubmitHunyuan3DPartJob"
    payload = json.dumps({"File": {"Type": "FBX", "Url": fbx_url}})
    headers = get_tencent_headers(action, payload)
    resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=90)
    data = resp.json()
    if "Response" not in data or "JobId" not in data["Response"]:
        error_msg = data.get("Response", {}).get("Error", {}).get("Message", str(data))
        raise Exception(f"Part submit error: {error_msg}")
    return data["Response"]["JobId"]




def query_hunyuan_part_job(job_id):
    """Проверка статуса разбивки на части."""
    action = "QueryHunyuan3DPartJob"
    payload = json.dumps({"JobId": job_id})
    headers = get_tencent_headers(action, payload)
    resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=90)
    return resp.json()




def hunyuan_part_generate(fbx_url, max_retries=3):
    """Полный цикл: submit -> poll -> download all FBX parts.
    Возвращает список bytes (по одному на каждую часть)."""
    for attempt in range(max_retries):
        try:
            print(f"[Part] Попытка {attempt+1}/{max_retries}")
            job_id = submit_hunyuan_part_job(fbx_url)
            print(f"[Part] JobId: {job_id}")


            for _ in range(30):  # 30 × 5 = 150 сек
                time.sleep(5)
                data = query_hunyuan_part_job(job_id)
                if "Response" not in data:
                    continue
                status = data["Response"].get("Status")


                if status == "DONE":
                    result_files = data["Response"].get("ResultFile3Ds", [])
                    if not result_files:
                        raise Exception("[Part] ResultFile3Ds пуст после DONE")
                    parts = []
                    for f in result_files:
                        url = f.get("Url")
                        if url:
                            r = safe_request("get", url, timeout=120)
                            if r.status_code == 200:
                                parts.append(r.content)
                                print(f"[Part] Скачана часть: {len(r.content)} байт, тип: {f.get('Type', '?')}")
                    if not parts:
                        raise Exception("[Part] Не удалось скачать ни одной части")
                    print(f"[Part] ✅ Успешно: {len(parts)} частей")
                    return parts


                elif status == "FAIL":
                    err = data["Response"].get("ErrorMessage", "неизвестная ошибка")
                    raise Exception(f"[Part] FAILED: {err}")


            raise Exception("[Part] Тайм-аут ожидания (150 сек)")


        except Exception as e:
            print(f"[Part] Ошибка в попытке {attempt+1}: {e}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(15)


    raise Exception("[Part] Все попытки исчерпаны")




# ========== MESHY API ==========
def meshy_generate_with_retry(prompt, output_format="stl", max_retries=2):
    if not MESHY_API_KEY:
        raise Exception("Meshy API ключ не настроен")
    headers = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
    target_formats = [output_format]
    for attempt in range(max_retries):
        try:
            print(f"Meshy: попытка {attempt+1}")
            payload = {
                "mode": "preview", "prompt": prompt[:800], "art_style": "realistic",
                "ai_model": "meshy-6", "topology": "triangle", "target_formats": target_formats
            }
            resp = safe_request("post", f"{MESHY_API_URL}/text-to-3d", headers=headers, json=payload, timeout=60)
            data = resp.json()
            if "result" not in data:
                raise Exception(f"Meshy error: {data}")
            task_id = data["result"]
            for _ in range(40):
                time.sleep(5)
                resp = safe_request("get", f"{MESHY_API_URL}/text-to-3d/{task_id}", headers=headers, timeout=60)
                data = resp.json()
                status = data.get("status")
                if status == "SUCCEEDED":
                    refine_payload = {"mode": "refine", "preview_task_id": task_id, "enable_pbr": True}
                    resp = safe_request("post", f"{MESHY_API_URL}/text-to-3d", headers=headers, json=refine_payload, timeout=60)
                    refine_data = resp.json()
                    if "result" not in refine_data:
                        raise Exception(f"Meshy refine error: {refine_data}")
                    refine_task_id = refine_data["result"]
                    for _ in range(40):
                        time.sleep(5)
                        resp = safe_request("get", f"{MESHY_API_URL}/text-to-3d/{refine_task_id}", headers=headers, timeout=60)
                        refine_data = resp.json()
                        refine_status = refine_data.get("status")
                        if refine_status == "SUCCEEDED":
                            model_url = refine_data.get("model_urls", {}).get(output_format)
                            if not model_url:
                                model_url = refine_data.get("model_urls", {}).get("stl")
                            if not model_url:
                                raise Exception("Meshy: URL модели не найден")
                            model_resp = safe_request("get", model_url, timeout=120)
                            if model_resp.status_code == 200:
                                return model_resp.content, output_format
                            else:
                                raise Exception(f"Meshy: не удалось скачать модель (статус {model_resp.status_code})")
                    raise Exception("Meshy refine: тайм-аут")
                elif status == "FAILED":
                    raise Exception(f"Meshy: генерация не удалась ({data.get('error', 'неизвестная ошибка')})")
            raise Exception("Meshy: тайм-аут ожидания")
        except Exception as e:
            print(f"Meshy ошибка в попытке {attempt+1}: {e}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(15)
    raise Exception("Meshy: не удалось сгенерировать модель")




# ========== ФОНОВАЯ ГЕНЕРАЦИЯ ==========
def process_text_generation(chat_id, user_id, prompt, gen_type):
    service = user_service_choice.get(user_id, "hunyuan")
    output_format = user_format_choice.get(user_id, "stl")
    try:
        model = None
        used_service = service
        if service == "meshy" and MESHY_API_KEY:
            try:
                model, fmt = meshy_generate_with_retry(prompt, output_format)
                used_service = "meshy"
            except Exception as e:
                print(f"Meshy упал, пробуем Hunyuan: {e}")
                try:
                    model, fmt = hunyuan_generate_with_retry(prompt, output_format, user_id=user_id)
                    used_service = "hunyuan"
                except:
                    raise e
        else:
            try:
                model, fmt = hunyuan_generate_with_retry(prompt, output_format, user_id=user_id)
                used_service = "hunyuan"
            except Exception as hunyuan_error:
                if MESHY_API_KEY:
                    model, fmt = meshy_generate_with_retry(prompt, output_format)
                    used_service = "meshy"
                else:
                    raise hunyuan_error
        if model:
            use_generation(user_id, gen_type)
            update_stats(user_id)
            remaining = max(0, FREE_LIMIT - user_free_used.get(user_id, 0))
            filename = f"model.{fmt}"
            caption = t(user_id, "model_ready", service=used_service.upper(), fmt=fmt.upper(), prompt=prompt[:100])
            if gen_type == "free" and user_id != ADMIN_CHAT_ID:
                caption += t(user_id, "free_remaining", remaining=remaining, limit=FREE_LIMIT)
            send_document(chat_id, model, filename=filename, caption=caption)
            user_last_prompt[user_id] = prompt
        else:
            send_message(chat_id, t(user_id, "error_generation"))
    except Exception as e:
        send_message(chat_id, t(user_id, "error", error=str(e)))
        send_alert(f"Ошибка генерации у {user_id}: {str(e)}")
    finally:
        user_busy[user_id] = False




def process_photo_generation(chat_id, user_id, image_base64, gen_type):
    output_format = user_format_choice.get(user_id, "stl")
    try:
        model, fmt = hunyuan_generate_from_photo(image_base64, output_format, user_id=user_id)
        if model:
            use_generation(user_id, gen_type)
            update_stats(user_id)
            caption = t(user_id, "model_ready", service="HUNYUAN", fmt=fmt.upper(), prompt="Фото")
            if gen_type == "free" and user_id != ADMIN_CHAT_ID:
                remaining = max(0, FREE_LIMIT - user_free_used.get(user_id, 0))
                caption += t(user_id, "free_remaining", remaining=remaining, limit=FREE_LIMIT)
            filename = f"model.{fmt}"
            send_document(chat_id, model, filename=filename, caption=caption)
        else:
            send_message(chat_id, t(user_id, "error_generation"))
    except Exception as e:
        send_message(chat_id, t(user_id, "error", error=str(e)))
        send_alert(f"Ошибка генерации у {user_id}: {str(e)}")
    finally:
        user_busy[user_id] = False




# ========== ГЕНЕРАЦИЯ РАЗБИВКИ НА ЧАСТИ ==========
def process_part_generation(chat_id, user_id, glb_url, gen_type):
    """Фоновая разбивка: конвертирует GLB в FBX, затем разбивает на части."""
    try:
        # 1. Конвертируем GLB в FBX через Convert3DFormat
        send_message(chat_id,
            "🧩 Конвертирую модель в FBX для разбивки...\n⏳ Это займёт 1-2 минуты.")
        conv_action = "Convert3DFormat"
        conv_payload = json.dumps({"File3D": glb_url, "Format": "FBX"})
        conv_headers = get_tencent_headers(conv_action, conv_payload)
        conv_resp = safe_request("post", f"https://{HUNYUAN_HOST}",
                                headers=conv_headers, data=conv_payload, timeout=120)
        conv_data = conv_resp.json()


        if "Response" not in conv_data or "ResultFile3D" not in conv_data["Response"]:
            raise Exception("Не удалось конвертировать GLB в FBX. Убедитесь, что последняя модель — Hunyuan.")


        fbx_url = conv_data["Response"]["ResultFile3D"]
        print(f"[Part] FBX URL получен: {fbx_url[:80]}...")


        # 2. Разбиваем на части через Hunyuan3D-Part
        send_message(chat_id,
            "🧩 Разбиваю модель на части с шипами и пазами...\n⏳ Это займёт 2-4 минуты.")
        parts = hunyuan_part_generate(fbx_url)


        # 3. Отправляем каждую часть
        use_generation(user_id, gen_type)
        update_stats(user_id)


        for i, part_data in enumerate(parts):
            caption = f"🧩 Часть {i+1} из {len(parts)}"
            send_document(chat_id, part_data, filename=f"part_{i+1}.fbx", caption=caption)


        send_message(chat_id, t(user_id, "part_split_done", count=len(parts)))


    except Exception as e:
        send_message(chat_id, t(user_id, "part_split_error", error=str(e)))
        send_alert(f"Ошибка Part у {user_id}: {str(e)}")
    finally:
        user_busy[user_id] = False




# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard(user_id):
    fmt = user_format_choice.get(user_id, "stl")
    lang = user_language.get(user_id, "ru")
    lang_label = "🌐 Язык / Language"
    return {
        "inline_keyboard": [
            [{"text": "🔧 Генерация по тексту" if lang == "ru" else "🔧 Text Generation", "callback_data": "gen_text"}],
            [{"text": "🎨 Генерация по фото" if lang == "ru" else "🎨 Photo Generation", "callback_data": "gen_photo"}],
            [{"text": "🧩 Разбить на части" if lang == "ru" else "🧩 Split into Parts", "callback_data": "part_split"}],
            [{"text": "📏 Допуски" if lang == "ru" else "📏 Tolerances", "callback_data": "tolerances"}],
            [{"text": "🔗 Соединители" if lang == "ru" else "🔗 Connectors", "callback_data": "connectors"}],
            [{"text": "🔵 Hunyuan" if lang == "ru" else "🔵 Hunyuan", "callback_data": "use_hunyuan"}],
            [{"text": "🟣 Meshy" if lang == "ru" else "🟣 Meshy", "callback_data": "use_meshy"}],
            [{"text": f"📦 Формат: {fmt.upper()}", "callback_data": "choose_format"}],
            [{"text": lang_label, "callback_data": "choose_lang"}],
            [{"text": "🔧 Уточнить модель" if lang == "ru" else "🔧 Refine Model", "callback_data": "refine_model"}],
            [{"text": "🎟 Разовая генерация (40⭐)" if lang == "ru" else "🎟 One-time (40⭐)", "callback_data": "buy_one"}],
            [{"text": "💎 Подписка (170⭐/мес)" if lang == "ru" else "💎 Subscription (170⭐/mo)", "callback_data": "subscription"}],
            [{"text": "💰 Баланс и лимиты" if lang == "ru" else "💰 Balance & Limits", "callback_data": "my_balance"}],
            [{"text": "❓ Как пользоваться" if lang == "ru" else "❓ How to use", "callback_data": "help_info"}],
        ]
    }




format_keyboard = {
    "inline_keyboard": [
        [{"text": "📦 STL (для 3D-печати)", "callback_data": "set_stl"}],
        [{"text": "📦 GLB (для игр и редакторов)", "callback_data": "set_glb"}],
        [{"text": "🔙 Назад", "callback_data": "menu"}],
    ]
}


lang_keyboard = {
    "inline_keyboard": [
        [{"text": "🇷🇺 Русский", "callback_data": "set_ru"}],
        [{"text": "🇬🇧 English", "callback_data": "set_en"}],
        [{"text": "🔙 Назад / Back", "callback_data": "menu"}],
    ]
}


back_keyboard = {
    "inline_keyboard": [[{"text": "🔙 Главное меню", "callback_data": "menu"}]]
}


cancel_keyboard = {
    "inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel"}]]
}


choice_keyboard = {
    "inline_keyboard": [
        [{"text": "📝 По тексту", "callback_data": "gen_text_confirm"}],
        [{"text": "📸 По фото", "callback_data": "gen_photo_confirm"}],
        [{"text": "🔙 Назад", "callback_data": "menu"}],
    ]
}


tolerances_keyboard = {
    "inline_keyboard": [
        [{"text": "🔵 PLA", "callback_data": "tol_pla"},
         {"text": "🟠 PETG", "callback_data": "tol_petg"},
         {"text": "⚪ ABS", "callback_data": "tol_abs"}],
        [{"text": "🔙 Назад", "callback_data": "menu"}],
    ]
}


user_states = {}




def handle_help_info(chat_id, user_id):
    text = t(user_id, "help_text", limit=FREE_LIMIT)
    send_message(chat_id, text, keyboard=back_keyboard)




def handle_balance(chat_id, user_id):
    used = user_free_used.get(user_id, 0)
    remaining = max(0, FREE_LIMIT - used)
    is_premium = user_subscription.get(str(user_id), 0) > time.time()
    has_paid = user_paid_one.get(user_id, False)
    service = user_service_choice.get(user_id, "hunyuan")
    fmt = user_format_choice.get(user_id, "stl")
    if is_premium:
        status = t(user_id, "premium_active")
    elif has_paid:
        status = t(user_id, "paid_one")
    else:
        status = t(user_id, "free_left", remaining=remaining, limit=FREE_LIMIT)
    text = t(user_id, "balance", status=status, service=service.upper(), fmt=fmt.upper())
    send_message(chat_id, text, keyboard=back_keyboard)




# ========== ОСНОВНОЙ ЦИКЛ ==========
last_update_id = 0




def poll():
    global last_update_id, user_subscription
    print("🟢 Bot polling started", flush=True)
    send_alert("✅ Бот запущен и готов к работе!")
    while True:
        try:
            resp = requests.get(API_URL + "/getUpdates", params={"offset": last_update_id + 1, "timeout": 30}, timeout=35)
            if resp.status_code == 200:
                updates = resp.json().get("result", [])
                if updates:
                    print(f"!!! [DEBUG] Получено {len(updates)} обновлений от Telegram", flush=True)
                for update in updates:
                    last_update_id = update["update_id"]
                    
                    if "pre_checkout_query" in update:
                        pq = update["pre_checkout_query"]
                        pq_id = pq["id"]
                        requests.post(API_URL + "/answerPreCheckoutQuery", json={
                            "pre_checkout_query_id": pq_id,
                            "ok": True
                        })
                        continue
                    
                    if "message" in update and "successful_payment" in update["message"]:
                        user_id = update["message"]["from"]["id"]
                        chat_id = update["message"]["chat"]["id"]
                        payload = update["message"]["successful_payment"]["invoice_payload"]
                        if payload == "single_generation":
                            user_paid_one[user_id] = True
                            send_message(chat_id, t(user_id, "one_time_activated"))
                            send_alert(f"💰 Пользователь {user_id} купил разовую генерацию")
                        elif payload == "monthly_subscription":
                            user_subscription[str(user_id)] = time.time() + 30 * 86400
                            save_subscriptions(user_subscription)
                            send_message(chat_id, t(user_id, "subscription_activated"))
                            send_alert(f"💰 Пользователь {user_id} купил подписку на месяц")
                        continue
                    
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        chat_id = cb["message"]["chat"]["id"]
                        user_id = cb["from"]["id"]
                        data = cb["data"]
                        requests.post(API_URL + "/answerCallbackQuery", json={"callback_query_id": cb["id"]})
                        
                        if data == "menu":
                            admin_reply_mode.pop(chat_id, None)
                            user_states.pop(chat_id, None)
                            send_message(chat_id, t(user_id, "choose_action"), keyboard=get_main_keyboard(user_id))
                        elif data == "help_info":
                            handle_help_info(chat_id, user_id)
                        elif data == "my_balance":
                            handle_balance(chat_id, user_id)
                        elif data == "use_hunyuan":
                            user_service_choice[user_id] = "hunyuan"
                            send_message(chat_id, t(user_id, "service_set", service="Hunyuan"), keyboard=get_main_keyboard(user_id))
                        elif data == "use_meshy":
                            if MESHY_API_KEY:
                                user_service_choice[user_id] = "meshy"
                                send_message(chat_id, t(user_id, "service_set", service="Meshy"), keyboard=get_main_keyboard(user_id))
                            else:
                                send_message(chat_id, t(user_id, "meshy_unavailable"), keyboard=get_main_keyboard(user_id))
                        elif data == "choose_format":
                            current_fmt = user_format_choice.get(user_id, "stl")
                            send_message(chat_id, t(user_id, "choose_format", fmt=current_fmt.upper()), keyboard=format_keyboard)
                        elif data == "set_stl":
                            user_format_choice[user_id] = "stl"
                            send_message(chat_id, t(user_id, "format_set", fmt_name="STL (для 3D-печати)"), keyboard=get_main_keyboard(user_id))
                        elif data == "set_glb":
                            user_format_choice[user_id] = "glb"
                            send_message(chat_id, t(user_id, "format_set", fmt_name="GLB (для игр и редакторов)"), keyboard=get_main_keyboard(user_id))
                        elif data == "choose_lang":
                            send_message(chat_id, t(user_id, "choose_lang"), keyboard=lang_keyboard)
                        elif data == "set_ru":
                            user_language[user_id] = "ru"
                            send_message(chat_id, t(user_id, "lang_set"), keyboard=get_main_keyboard(user_id))
                        elif data == "set_en":
                            user_language[user_id] = "en"
                            send_message(chat_id, t(user_id, "lang_set"), keyboard=get_main_keyboard(user_id))
                        elif data == "refine_model":
                            if user_id in user_last_prompt:
                                user_states[chat_id] = "awaiting_refine"
                                send_message(chat_id, t(user_id, "refine_prompt", prompt=user_last_prompt[user_id][:100]), keyboard=cancel_keyboard)
                            else:
                                send_message(chat_id, t(user_id, "no_last_prompt"), keyboard=get_main_keyboard(user_id))
                        elif data == "buy_one":
                            send_invoice(chat_id, "Разовая генерация 3D-модели", "Одна генерация без подписки", "single_generation", 40)
                        elif data == "subscription":
                            send_invoice(chat_id, "Premium подписка", "Безлимит на месяц", "monthly_subscription", 170)
                        elif data == "gen_text":
                            send_message(chat_id, "🔧 Выберите способ генерации:", keyboard=choice_keyboard)
                        elif data == "gen_photo":
                            send_message(chat_id, "🔧 Выберите способ генерации:", keyboard=choice_keyboard)
                        elif data == "gen_text_confirm":
                            user_states[chat_id] = "awaiting_text"
                            service = user_service_choice.get(user_id, "hunyuan")
                            fmt = user_format_choice.get(user_id, "stl")
                            send_message(chat_id, t(user_id, "send_text", service=service.upper(), fmt=fmt.upper()), keyboard=cancel_keyboard)
                        elif data == "gen_photo_confirm":
                            user_states[chat_id] = "awaiting_photo"
                            service = user_service_choice.get(user_id, "hunyuan")
                            fmt = user_format_choice.get(user_id, "stl")
                            send_message(chat_id, t(user_id, "send_photo", service=service.upper(), fmt=fmt.upper()), keyboard=cancel_keyboard)
                        elif data == "cancel":
                            if chat_id in user_states:
                                del user_states[chat_id]
                            send_message(chat_id, t(user_id, "cancelled"), keyboard=get_main_keyboard(user_id))
                        # ★ НОВЫЕ ФИЧИ ★
                        elif data == "part_split":
                            glb_url = user_last_glb_url.get(user_id)
                            if not glb_url:
                                send_message(chat_id, t(user_id, "part_split_no_model"), keyboard=get_main_keyboard(user_id))
                                continue
                            rate_ok, rate_msg = check_rate_limit(user_id)
                            if not rate_ok:
                                send_message(chat_id, t(user_id, "rate_limit", msg=rate_msg), keyboard=get_main_keyboard(user_id))
                                continue
                            if user_busy.get(user_id, False):
                                send_message(chat_id, t(user_id, "already_generating"), keyboard=get_main_keyboard(user_id))
                                continue
                            can, gen_type = can_generate(user_id)
                            if not can:
                                send_message(chat_id, t(user_id, "no_free"), keyboard=get_main_keyboard(user_id))
                                continue
                            user_busy[user_id] = True
                            send_message(chat_id, t(user_id, "part_split_start"))
                            executor.submit(process_part_generation, chat_id, user_id, glb_url, gen_type)
                        elif data == "tolerances":
                            send_message(chat_id, t(user_id, "tolerances_title"), keyboard=tolerances_keyboard)
                        elif data == "tol_pla":
                            send_message(chat_id, t(user_id, "tolerances_pla"), keyboard=get_main_keyboard(user_id))
                        elif data == "tol_petg":
                            send_message(chat_id, t(user_id, "tolerances_petg"), keyboard=get_main_keyboard(user_id))
                        elif data == "tol_abs":
                            send_message(chat_id, t(user_id, "tolerances_abs"), keyboard=get_main_keyboard(user_id))
                        elif data == "connectors":
                            send_message(chat_id, t(user_id, "connectors_info"), keyboard=get_main_keyboard(user_id))
                        elif data.startswith("reply_to_"):
                            target_user_id = int(data.replace("reply_to_", ""))
                            pending_reply[chat_id] = target_user_id
                            admin_reply_mode[chat_id] = True
                            requests.post(API_URL + "/sendMessage", json={
                                "chat_id": chat_id,
                                "text": f"✏️ Теперь напишите сообщение для пользователя {target_user_id}.\nНажмите /cancelreply для отмены."
                            })
                        elif data == "cancel_reply":
                            pending_reply.pop(chat_id, None)
                            admin_reply_mode.pop(chat_id, None)
                            send_message(chat_id, "❌ Режим ответа отменён.", keyboard=get_main_keyboard(user_id))
                        continue
                    
                    if "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        user_id = msg["from"]["id"]
                        
                        if "text" in msg:
                            print(f"!!! [DEBUG] Сообщение: user_id={user_id}, chat_id={chat_id}, текст={msg['text'][:50]}", flush=True)
                        
                        if "text" in msg and msg["text"] == "/start":
                            used = user_free_used.get(user_id, 0)
                            remain = max(0, FREE_LIMIT - used)
                            send_message(chat_id, t(user_id, "start", remain=remain, limit=FREE_LIMIT), keyboard=get_main_keyboard(user_id))
                            continue
                        
                        if "text" in msg and msg["text"] == "/stats" and user_id == ADMIN_CHAT_ID:
                            send_stats(chat_id)
                            continue
                        
                        if "text" in msg and msg["text"] == "/cancelreply" and user_id == ADMIN_CHAT_ID:
                            pending_reply.pop(chat_id, None)
                            admin_reply_mode.pop(chat_id, None)
                            send_message(chat_id, "❌ Режим ответа отменён.")
                            continue
                        
                        if "text" in msg and msg["text"].startswith("/grant_sub") and user_id == ADMIN_CHAT_ID:
                            try:
                                parts = msg["text"].split()
                                target_user_id = int(parts[1])
                                user_subscription[str(target_user_id)] = time.time() + 30 * 86400
                                save_subscriptions(user_subscription)
                                send_message(chat_id, t(user_id, "grant_sub_ok", uid=target_user_id))
                                send_message(target_user_id, t(target_user_id, "grant_sub_user"))
                            except:
                                send_message(chat_id, t(user_id, "grant_sub_fail"))
                            continue
                        
                        if user_id == ADMIN_CHAT_ID and admin_reply_mode.get(chat_id, False):
                            if "text" in msg and msg["text"] and not msg["text"].startswith('/'):
                                reply_to_user(msg["text"])
                            continue
                        
                        if chat_id in user_states:
                            state = user_states[chat_id]
                            
                            if state == "awaiting_refine" and "text" in msg:
                                refine_text = msg["text"]
                                del user_states[chat_id]
                                original_prompt = user_last_prompt.get(user_id, "")
                                new_prompt = f"{original_prompt}, {refine_text}"
                                user_last_prompt[user_id] = new_prompt
                                if not check_content(new_prompt):
                                    send_message(chat_id, t(user_id, "banned_words"), keyboard=get_main_keyboard(user_id))
                                    continue
                                rate_ok, rate_msg = check_rate_limit(user_id)
                                if not rate_ok:
                                    send_message(chat_id, t(user_id, "rate_limit", msg=rate_msg), keyboard=get_main_keyboard(user_id))
                                    continue
                                if user_busy.get(user_id, False):
                                    send_message(chat_id, t(user_id, "already_generating"), keyboard=get_main_keyboard(user_id))
                                    continue
                                can, gen_type = can_generate(user_id)
                                if not can:
                                    send_message(chat_id, t(user_id, "no_free"), keyboard=get_main_keyboard(user_id))
                                    continue
                                user_busy[user_id] = True
                                service = user_service_choice.get(user_id, "hunyuan")
                                fmt = user_format_choice.get(user_id, "stl")
                                send_message(chat_id, t(user_id, "generating", service=service.upper(), fmt=fmt.upper()))
                                executor.submit(process_text_generation, chat_id, user_id, new_prompt, gen_type)
                                continue
                            
                            if state == "awaiting_text" and "text" in msg:
                                prompt = msg["text"]
                                if prompt.startswith('/'):
                                    continue
                                if not check_content(prompt):
                                    send_message(chat_id, t(user_id, "banned_words"), keyboard=get_main_keyboard(user_id))
                                    continue
                                del user_states[chat_id]
                                rate_ok, rate_msg = check_rate_limit(user_id)
                                if not rate_ok:
                                    send_message(chat_id, t(user_id, "rate_limit", msg=rate_msg), keyboard=get_main_keyboard(user_id))
                                    continue
                                if user_busy.get(user_id, False):
                                    send_message(chat_id, t(user_id, "already_generating"), keyboard=get_main_keyboard(user_id))
                                    continue
                                can, gen_type = can_generate(user_id)
                                if not can:
                                    send_message(chat_id, t(user_id, "no_free"), keyboard=get_main_keyboard(user_id))
                                    continue
                                user_busy[user_id] = True
                                service = user_service_choice.get(user_id, "hunyuan")
                                fmt = user_format_choice.get(user_id, "stl")
                                send_message(chat_id, t(user_id, "generating", service=service.upper(), fmt=fmt.upper()))
                                executor.submit(process_text_generation, chat_id, user_id, prompt, gen_type)
                                continue
                            
                            elif state == "awaiting_photo" and "photo" in msg:
                                del user_states[chat_id]
                                rate_ok, rate_msg = check_rate_limit(user_id)
                                if not rate_ok:
                                    send_message(chat_id, t(user_id, "rate_limit", msg=rate_msg), keyboard=get_main_keyboard(user_id))
                                    continue
                                if user_busy.get(user_id, False):
                                    send_message(chat_id, t(user_id, "already_generating"), keyboard=get_main_keyboard(user_id))
                                    continue
                                can, gen_type = can_generate(user_id)
                                if not can:
                                    send_message(chat_id, t(user_id, "no_free"), keyboard=get_main_keyboard(user_id))
                                    continue
                                user_busy[user_id] = True
                                fmt = user_format_choice.get(user_id, "stl")
                                send_message(chat_id, t(user_id, "generating", service="Hunyuan", fmt=fmt.upper()))
                                def photo_task(chat_id, user_id, gen_type, msg):
                                    try:
                                        file_id = msg["photo"][-1]["file_id"]
                                        file_info = requests.get(API_URL + f"/getFile?file_id={file_id}").json()
                                        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_info['result']['file_path']}"
                                        photo_data = requests.get(file_url).content
                                        image_base64 = base64.b64encode(photo_data).decode('utf-8')
                                        process_photo_generation(chat_id, user_id, image_base64, gen_type)
                                    except Exception as e:
                                        send_message(chat_id, f"❌ Ошибка загрузки фото: {str(e)}")
                                        user_busy[user_id] = False
                                executor.submit(photo_task, chat_id, user_id, gen_type, msg)
                                continue
                            
                            elif "text" in msg and not msg["text"].startswith('/'):
                                if state == "awaiting_text":
                                    send_message(chat_id, "📝 Отправьте именно текстовое описание модели.", keyboard=cancel_keyboard)
                                elif state == "awaiting_photo":
                                    send_message(chat_id, "📸 Отправьте именно фото объекта.", keyboard=cancel_keyboard)
                                continue
                        
                        if "text" in msg and msg["text"] and not msg["text"].startswith('/') and user_id != ADMIN_CHAT_ID:
                            username = msg["from"].get("username", None)
                            first_name = msg["from"].get("first_name", "")
                            forward_to_admin(user_id, username, first_name, msg["text"])
                            send_message(chat_id, t(user_id, "thanks_message"))
                            continue
                        
            time.sleep(1)
        except Exception as e:
            print(f"Poll error: {e}", flush=True)
            time.sleep(5)




flask_app = Flask(__name__)




@flask_app.route('/')
def home():
    return "Bot running"




@flask_app.route('/health')
def health():
    return "OK"




if __name__ == "__main__":
    print("🟢 Bot polling started", flush=True)
    threading.Thread(target=poll, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
