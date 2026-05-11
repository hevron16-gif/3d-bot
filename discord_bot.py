import discord
from discord.ext import commands
import os
import json
import time
import requests
import hashlib
import hmac
import base64
from datetime import datetime
from collections import defaultdict
from flask import Flask
import threading
import asyncio


# ========== ПРИНУДИТЕЛЬНАЯ ОЧИСТКА ПЕРЕМЕННЫХ ==========
def clean_env_var(value):
    if value is None:
        return None
    cleaned = value.strip().replace('\x00', '').replace('\r', '').replace('\n', '')
    return cleaned


DISCORD_BOT_TOKEN = clean_env_var(os.environ.get("DISCORD_BOT_TOKEN"))
TENCENT_SECRET_ID = clean_env_var(os.environ.get("TENCENT_SECRET_ID"))
TENCENT_SECRET_KEY = clean_env_var(os.environ.get("TENCENT_SECRET_KEY"))


os.environ["DISCORD_BOT_TOKEN"] = DISCORD_BOT_TOKEN or ""
os.environ["TENCENT_SECRET_ID"] = TENCENT_SECRET_ID or ""
os.environ["TENCENT_SECRET_KEY"] = TENCENT_SECRET_KEY or ""


# ========== КОНФИГУРАЦИЯ ==========
ADMIN_USER_ID = 5193424909


if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN missing")
if not TENCENT_SECRET_ID or not TENCENT_SECRET_KEY:
    raise ValueError("TENCENT_SECRET_ID and TENCENT_SECRET_KEY required")


HUNYUAN_HOST = "hunyuan.intl.tencentcloudapi.com"


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
bot = commands.Bot(command_prefix="!", intents=intents)


# Flask для healthcheck
flask_app = Flask(__name__)


@flask_app.route('/')
def home():
    return "OK"


@flask_app.route('/health')
def health():
    return "OK"


# ========== БАЗА ДАННЫХ ==========
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
    date_str = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")


    http_method = "POST"
    canonical_uri = "/"
    canonical_querystring = ""
    ct = "application/json"
    canonical_headers = f"content-type:{ct}\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = f"{http_method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{hashed_payload}"
    credential_scope = f"{date_str}/{service}/tc3_request"
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashed_canonical}"
    secret_date = hmac.new(("TC3" + TENCENT_SECRET_KEY).encode("utf-8"), date_str.encode("utf-8"), hashlib.sha256).digest()
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


def hunyuan_generate(prompt):
    # Жёсткая очистка промпта от null byte и непечатных символов
    if isinstance(prompt, str):
        prompt = prompt.replace('\x00', '')
        prompt = ''.join(c for c in prompt if ord(c) >= 32 or c in '\n\r\t')
        prompt = prompt.strip()
    
    print(f"DEBUG: Cleaned prompt: {prompt}")
    print(f"DEBUG: Prompt length: {len(prompt)}")
    
    if not TENCENT_SECRET_ID or not TENCENT_SECRET_KEY:
        raise Exception("Tencent Cloud keys missing")
    
    for attempt in range(3):
        try:
            action = "SubmitHunyuanTo3DProJob"
            payload = json.dumps({"Prompt": prompt})
            print(f"DEBUG: Sending payload: {payload}")
            
            headers = get_tencent_headers(action, payload)
            resp = requests.post(f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=30)
            
            if resp.status_code != 200:
                raise Exception(f"Hunyuan API error: {resp.status_code}")
            
            data = resp.json()
            print(f"DEBUG: Response: {data}")
            
            if "Response" not in data or "JobId" not in data["Response"]:
                error_msg = data.get("Response", {}).get("Error", {}).get("Message", "Unknown error")
                raise Exception(f"Hunyuan submit error: {error_msg}")
            
            job_id = data["Response"]["JobId"]
            print(f"DEBUG: Job ID: {job_id}")
            
            action = "QueryHunyuanTo3DProJob"
            
            for _ in range(35):
                time.sleep(4)
                payload_query = json.dumps({"JobId": job_id})
                headers_query = get_tencent_headers(action, payload_query)
                resp_query = requests.post(f"https://{HUNYUAN_HOST}", headers=headers_query, data=payload_query, timeout=30)
                data_query = resp_query.json()
                
                if "Response" in data_query:
                    status = data_query["Response"].get("Status")
                    print(f"DEBUG: Status: {status}")
                    
                    if status == "DONE":
                        result_files = data_query["Response"].get("ResultFile3Ds", [])
                        if result_files and "Url" in result_files[0]:
                            glb_url = result_files[0]["Url"]
                            print(f"DEBUG: GLB URL: {glb_url}")
                            
                            # Конвертация в STL
                            conv_action = "Convert3DFormat"
                            conv_payload = json.dumps({"File3D": glb_url, "Format": "STL"})
                            conv_headers = get_tencent_headers(conv_action, conv_payload)
                            conv_resp = requests.post(f"https://{HUNYUAN_HOST}", headers=conv_headers, data=conv_payload, timeout=60)
                            conv_data = conv_resp.json()
                            
                            if "Response" in conv_data and "ResultFile3D" in conv_data["Response"]:
                                stl_url = conv_data["Response"]["ResultFile3D"]
                                print(f"DEBUG: STL URL: {stl_url}")
                                model_resp = requests.get(stl_url, timeout=60)
                                if model_resp.status_code == 200:
                                    return model_resp.content
                            raise Exception("Conversion failed")
                    elif status == "FAIL":
                        raise Exception("Generation failed")
            
            if attempt < 2:
                time.sleep(5)
                continue
            raise Exception("Timeout")
            
        except Exception as e:
            print(f"DEBUG: Attempt {attempt+1} failed: {e}")
            if attempt == 2:
                raise
            time.sleep(5)
    
    raise Exception("Hunyuan generation error")


@bot.tree.command(name="generate", description="Generate 3D STL model from text prompt")
async def generate(interaction: discord.Interaction, prompt: str):
    # Очистка промпта
    prompt = prompt.replace('\x00', '').strip()
    
    if not can_generate(interaction.user.id):
        await interaction.response.send_message("❌ Daily free limit (5 models) reached. Support the project via Telegram: @Kostya_3d_bot", ephemeral=True)
        return
    
    await interaction.response.send_message(f"🔄 Generating 3D model from prompt:\n`{prompt}`\nPlease wait 1-3 minutes...")
    
    try:
        stl_data = hunyuan_generate(prompt)
        use_generation(interaction.user.id)
        await interaction.followup.send(file=discord.File(stl_data, filename="model.stl"))
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")


@bot.event
async def on_ready():
    print(f"✅ Bot {bot.user} is online!")
    await asyncio.sleep(3)
    await bot.tree.sync()
    print("✅ Commands synced!")


def run_bot():
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    # Запускаем бота в потоке
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Запускаем Flask для healthcheck
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
