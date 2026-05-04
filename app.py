import os
import json
import time
import threading
import requests
import hashlib
import hmac
from datetime import datetime
from flask import Flask


# ========== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ==========
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY")


if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN missing")


API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# ========== ОТПРАВКА ==========
def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    requests.post(API_URL + "/sendMessage", json=payload, timeout=10)


def send_document(chat_id, file_content, filename="model.stl", caption=""):
    files = {"document": (filename, file_content)}
    data = {"chat_id": chat_id, "caption": caption}
    requests.post(API_URL + "/sendDocument", files=files, data=data, timeout=60)


# ========== КЛАВИАТУРЫ ==========
main_keyboard = {
    "inline_keyboard": [
        [{"text": "🎲 Meshy (Быстрая)", "callback_data": "gen_meshy"}],
        [{"text": "🔧 Hunyuan (Точная)", "callback_data": "gen_hunyuan"}],
        [{"text": "📦 Мои модели", "callback_data": "my_models"}],
        [{"text": "💎 Подписка", "callback_data": "subscription"}],
    ]
}
back_keyboard = {
    "inline_keyboard": [[{"text": "🔙 Назад", "callback_data": "menu"}]]
}


# ========== HUNYUAN С ПОДПИСЬЮ ==========
HUNYUAN_HOST = "hunyuan.intl.tencentcloudapi.com"


def get_tencent_headers(action, payload):
    service = "hunyuan"
    host = HUNYUAN_HOST
    region = "ap-singapore"
    version = "2023-09-01"
    algorithm = "TC3-HMAC-SHA256"
    timestamp = int(time.time())
    date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
    
    http_method = "POST"
    canonical_uri = "/"
    canonical_querystring = ""
    ct = "application/json"
    canonical_headers = f"content-type:{ct}\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = f"{http_method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{hashed_payload}"
    
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


def hunyuan_generate(prompt):
    action = "SubmitHunyuanTo3DProJob"
    payload = json.dumps({
        "Prompt": prompt,
        "FaceCount": 500000,
        "GenerateType": "Pro"      # "Normal" или "Pro"
    })
    headers = get_tencent_headers(action, payload)
    resp = requests.post(f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=30)
    data = resp.json()
    if "Response" not in data or "JobId" not in data["Response"]:
        raise Exception(f"Hunyuan submit error: {data}")
    job_id = data["Response"]["JobId"]


    action = "QueryHunyuanTo3DProJob"
    for _ in range(40):
        time.sleep(5)
        payload = json.dumps({"JobId": job_id})
        headers = get_tencent_headers(action, payload)
        resp = requests.post(f"https://{HUNYUAN_HOST}", headers=headers, data=payload, timeout=30)
        data = resp.json()
        if "Response" in data:
            status = data["Response"].get("Status")
            if status == "DONE":
                result_files = data["Response"].get("ResultFile3Ds", [])
                if result_files and "Url" in result_files[0]:
                    glb_url = result_files[0]["Url"]
                    # Конвертация GLB → STL (через тот же API)
                    conv_action = "Convert3DFormat"
                    conv_payload = json.dumps({
                        "File3D": glb_url,
                        "Format": "STL"
                    })
                    conv_headers = get_tencent_headers(conv_action, conv_payload)
                    conv_resp = requests.post(f"https://{HUNYUAN_HOST}", headers=conv_headers, data=conv_payload, timeout=60)
                    conv_data = conv_resp.json()
                    if "Response" in conv_data and "ResultFile3D" in conv_data["Response"]:
                        stl_url = conv_data["Response"]["ResultFile3D"]
                        model_resp = requests.get(stl_url, timeout=60)
                        if model_resp.status_code == 200:
                            return model_resp.content
                        else:
                            raise Exception("Failed to download STL")
                    else:
                        raise Exception(f"Conversion error: {conv_data}")
                else:
                    raise Exception("No result file URL")
            elif status == "FAIL":
                raise Exception("Generation failed")
    raise Exception("Hunyuan timeout")


# ========== MESHY ==========
MESHY_API_URL = "https://api.meshy.ai/openapi/v1"


def meshy_generate(prompt):
    if not MESHY_API_KEY:
        raise Exception("Meshy API key not configured")
    headers = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}
    payload = {"mode": "preview", "prompt": prompt, "art_style": "realistic", "should_remesh": True}
    resp = requests.post(f"{MESHY_API_URL}/text-to-3d", headers=headers, json=payload, timeout=30)
    if resp.status_code not in (200, 202):
        raise Exception(f"Meshy error: {resp.status_code}")
    task_id = resp.json().get("result")
    if not task_id:
        raise Exception("No task_id")
    
    while True:
        time.sleep(5)
        r = requests.get(f"{MESHY_API_URL}/text-to-3d/{task_id}", headers=headers)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "SUCCEEDED":
                model_url = data.get("model_urls", {}).get("glb")
                if model_url:
                    return requests.get(model_url).content
            elif data.get("status") == "FAILED":
                raise Exception("Meshy failed")
        elif r.status_code == 404:
            time.sleep(3)


# ========== ЛИМИТЫ ==========
user_limits = {}


def get_limit(user_id):
    today = int(time.time() // 86400)
    if user_id not in user_limits or user_limits[user_id][0] != today:
        user_limits[user_id] = (today, 3)
        return 3
    return max(0, 3 - user_limits[user_id][1])


def dec_limit(user_id):
    today = int(time.time() // 86400)
    if user_id not in user_limits or user_limits[user_id][0] != today:
        user_limits[user_id] = (today, 1)
    else:
        user_limits[user_id] = (today, user_limits[user_id][1] + 1)


# ========== ОСНОВНОЙ ЦИКЛ ==========
last_update_id = 0
user_states = {}


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
                            send_message(chat_id, "Выбери движок:", keyboard=main_keyboard)
                        elif data == "gen_meshy":
                            user_states[chat_id] = "meshy"
                            send_message(chat_id, "🎲 Выбран Meshy. Напиши промт:")
                        elif data == "gen_hunyuan":
                            user_states[chat_id] = "hunyuan"
                            send_message(chat_id, "🔧 Выбран Hunyuan. Напиши промт:")
                        elif data == "my_models":
                            send_message(chat_id, "📦 История появится позже.")
                        elif data == "subscription":
                            send_message(chat_id, "💎 Premium — 299₽/мес\nСкоро!", keyboard=back_keyboard)
                    
                    elif "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        user_id = msg["from"]["id"]
                        if "text" not in msg:
                            continue
                        text = msg["text"]
                        
                        if text == "/start":
                            send_message(chat_id, "👋 Привет! Я бот для генерации 3D-моделей.\n\n🎲 Meshy — фигурки\n🔧 Hunyuan — точные детали\n\nБесплатно: 3 в день", keyboard=main_keyboard)
                            continue
                        
                        if chat_id in user_states:
                            engine = user_states.pop(chat_id)
                            remaining = get_limit(user_id)
                            if remaining <= 0:
                                send_message(chat_id, "❌ Лимит на сегодня. Купи Premium!")
                                continue
                            
                            send_message(chat_id, f"⏳ Генерирую через {engine.upper()}... (1-3 мин)")
                            try:
                                if engine == "meshy":
                                    model = meshy_generate(text)
                                else:
                                    model = hunyuan_generate(text)
                                dec_limit(user_id)
                                send_document(chat_id, model, caption=f"{engine.upper()}: {text[:100]}")
                                send_message(chat_id, "✅ Готово!")
                            except Exception as e:
                                send_message(chat_id, f"❌ Ошибка: {str(e)}")
                        else:
                            send_message(chat_id, "Сначала выбери движок в меню.", keyboard=main_keyboard)
            time.sleep(1)
        except Exception as e:
            print(f"Poll error: {e}")
            time.sleep(5)


# ========== FLASK ==========
flask_app = Flask(__name__)


@flask_app.route('/')
def home():
    return "Bot running (Meshy + Hunyuan)"


@flask_app.route('/health')
def health():
    return "OK"


if __name__ == "__main__":
    threading.Thread(target=poll, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
