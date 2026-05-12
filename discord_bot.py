import discord
import os
import requests
import hashlib
import hmac
import json
import base64
import asyncio
from datetime import datetime
from flask import Flask
import threading


print("🟢 [DISCORD] Бот запускается...")


DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY")


if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN missing")
if not TENCENT_SECRET_ID or not TENCENT_SECRET_KEY:
    raise ValueError("TENCENT_SECRET_ID and TENCENT_SECRET_KEY required")


print(f"🔑 Secret ID: {TENCENT_SECRET_ID[:10]}... (OK)")
print(f"🔑 Secret Key: {'установлен' if TENCENT_SECRET_KEY else 'ОТСУТСТВУЕТ'}")


HUNYUAN_HOST = "hunyuan.intl.tencentcloudapi.com"


intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


flask_app = Flask(__name__)


@flask_app.route('/')
def home():
    return "Discord Test Bot is running!"


@flask_app.route('/health')
def health():
    return "OK"


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
        "Authorization": authorization,
        "Content-Type": ct,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": version,
        "X-TC-Region": region
    }


def test_hunyuan():
    """Тестовый вызов Hunyuan"""
    print("🔄 Тестирую подключение к Hunyuan API...")
    try:
        action = "SubmitHunyuanTo3DProJob"
        payload = json.dumps({"Prompt": "cube"})
        headers = get_tencent_headers(action, payload)
        resp = requests.post(f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=30)
        
        print(f"📡 Статус: {resp.status_code}")
        print(f"📄 Ответ: {resp.text[:500]}")
        
        if resp.status_code == 200:
            data = resp.json()
            if "Response" in data and "JobId" in data["Response"]:
                print(f"✅ Hunyuan доступен! Job ID: {data['Response']['JobId'][:20]}...")
                return True
            else:
                print(f"❌ Странный ответ: {data}")
        else:
            print(f"❌ Ошибка HTTP: {resp.status_code}")
            print(f"📄 Тело ошибки: {resp.text[:500]}")
        return False
    except Exception as e:
        print(f"❌ Исключение: {e}")
        return False


@bot.event
async def on_ready():
    print(f"✅ {bot.user} is online!")
    print("🔄 Тестирую Hunyuan...")
    result = await asyncio.to_thread(test_hunyuan)
    if result:
        print("✅ Hunyuan готов к работе!")
    else:
        print("❌ Hunyuan недоступен. Проверь ключи и баланс.")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    if message.content.startswith("!test"):
        await message.channel.send("🔄 Проверяю Hunyuan...")
        try:
            action = "SubmitHunyuanTo3DProJob"
            payload = json.dumps({"Prompt": "cube"})
            headers = get_tencent_headers(action, payload)
            resp = requests.post(f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=30)
            
            await message.channel.send(f"📡 Статус: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                if "Response" in data and "JobId" in data["Response"]:
                    await message.channel.send(f"✅ Hunyuan работает! Job ID: {data['Response']['JobId'][:20]}...")
                else:
                    await message.channel.send(f"❌ Странный ответ: {data}")
            else:
                await message.channel.send(f"❌ Ошибка: {resp.text[:300]}")
        except Exception as e:
            await message.channel.send(f"❌ Исключение: {str(e)}")


def run_bot():
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
