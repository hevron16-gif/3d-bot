import discord
from discord.ext import commands
import os
import json
import time
import asyncio
import requests
import hashlib
import hmac
import base64
from datetime import datetime
from collections import defaultdict
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
bot = commands.Bot(command_prefix="!", intents=intents)


flask_app = Flask(__name__)


@flask_app.route('/')
def home():
    return "Discord 3D Bot is running!"


@flask_app.route('/health')
def health():
    return "OK"


# ========== ЛИМИТЫ ==========
user_limits = defaultdict(lambda: {"used": 0, "date": 0})


def can_generate(user_id):
    today = int(time.time() // 86400)
    if user_limits[user_id]["date"] != today:
        user_limits[user_id] = {"used": 0, "date": today}
    return user_limits[user_id]["used"] < 5


def use_generation(user_id):
    user_limits[user_id]["used"] += 1


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
        "Authorization": authorization,
        "Content-Type": ct,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": version,
        "X-TC-Region": region
    }


async def hunyuan_generate(prompt):
    for attempt in range(3):
        try:
            print(f"🟢 Попытка {attempt+1} для промта: {prompt[:50]}...")
            
            # 1. Отправляем задачу на генерацию
            action = "SubmitHunyuanTo3DProJob"
            payload = json.dumps({"Prompt": prompt[:900]})
            headers = get_tencent_headers(action, payload)
            resp = requests.post(f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=30)
            print(f"📡 Submit status: {resp.status_code}")
            
            if resp.status_code != 200:
                print(f"❌ Ошибка HTTP: {resp.status_code}")
                print(f"📄 Ответ: {resp.text[:200]}")
                raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
            
            data = resp.json()
            if "Response" not in data or "JobId" not in data["Response"]:
                print(f"❌ Ошибка в ответе: {data}")
                raise Exception(f"Hunyuan error: {data}")
            
            job_id = data["Response"]["JobId"]
            print(f"🟢 Job ID: {job_id}")
            
            # 2. Ожидаем результат
            action = "QueryHunyuanTo3DProJob"
            for _ in range(40):  # увеличил до 40 для надёжности
                await asyncio.sleep(4)
                payload = json.dumps({"JobId": job_id})
                headers = get_tencent_headers(action, payload)
                resp = requests.post(f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=30)
                data = resp.json()
                if "Response" in data:
                    status = data["Response"].get("Status")
                    print(f"🟢 Статус: {status}")
                    if status == "DONE":
                        result_files = data["Response"].get("ResultFile3Ds", [])
                        print(f"📁 Result files: {result_files}")
                        if result_files and "Url" in result_files[0]:
                            glb_url = result_files[0]["Url"]
                            print(f"🟢 GLB URL получен: {glb_url[:80]}...")
                            
                            # 3. Конвертируем GLB в STL
                            conv_action = "Convert3DFormat"
                            conv_payload = json.dumps({"File3D": glb_url, "Format": "STL"})
                            conv_headers = get_tencent_headers(conv_action, conv_payload)
                            conv_resp = requests.post(f"https://{HUNYUAN_HOST}", headers=conv_headers, data=conv_payload, timeout=60)
                            print(f"📡 Convert status: {conv_resp.status_code}")
                            
                            if conv_resp.status_code != 200:
                                print(f"❌ Ошибка конвертации: {conv_resp.status_code}")
                                print(f"📄 Ответ: {conv_resp.text[:200]}")
                                raise Exception(f"Convert error: {conv_resp.status_code}")
                            
                            conv_data = conv_resp.json()
                            if "Response" in conv_data and "ResultFile3D" in conv_data["Response"]:
                                stl_url = conv_data["Response"]["ResultFile3D"]
                                print(f"🟢 STL URL получен: {stl_url[:80]}...")
                                
                                model_resp = requests.get(stl_url, timeout=60)
                                print(f"📡 Download status: {model_resp.status_code}")
                                
                                if model_resp.status_code == 200:
                                    return model_resp.content
                                else:
                                    print(f"❌ Не удалось скачать STL: {model_resp.status_code}")
                                    raise Exception(f"Download failed: {model_resp.status_code}")
                    elif status == "FAIL":
                        raise Exception("Generation FAILED")
            
            if attempt < 2:
                print(f"⚠️ Попытка {attempt+1} не удалась, повтор через 5 секунд...")
                await asyncio.sleep(5)
                continue
            raise Exception("Timeout")
        except Exception as e:
            print(f"❌ Ошибка в попытке {attempt+1}: {e}")
            if attempt == 2:
                raise
            await asyncio.sleep(5)
    raise Exception("Не удалось сгенерировать модель после 3 попыток")


@bot.tree.command(name="generate", description="Generate 3D STL model from text prompt")
async def generate(interaction: discord.Interaction, prompt: str):
    print(f"🟢 Команда generate получена от {interaction.user.id}: {prompt[:50]}...")
    user_id = interaction.user.id
    if not can_generate(user_id):
        await interaction.response.send_message("❌ Daily free limit (5 models) reached. Support: @Kostya_3d_bot", ephemeral=True)
        return
    await interaction.response.defer()
    await interaction.followup.send(f"🔄 Generating `{prompt[:100]}`... (1-3 min)")
    try:
        stl = await hunyuan_generate(prompt)
        use_generation(user_id)
        await interaction.followup.send(file=discord.File(stl, filename="model.stl"))
        print(f"✅ Модель отправлена пользователю {user_id}")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")
        print(f"❌ Ошибка: {e}")


@bot.event
async def on_ready():
    print(f"✅ {bot.user} is online!")
    await bot.tree.sync()
    print("🔁 Slash commands synced")


def run_bot():
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
