import os
import logging
import time
import threading
import asyncio
import requests
from flask import Flask


# Импорты для Telegram (синхронный режим)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes


# Импорты для Tencent Cloud
from tencentcloud.common import credential
from tencentcloud.hunyuan.v20230901 import hunyuan_client, models


# ========== ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY")


if not TELEGRAM_BOT_TOKEN or not MESHY_API_KEY or not TENCENT_SECRET_ID or not TENCENT_SECRET_KEY:
    raise ValueError("Missing environment variables!")


print("✅ Все переменные окружения загружены")


# ========== НАСТРОЙКИ КЛИЕНТОВ ==========
MESHY_API_URL = "https://api.meshy.ai/openapi/v1"
API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


cred = credential.Credential(TENCENT_SECRET_ID, TENCENT_SECRET_KEY)
hunyuan_client_instance = hunyuan_client.HunyuanClient(cred, "ap-singapore")


# ========== ЛИМИТЫ (ВРЕМЕННАЯ ЗАГЛУШКА) ==========
user_limits = {}


def get_user_limit(user_id: int) -> int:
    today = int(time.time() // 86400)
    if user_id not in user_limits or user_limits[user_id][0] != today:
        user_limits[user_id] = (today, 3)
        return 3
    return max(0, 3 - user_limits[user_id][1])


def decrement_limit(user_id: int):
    today = int(time.time() // 86400)
    if user_id not in user_limits or user_limits[user_id][0] != today:
        user_limits[user_id] = (today, 1)
    else:
        user_limits[user_id] = (today, user_limits[user_id][1] + 1)


# ========== КЛАВИАТУРЫ ==========
def main_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎲 Meshy (Быстрая)", callback_data="gen_meshy")],
        [InlineKeyboardButton("🔧 Hunyuan (Точная)", callback_data="gen_hunyuan")],
        [InlineKeyboardButton("📦 Мои модели", callback_data="my_models")],
        [InlineKeyboardButton("💎 Подписка", callback_data="subscription")],
    ]
    return InlineKeyboardMarkup(keyboard)


def sub_keyboard():
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="menu")]]
    return InlineKeyboardMarkup(keyboard)


# ========== ФУНКЦИЯ ОТПРАВКИ ФАЙЛА (СИНХРОННАЯ) ==========
def send_document(chat_id, file_content, caption=""):
    url = API_URL + "/sendDocument"
    files = {"document": ("model.glb", file_content)}
    data = {"chat_id": chat_id, "caption": caption}
    requests.post(url, files=files, data=data)


# ========== MESHY: ТЕКСТ → 3D ==========
def meshy_text_to_3d(prompt: str):
    headers = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "mode": "preview",
        "prompt": prompt,
        "art_style": "realistic",
        "should_remesh": True
    }
    response = requests.post(f"{MESHY_API_URL}/text-to-3d", headers=headers, json=payload, timeout=30)
    if response.status_code not in (200, 202):
        raise Exception(f"Meshy error: {response.status_code} - {response.text}")
    task_id = response.json().get("result")
    if not task_id:
        raise Exception("No task_id from Meshy")
    
    while True:
        time.sleep(5)
        r = requests.get(f"{MESHY_API_URL}/text-to-3d/{task_id}", headers=headers)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "SUCCEEDED":
                model_url = data.get("model_urls", {}).get("glb")
                if model_url:
                    model = requests.get(model_url).content
                    return model
            elif data.get("status") == "FAILED":
                raise Exception("Meshy generation failed")


# ========== HUNYUAN: ТЕКСТ → 3D ==========
def hunyuan_text_to_3d(prompt: str) -> str:
    req = models.SubmitHunyuanTo3DProJobRequest()
    req.Prompt = prompt
    req.ResultFormat = "glb"
    resp = hunyuan_client_instance.SubmitHunyuanTo3DProJob(req)
    job_id = resp.JobId
    req_query = models.QueryHunyuanTo3DProJobRequest()
    req_query.JobId = job_id
    for _ in range(40):
        time.sleep(3)
        resp_query = hunyuan_client_instance.QueryHunyuanTo3DProJob(req_query)
        if resp_query.Status == "SUCCESS":
            if hasattr(resp_query, 'ModelUrl') and resp_query.ModelUrl:
                return resp_query.ModelUrl
            elif hasattr(resp_query, 'ModelUrls') and resp_query.ModelUrls and hasattr(resp_query.ModelUrls, 'Glb'):
                return resp_query.ModelUrls.Glb
            else:
                raise Exception("Model URL not found")
        elif resp_query.Status == "FAILED":
            raise Exception("Hunyuan generation failed")
    raise Exception("Hunyuan timeout")


# ========== ОБРАБОТЧИКИ TELEGRAM ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для генерации 3D-моделей.\n\n"
        "🎲 Meshy (Быстрая) — для фигурок и фото → 3D\n"
        "🔧 Hunyuan (Точная) — для инженерных деталей и STL\n\n"
        "Бесплатно: 3 генерации в день\n"
        "Выбери движок в меню:",
        reply_markup=main_keyboard()
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id


    if data == "menu":
        await query.edit_message_text("Выбери движок:", reply_markup=main_keyboard())
        return
    if data == "my_models":
        await query.edit_message_text("📦 История генераций появится позже.")
        return
    if data == "subscription":
        await query.edit_message_text(
            "💎 Премиум-подписка — 299₽/мес\n\n"
            "Что даёт:\n"
            "✅ Безлимит генераций\n"
            "✅ Приоритетную обработку\n"
            "✅ Скачивание в STL\n\n"
            "Оплата через Telegram Stars — скоро!",
            reply_markup=sub_keyboard()
        )
        return


    engine = "meshy" if data == "gen_meshy" else "hunyuan"
    remaining = get_user_limit(user_id)
    if remaining <= 0:
        await query.edit_message_text("❌ Лимит исчерпан. Купи Premium!", reply_markup=sub_keyboard())
        return


    await query.edit_message_text(f"🔧 Выбран движок: {'🎲 Meshy' if engine == 'meshy' else '🔧 Hunyuan'}. Осталось попыток: {remaining}. Напиши промт:")
    context.user_data["engine"] = engine
    context.user_data["awaiting_prompt"] = True


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_prompt"):
        return
    context.user_data["awaiting_prompt"] = False
    engine = context.user_data.get("engine")
    prompt = update.message.text
    user_id = update.effective_user.id


    await update.message.reply_text(f"⏳ Генерирую через {engine.upper()}... (1-3 минуты)")


    try:
        if engine == "meshy":
            model_bytes = meshy_text_to_3d(prompt)
            send_document(update.message.chat_id, model_bytes, caption=f"Движок: {engine.upper()}, промт: {prompt[:100]}")
        else:
            model_url = hunyuan_text_to_3d(prompt)
            model_bytes = requests.get(model_url).content
            send_document(update.message.chat_id, model_bytes, caption=f"Движок: {engine.upper()}, промт: {prompt[:100]}")
        decrement_limit(user_id)
        await update.message.reply_text("✅ Готово! Модель выше.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")


def run_bot():
    """Запуск Telegram-бота с правильным event loop"""
    # Создаём новый event loop для этого потока
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запускаем бота в этом event loop
    loop.run_until_complete(app.initialize())
    loop.run_until_complete(app.start())
    loop.run_until_complete(app.updater.start_polling())
    
    print("Бот запущен и готов к работе!")
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(app.stop())
        loop.close()


# ========== FLASK ДЛЯ RENDER ==========
flask_app = Flask(__name__)


@flask_app.route('/')
def home():
    return "Bot is running (Hunyuan + Meshy)"


@flask_app.route('/health')
def health():
    return "OK"


if __name__ == "__main__":
    # Запускаем Telegram-бота в отдельном потоке (syncio)
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Flask для health check
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
