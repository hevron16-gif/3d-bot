import os
import json
import time
import base64
import sqlite3
import hashlib
import hmac
import threading
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from requests.exceptions import Timeout, ConnectionError
from flask import Flask


# ==================== КОНФИГУРАЦИЯ ====================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY")
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
DB_PATH = os.environ.get("DB_PATH", "bot.db")


HUNYUAN_HOST = "hunyuan.intl.tencentcloudapi.com"
REGION = "ap-singapore"
VERSION = "2023-09-01"


# ЦЕНЫ (Stars)
PRICE_SINGLE = 50
PRICE_PACK_5 = 200
PRICE_SUB_LITE = 150
PRICE_SUB_PRO = 300
PRICE_PART_API = 80


FREE_LIMIT = 2
REQ_PER_MINUTE = 3
REQ_PER_HOUR = 20


BANNED_WORDS = {
    "наркотик", "наркотики", "порно", "секс", "оружие", "бомба", "теракт",
    "убийство", "суицид", "самоубийство", "педофил", "детское порно",
    "экстремизм", "фашизм", "нацизм", "насилие", "жестокость",
    "пистолет", "автомат", "винтовка", "взрывчатка", "граната",
    "naked", "nude", "porn", "sex", "weapon", "bomb", "terrorist",
    "kill", "murder", "suicide", "drug", "cocaine", "heroin",
    "gun", "pistol", "rifle", "grenade", "explosive"
}


if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN missing")
if not TENCENT_SECRET_ID or not TENCENT_SECRET_KEY:
    raise ValueError("TENCENT_SECRET_ID and TENCENT_SECRET_KEY required")


API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MESHY_API_URL = "https://api.meshy.ai/v2"


# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        free_used INTEGER DEFAULT 0,
        paid_one INTEGER DEFAULT 0,
        pack_remaining INTEGER DEFAULT 0,
        subscription_until REAL DEFAULT 0,
        sub_type TEXT DEFAULT '',
        sub_generations_used INTEGER DEFAULT 0,
        part_paid INTEGER DEFAULT 0,
        service TEXT DEFAULT 'hunyuan',
        format TEXT DEFAULT 'stl'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS stats (
        id INTEGER PRIMARY KEY CHECK(id=1),
        total_gens INTEGER DEFAULT 0,
        total_users TEXT DEFAULT '[]',
        daily_stats TEXT DEFAULT '{}'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS rate_limits (
        user_id INTEGER,
        timestamp REAL,
        PRIMARY KEY(user_id, timestamp)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS admin_threads (
        admin_chat INTEGER PRIMARY KEY,
        user_id INTEGER,
        active INTEGER DEFAULT 1
    )''')
    c.execute('''INSERT OR IGNORE INTO stats(id) VALUES(1)''')
    conn.commit()
    conn.close()


def db_get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.commit()
    conn.close()
    keys = ['user_id','free_used','paid_one','pack_remaining','subscription_until','sub_type','sub_generations_used','part_paid','service','format']
    return dict(zip(keys, row))


def db_update_user(user_id, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for k, v in kwargs.items():
        c.execute(f"UPDATE users SET {k}=? WHERE user_id=?", (v, user_id))
    conn.commit()
    conn.close()


def check_rate_limit(user_id):
    now = time.time()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM rate_limits WHERE timestamp < ?", (now - 3600,))
    c.execute("SELECT COUNT(*) FROM rate_limits WHERE user_id=? AND timestamp > ?", (user_id, now - 60))
    per_min = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM rate_limits WHERE user_id=?", (user_id,))
    per_hour = c.fetchone()[0]
    if per_min >= REQ_PER_MINUTE:
        conn.close()
        return False, "Слишком много запросов. Подождите минуту."
    if per_hour >= REQ_PER_HOUR:
        conn.close()
        return False, "Лимит запросов на час исчерпан."
    c.execute("INSERT INTO rate_limits(user_id, timestamp) VALUES(?,?)", (user_id, now))
    conn.commit()
    conn.close()
    return True, ""


def can_generate(user_id):
    u = db_get_user(user_id)
    now = time.time()
    if u['subscription_until'] > now and u['sub_type'] == 'pro':
        return True, "premium_pro"
    if u['subscription_until'] > now and u['sub_type'] == 'lite':
        if u['sub_generations_used'] < 20:
            return True, "premium_lite"
        else:
            return False, "lite_limit"
    if u['pack_remaining'] > 0:
        return True, "pack"
    if u['paid_one']:
        return True, "paid"
    if user_id == ADMIN_CHAT_ID:
        return True, "free"
    if u['free_used'] < FREE_LIMIT:
        return True, "free"
    return False, ""


def use_generation(user_id, gen_type):
    if gen_type == "free" and user_id != ADMIN_CHAT_ID:
        u = db_get_user(user_id)
        db_update_user(user_id, free_used=u['free_used'] + 1)
    elif gen_type == "premium_lite":
        u = db_get_user(user_id)
        db_update_user(user_id, sub_generations_used=u['sub_generations_used'] + 1)
    elif gen_type == "pack":
        u = db_get_user(user_id)
        db_update_user(user_id, pack_remaining=u['pack_remaining'] - 1)
    elif gen_type == "paid":
        db_update_user(user_id, paid_one=0)


def get_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM stats WHERE id=1")
    row = c.fetchone()
    conn.close()
    return {"total_gens": row[1], "total_users": json.loads(row[2]), "daily_stats": json.loads(row[3])}


def update_stats(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    s = get_stats()
    s['total_gens'] += 1
    users = s['total_users']
    if user_id not in users:
        users.append(user_id)
    ds = s['daily_stats']
    ds[today] = ds.get(today, 0) + 1
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE stats SET total_gens=?, total_users=?, daily_stats=? WHERE id=1",
              (s['total_gens'], json.dumps(users), json.dumps(ds)))
    conn.commit()
    conn.close()


def set_admin_thread(admin_chat, user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO admin_threads(admin_chat, user_id, active) VALUES(?,?,1)", (admin_chat, user_id))
    conn.commit()
    conn.close()


def get_admin_thread(admin_chat):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM admin_threads WHERE admin_chat=? AND active=1", (admin_chat,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def close_admin_thread(admin_chat):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE admin_threads SET active=0 WHERE admin_chat=?", (admin_chat,))
    conn.commit()
    conn.close()


# ==================== TENCENT AUTH ====================
def get_tencent_headers(action, payload):
    service = "hunyuan"
    host = HUNYUAN_HOST
    algorithm = "TC3-HMAC-SHA256"
    timestamp = int(time.time())
    date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
    ct = "application/json"
    canonical_headers = f"content-type:{ct}\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{hashed_payload}"
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    secret_date = hmac.new(("TC3" + TENCENT_SECRET_KEY).encode(), date.encode(), hashlib.sha256).digest()
    secret_service = hmac.new(secret_date, service.encode(), hashlib.sha256).digest()
    secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
    signature = hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    auth = f"{algorithm} Credential={TENCENT_SECRET_ID}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
    return {
        "Authorization": auth, "Content-Type": ct, "Host": host,
        "X-TC-Action": action, "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": VERSION, "X-TC-Region": REGION
    }


def safe_request(method, url, max_retries=3, **kwargs):
    for attempt in range(max_retries):
        try:
            if method == "post":
                return requests.post(url, **kwargs, timeout=90)
            return requests.get(url, **kwargs, timeout=120)
        except (Timeout, ConnectionError) as e:
            print(f"⚠️ Сетевая ошибка (попытка {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(15)
            else:
                raise


# ==================== HUNYUAN API ====================
def hunyuan_generate_text(prompt, output_format="stl"):
    action = "SubmitHunyuanTo3DProJob"
    payload = json.dumps({"Prompt": prompt[:900]})
    headers = get_tencent_headers(action, payload)
    resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload)
    data = resp.json()
    if "Response" not in data or "JobId" not in data["Response"]:
        raise Exception(f"Hunyuan submit error: {data}")
    job_id = data["Response"]["JobId"]


    action = "QueryHunyuanTo3DProJob"
    for _ in range(40):
        time.sleep(5)
        payload = json.dumps({"JobId": job_id})
        headers = get_tencent_headers(action, payload)
        resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload)
        data = resp.json()
        if "Response" not in data:
            continue
        status = data["Response"].get("Status")
        if status == "DONE":
            files = data["Response"].get("ResultFile3Ds", [])
            if not files or "Url" not in files[0]:
                raise Exception("No result URL")
            glb_url = files[0]["Url"]
            if output_format == "glb":
                r = safe_request("get", glb_url)
                return r.content, "glb"
            c_action = "Convert3DFormat"
            c_payload = json.dumps({"File3D": glb_url, "Format": "STL"})
            c_headers = get_tencent_headers(c_action, c_payload)
            c_resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=c_headers, data=c_payload)
            c_data = c_resp.json()
            if "Response" in c_data and "ResultFile3D" in c_data["Response"]:
                stl_url = c_data["Response"]["ResultFile3D"]
                r = safe_request("get", stl_url)
                return r.content, "stl"
            raise Exception("STL conversion failed")
        elif status == "FAIL":
            raise Exception("Hunyuan generation failed")
    raise Exception("Hunyuan timeout")


def hunyuan_generate_photo(image_base64):
    if len(image_base64) > 8000000:
        raise Exception("Фото слишком большое после кодирования (>8MB)")
    action = "SubmitHunyuanTo3DProJob"
    payload = json.dumps({"ImageBase64": image_base64})
    headers = get_tencent_headers(action, payload)
    resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload)
    data = resp.json()
    if "Response" not in data or "JobId" not in data["Response"]:
        raise Exception(f"Hunyuan photo error: {data}")
    job_id = data["Response"]["JobId"]


    action = "QueryHunyuanTo3DProJob"
    for _ in range(40):
        time.sleep(5)
        payload = json.dumps({"JobId": job_id})
        headers = get_tencent_headers(action, payload)
        resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload)
        data = resp.json()
        if "Response" not in data:
            continue
        status = data["Response"].get("Status")
        if status == "DONE":
            files = data["Response"].get("ResultFile3Ds", [])
            if not files or "Url" not in files[0]:
                raise Exception("No result URL")
            glb_url = files[0]["Url"]
            c_action = "Convert3DFormat"
            c_payload = json.dumps({"File3D": glb_url, "Format": "STL"})
            c_headers = get_tencent_headers(c_action, c_payload)
            c_resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=c_headers, data=c_payload)
            c_data = c_resp.json()
            if "Response" in c_data and "ResultFile3D" in c_data["Response"]:
                stl_url = c_data["Response"]["ResultFile3D"]
                r = safe_request("get", stl_url)
                return r.content, "stl"
            raise Exception("Photo STL conversion failed")
        elif status == "FAIL":
            raise Exception("Photo generation failed")
    raise Exception("Photo timeout")


def hunyuan_part_segment(fbx_url, staged=False, segmentation_info=None):
    action = "SubmitHunyuan3DPartJob"
    body = {"File": {"Type": "FBX", "Url": fbx_url}}
    if staged:
        body["EnableStagedGeneration"] = True
    if segmentation_info:
        body["PartSegmentationInfo"] = json.dumps(segmentation_info)
    payload = json.dumps(body)
    headers = get_tencent_headers(action, payload)
    resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload)
    data = resp.json()
    if "Response" not in data or "JobId" not in data["Response"]:
        raise Exception(f"Part job error: {data}")
    return data["Response"]["JobId"]


def hunyuan_query_part(job_id):
    action = "QueryHunyuan3DPartJob"
    for _ in range(40):
        time.sleep(5)
        payload = json.dumps({"JobId": job_id})
        headers = get_tencent_headers(action, payload)
        resp = safe_request("post", f"https://{HUNYUAN_HOST}", headers=headers, data=payload)
        data = resp.json()
        if "Response" not in data:
            continue
        status = data["Response"].get("Status")
        if status == "DONE":
            return data["Response"]
        elif status == "FAIL":
            raise Exception("Part segmentation failed")
    raise Exception("Part job timeout")


# ==================== MESHY API ====================
def meshy_generate(prompt, output_format="stl"):
    if not MESHY_API_KEY:
        raise Exception("Meshy API ключ не настроен")
    headers = {"Authorization": f"Bearer {MESHY_API_KEY}", "Content-Type": "application/json"}


    payload = {
        "mode": "preview",
        "prompt": prompt[:800],
        "art_style": "realistic",
        "ai_model": "meshy-6",
        "topology": "triangle",
        "target_formats": [output_format]
    }
    resp = safe_request("post", f"{MESHY_API_URL}/text-to-3d", headers=headers, json=payload)
    data = resp.json()
    if "result" not in data:
        raise Exception(f"Meshy error: {data}")
    task_id = data["result"]


    for _ in range(40):
        time.sleep(5)
        resp = safe_request("get", f"{MESHY_API_URL}/text-to-3d/{task_id}", headers=headers)
        data = resp.json()
        status = data.get("status")
        if status == "SUCCEEDED":
            r_payload = {"mode": "refine", "preview_task_id": task_id, "enable_pbr": True}
            r_resp = safe_request("post", f"{MESHY_API_URL}/text-to-3d", headers=headers, json=r_payload)
            r_data = r_resp.json()
            if "result" not in r_data:
                raise Exception(f"Meshy refine error: {r_data}")
            r_task = r_data["result"]
            for _ in range(40):
                time.sleep(5)
                r_resp = safe_request("get", f"{MESHY_API_URL}/text-to-3d/{r_task}", headers=headers)
                r_data = r_resp.json()
                if r_data.get("status") == "SUCCEEDED":
                    url = r_data.get("model_urls", {}).get(output_format) or r_data.get("model_urls", {}).get("stl")
                    if not url:
                        raise Exception("Meshy: no model URL")
                    m = safe_request("get", url)
                    return m.content, output_format if url.endswith(output_format) else "stl"
                elif r_data.get("status") == "FAILED":
                    raise Exception("Meshy refine failed")
            raise Exception("Meshy refine timeout")
        elif status == "FAILED":
            raise Exception(f"Meshy preview failed: {data.get('error')}")
    raise Exception("Meshy preview timeout")


# ==================== TELEGRAM BOT ====================
executor = ThreadPoolExecutor(max_workers=5)
user_busy = {}


def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    requests.post(API_URL + "/sendMessage", json=payload, timeout=10)


def send_document(chat_id, file_content, filename="model.stl", caption=""):
    if not file_content or len(file_content) == 0:
        send_message(chat_id, "❌ Ошибка: файл пустой.")
        return
    if len(file_content) > 50 * 1024 * 1024:
        send_message(chat_id, "❌ Модель слишком большая для Telegram (>50 МБ).")
        return
    files = {"document": (filename, file_content)}
    data = {"chat_id": chat_id, "caption": caption}
    requests.post(API_URL + "/sendDocument", files=files, data=data, timeout=120)


def send_invoice(chat_id, title, description, payload, stars_amount):
    requests.post(API_URL + "/sendInvoice", json={
        "chat_id": chat_id, "title": title, "description": description,
        "payload": payload, "provider_token": "", "currency": "XTR",
        "prices": [{"label": "Оплата", "amount": stars_amount}],
        "start_parameter": "generate_3d"
    })


def main_keyboard(user_id):
    u = db_get_user(user_id)
    fmt = u.get('format', 'stl')
    return {"inline_keyboard": [
        [{"text": "🔧 Генерация по тексту", "callback_data": "gen_text"}],
        [{"text": "🎨 Генерация по фото", "callback_data": "gen_photo"}],
        [{"text": "⚡ Выбрать сервис", "callback_data": "choose_service"}],
        [{"text": f"📦 Формат: {fmt.upper()}", "callback_data": "choose_format"}],
        [{"text": f"🎟 Разовая ({PRICE_SINGLE}⭐)", "callback_data": "buy_one"}],
        [{"text": f"📦 Пакет 5 шт. ({PRICE_PACK_5}⭐)", "callback_data": "buy_pack"}],
        [{"text": f"💎 Lite ({PRICE_SUB_LITE}⭐/мес)", "callback_data": "sub_lite"}],
        [{"text": f"💎 Pro ({PRICE_SUB_PRO}⭐/мес)", "callback_data": "sub_pro"}],
        
        [{"text": "💰 Баланс", "callback_data": "my_balance"}],
        [{"text": "❓ Помощь", "callback_data": "help_info"}],
    ]}


back_keyboard = {"inline_keyboard": [[{"text": "🔙 Главное меню", "callback_data": "menu"}]]}
cancel_keyboard = {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel"}]]}


def check_content(text):
    if not text:
        return True
    return not any(w in text.lower() for w in BANNED_WORDS)


def process_text_generation(chat_id, user_id, prompt, gen_type):
    u = db_get_user(user_id)
    service = u.get('service', 'hunyuan')
    fmt = u.get('format', 'stl')
    try:
        model, used_fmt = None, fmt
        if service == "meshy" and MESHY_API_KEY:
            try:
                model, used_fmt = meshy_generate(prompt, fmt)
            except Exception as e:
                print(f"Meshy failed: {e}, fallback to Hunyuan")
                model, used_fmt = hunyuan_generate_text(prompt, fmt)
        else:
            try:
                model, used_fmt = hunyuan_generate_text(prompt, fmt)
            except Exception as e:
                print(f"Hunyuan failed: {e}")
                if MESHY_API_KEY:
                    model, used_fmt = meshy_generate(prompt, fmt)
                else:
                    raise
        if model:
            use_generation(user_id, gen_type)
            update_stats(user_id)
            rem = max(0, FREE_LIMIT - db_get_user(user_id)['free_used'])
            cap = f"✅ Модель готова! ({used_fmt.upper()})\n📝 {prompt[:100]}"
            if gen_type == "free" and user_id != ADMIN_CHAT_ID:
                cap += f"\n🎁 Осталось бесплатно: {rem} из {FREE_LIMIT}"
            send_document(chat_id, model, f"model.{used_fmt}", cap)
        else:
            send_message(chat_id, "❌ Ошибка генерации.")
    except Exception as e:
        send_message(chat_id, f"❌ Ошибка: {str(e)}")
    finally:
        user_busy[user_id] = False


def process_photo_generation(chat_id, user_id, image_b64, gen_type):
    try:
        model, fmt = hunyuan_generate_photo(image_b64)
        use_generation(user_id, gen_type)
        update_stats(user_id)
        rem = max(0, FREE_LIMIT - db_get_user(user_id)['free_used'])
        cap = "✅ Модель по фото готова!"
        if gen_type == "free" and user_id != ADMIN_CHAT_ID:
            cap += f"\n🎁 Осталось: {rem} из {FREE_LIMIT}"
        send_document(chat_id, model, "model.stl", cap)
    except Exception as e:
        send_message(chat_id, f"❌ Ошибка: {str(e)}")
    finally:
        user_busy[user_id] = False


def process_part_segmentation(chat_id, user_id, fbx_url):
    try:
        job_id = hunyuan_part_segment(fbx_url)
        result = hunyuan_query_part(job_id)
        files = result.get("ResultFile3Ds", [])
        seg_info = result.get("PartSegmentationInfo", "")
        caption = "✅ Модель разделена на части!\n\n<b>Файлы:</b>"
        for f in files:
            caption += f"\n• {f.get('Type', 'file')}: {f.get('Url', 'нет ссылки')}"
        if seg_info:
            caption += f"\n\n<b>JSON разметка:</b>\n<pre>{seg_info[:800]}</pre>"
        send_message(chat_id, caption, back_keyboard)
    except Exception as e:
        send_message(chat_id, f"❌ Ошибка Part API: {str(e)}", back_keyboard)


user_states = {}


def poll():
    init_db()
    last_update_id = 0
    print("🟢 Bot polling started", flush=True)


    while True:
        try:
            resp = requests.get(API_URL + "/getUpdates", params={"offset": last_update_id + 1, "timeout": 30}, timeout=35)
            if resp.status_code != 200:
                continue
            updates = resp.json().get("result", [])
            for update in updates:
                last_update_id = update["update_id"]


                # Pre-checkout
                if "pre_checkout_query" in update:
                    requests.post(API_URL + "/answerPreCheckoutQuery", json={
                        "pre_checkout_query_id": update["pre_checkout_query"]["id"], "ok": True
                    })
                    continue


                # Successful payment
                if "message" in update and "successful_payment" in update["message"]:
                    uid = update["message"]["from"]["id"]
                    cid = update["message"]["chat"]["id"]
                    pl = update["message"]["successful_payment"]["invoice_payload"]
                    if pl == "single_generation":
                        db_update_user(uid, paid_one=1)
                        send_message(cid, "✅ Разовая генерация активирована!")
                    elif pl == "pack_5":
                        db_update_user(uid, pack_remaining=5)
                        send_message(cid, "✅ Пакет 5 генераций активирован!")
                    elif pl == "sub_lite":
                        db_update_user(uid, subscription_until=time.time() + 30 * 86400, sub_type='lite', sub_generations_used=0)
                        send_message(cid, "✅ Подписка Lite активирована (20 ген/мес)!")
                    elif pl == "sub_pro":
                        db_update_user(uid, subscription_until=time.time() + 30 * 86400, sub_type='pro', sub_generations_used=0)
                        send_message(cid, "✅ Подписка Pro активирована (безлимит)!")
                    elif pl == "part_api":
                        db_update_user(uid, part_paid=1)
                        send_message(cid, "✅ Part API оплачен!\n\nОтправьте ссылку на FBX командой:\n<code>/part https://site.com/model.fbx</code>")
                    continue


                # Callbacks
                if "callback_query" in update:
                    cb = update["callback_query"]
                    cid = cb["message"]["chat"]["id"]
                    uid = cb["from"]["id"]
                    data = cb["data"]
                    requests.post(API_URL + "/answerCallbackQuery", json={"callback_query_id": cb["id"]})


                    if data == "menu":
                        close_admin_thread(cid)
                        send_message(cid, "Выбери действие:", main_keyboard(uid))
                    elif data == "help_info":
                        send_message(cid, "❓ <b>Как пользоваться:</b>\n\n1️⃣ Нажмите «Генерация по тексту» или «по фото»\n2️⃣ Отправьте описание или фото\n3️⃣ Подождите 1-5 минут\n\n🎁 Бесплатно: 2 модели\n🎟 Разовая: 50⭐\n📦 Пакет 5шт: 200⭐\n💎 Lite: 150⭐ (20 ген/мес)\n💎 Pro: 300⭐ (безлимит)\n🧩 Part API: 80⭐", back_keyboard)
                    elif data == "my_balance":
                        u = db_get_user(uid)
                        now = time.time()
                        if u['subscription_until'] > now and u['sub_type'] == 'pro':
                            status = "💎 Pro безлимит"
                        elif u['subscription_until'] > now and u['sub_type'] == 'lite':
                            used = u['sub_generations_used']
                            status = f"💎 Lite ({used}/20 использовано)"
                        elif u['pack_remaining'] > 0:
                            status = f"📦 Пакет: {u['pack_remaining']} шт. осталось"
                        elif u['paid_one']:
                            status = "🎟 Есть разовая генерация"
                        else:
                            status = f"🎁 Бесплатно: {max(0, FREE_LIMIT - u['free_used'])} из {FREE_LIMIT}"
                        send_message(cid, f"💰 <b>Баланс:</b>\n\n{status}\n⚡ Сервис: {u['service']}\n📦 Формат: {u['format'].upper()}", back_keyboard)
                    elif data == "choose_service":
                        kb = {"inline_keyboard": [
                            [{"text": "🔵 Hunyuan", "callback_data": "set_hunyuan"}, {"text": "🟣 Meshy", "callback_data": "set_meshy"}],
                            [{"text": "🔙 Назад", "callback_data": "menu"}]
                        ]}
                        send_message(cid, "Выберите сервис:", kb)
                    elif data == "set_hunyuan":
                        db_update_user(uid, service="hunyuan")
                        send_message(cid, "✅ Hunyuan", main_keyboard(uid))
                    elif data == "set_meshy":
                        if MESHY_API_KEY:
                            db_update_user(uid, service="meshy")
                            send_message(cid, "✅ Meshy", main_keyboard(uid))
                        else:
                            send_message(cid, "⚠️ Meshy недоступен.", main_keyboard(uid))
                    elif data == "choose_format":
                        kb = {"inline_keyboard": [
                            [{"text": "📦 STL", "callback_data": "set_stl"}, {"text": "📦 GLB", "callback_data": "set_glb"}],
                            [{"text": "🔙 Назад", "callback_data": "menu"}]
                        ]}
                        send_message(cid, "Выберите формат:", kb)
                    elif data == "set_stl":
                        db_update_user(uid, format="stl")
                        send_message(cid, "✅ STL", main_keyboard(uid))
                    elif data == "set_glb":
                        db_update_user(uid, format="glb")
                        send_message(cid, "✅ GLB", main_keyboard(uid))
                    elif data == "buy_one":
                        send_invoice(cid, "Разовая генерация", "Одна генерация 3D-модели", "single_generation", PRICE_SINGLE)
                    elif data == "buy_pack":
                        send_invoice(cid, "Пакет 5 генераций", "5 генераций 3D-моделей", "pack_5", PRICE_PACK_5)
                    elif data == "sub_lite":
                        send_invoice(cid, "Подписка Lite", "20 генераций в месяц", "sub_lite", PRICE_SUB_LITE)
                    elif data == "sub_pro":
                        send_invoice(cid, "Подписка Pro", "Безлимитные генерации на месяц", "sub_pro", PRICE_SUB_PRO)
                    elif data in ("gen_text", "gen_photo"):
                        send_message(cid, "🔧 Выберите способ:", {"inline_keyboard": [
                            [{"text": "📝 По тексту", "callback_data": f"{data}_confirm"}, {"text": "📸 По фото", "callback_data": f"{data}_confirm"}],
                            [{"text": "🔙 Назад", "callback_data": "menu"}]
                        ]})
                    elif data == "gen_text_confirm":
                        user_states[cid] = "awaiting_text"
                        u = db_get_user(uid)
                        send_message(cid, f"📝 Отправьте описание модели.\n⚡ {u['service']} | 📦 {u['format'].upper()}", cancel_keyboard)
                    elif data == "gen_photo_confirm":
                        user_states[cid] = "awaiting_photo"
                        send_message(cid, "📸 Отправьте фото объекта.\n⚠️ До 4-5 минут.", cancel_keyboard)
                    elif data == "cancel":
                        user_states.pop(cid, None)
                        send_message(cid, "❌ Отменено.", main_keyboard(uid))
                    elif data == "part_menu":
                        send_message(cid, "🧩 <b>Part API</b>\n\nРазделение FBX-модели на части.\nДо 200 МБ, до 1.5M полигонов.\n\nДля начала оплатите:", {"inline_keyboard": [
                            [{"text": f"💳 Оплатить {PRICE_PART_API}⭐", "callback_data": "buy_part"}],
                            [{"text": "🔙 Назад", "callback_data": "menu"}]
                        ]})
                    elif data == "buy_part":
                        send_invoice(cid, "Part API", "Разделение 3D-модели на части", "part_api", PRICE_PART_API)
                    elif data.startswith("reply_to_"):
                        target = int(data.replace("reply_to_", ""))
                        set_admin_thread(cid, target)
                        send_message(cid, f"✏️ Пишите сообщение для {target}.\n/cancelreply — отмена")
                    elif data == "cancel_reply":
                        close_admin_thread(cid)
                        send_message(cid, "❌ Режим ответа отменён.", main_keyboard(uid))
                    continue


                if "message" not in update:
                    continue
                msg = update["message"]
                cid = msg["chat"]["id"]
                uid = msg["from"]["id"]


                if "text" in msg:
                    txt = msg["text"]
                    if txt == "/start":
                        u = db_get_user(uid)
                        rem = max(0, FREE_LIMIT - u['free_used'])
                        send_message(cid, f"👋 Привет! Генерация 3D-моделей.\n\n🎁 Бесплатно: {rem} из {FREE_LIMIT}\n🎟 Разовая: {PRICE_SINGLE}⭐\n📦 Пакет 5шт: {PRICE_PACK_5}⭐\n💎 Lite: {PRICE_SUB_LITE}⭐ (20 ген/мес)\n💎 Pro: {PRICE_SUB_PRO}⭐ (безлимит)\n🧩 Part API: {PRICE_PART_API}⭐", main_keyboard(uid))
                        continue
                    if txt == "/stats" and uid == ADMIN_CHAT_ID:
                        s = get_stats()
                        today = datetime.now().strftime("%Y-%m-%d")
                        text = f"📊 <b>Статистика</b>\n\n👥 Пользователей: {len(s['total_users'])}\n🎲 Всего генераций: {s['total_gens']}\n📅 Сегодня: {s['daily_stats'].get(today, 0)}"
                        send_message(cid, text)
                        continue
                    if txt == "/cancelreply" and uid == ADMIN_CHAT_ID:
                        close_admin_thread(cid)
                        send_message(cid, "❌ Отменено.")
                        continue
                    if uid == ADMIN_CHAT_ID and get_admin_thread(cid):
                        target = get_admin_thread(cid)
                        send_message(target, f"📨 <b>Ответ разработчика:</b>\n\n{txt}")
                        send_message(cid, f"✅ Отправлено {target}")
                        continue


                    # Команда /part для Part API
                    if txt.startswith('/part '):
                        u = db_get_user(uid)
                        if not u['part_paid']:
                            send_message(cid, "❌ Сначала оплатите Part API в меню.", main_keyboard(uid))
                            continue
                        url = txt[6:].strip()
                        if not url.startswith(('http://', 'https://')):
                            send_message(cid, "❌ Некорректная ссылка. Пример:\n<code>/part https://site.com/model.fbx</code>", main_keyboard(uid))
                            continue
                        db_update_user(uid, part_paid=0)
                        send_message(cid, "⏳ Запускаю разделение модели...")
                        executor.submit(process_part_segmentation, cid, uid, url)
                        continue


                    if cid in user_states:
                        state = user_states[cid]
                        if state == "awaiting_text" and not txt.startswith('/'):
                            if not check_content(txt):
                                send_message(cid, "⚠️ Запрещённые слова.", main_keyboard(uid))
                                continue
                            del user_states[cid]
                            ok, rm = check_rate_limit(uid)
                            if not ok:
                                send_message(cid, f"⏳ {rm}", main_keyboard(uid))
                                continue
                            if user_busy.get(uid):
                                send_message(cid, "⏳ Уже выполняется.", main_keyboard(uid))
                                continue
                            c, gt = can_generate(uid)
                            if not c:
                                if gt == "lite_limit":
                                    send_message(cid, "❌ Лимит Lite подписки исчерпан (20/мес).", main_keyboard(uid))
                                else:
                                    send_message(cid, "❌ Бесплатные генерации закончились.", main_keyboard(uid))
                                continue
                            user_busy[uid] = True
                            u = db_get_user(uid)
                            send_message(cid, f"⏳ Генерация ({u['service']}, {u['format'].upper()})...")
                            executor.submit(process_text_generation, cid, uid, txt, gt)
                            continue
                        elif state == "awaiting_photo":
                            send_message(cid, "📸 Отправьте фото, а не текст.", cancel_keyboard)
                            continue


                    if not txt.startswith('/') and uid != ADMIN_CHAT_ID:
                        uname = msg["from"].get("username")
                        fname = msg["from"].get("first_name", "")
                        tag = f"@{uname}" if uname else f"ID:{uid}"
                        set_admin_thread(ADMIN_CHAT_ID, uid)
                        kb = {"inline_keyboard": [
                            [{"text": "💬 Ответить", "callback_data": f"reply_to_{uid}"}],
                            [{"text": "❌ Отмена", "callback_data": "cancel_reply"}]
                        ]}
                        send_message(ADMIN_CHAT_ID, f"📩 <b>Сообщение</b>\n👤 {fname} ({tag})\n💬 {txt}", kb)
                        send_message(cid, "📨 Передано разработчику.")
                        continue


                if "photo" in msg and cid in user_states and user_states[cid] == "awaiting_photo":
                    del user_states[cid]
                    ok, rm = check_rate_limit(uid)
                    if not ok:
                        send_message(cid, f"⏳ {rm}", main_keyboard(uid))
                        continue
                    if user_busy.get(uid):
                        send_message(cid, "⏳ Уже выполняется.", main_keyboard(uid))
                        continue
                    c, gt = can_generate(uid)
                    if not c:
                        send_message(cid, "❌ Лимит исчерпан.", main_keyboard(uid))
                        continue
                    user_busy[uid] = True
                    send_message(cid, "⏳ Генерация по фото...")


                    def photo_task(cid, uid, gt, msg):
                        try:
                            fid = msg["photo"][-1]["file_id"]
                            fi = requests.get(API_URL + f"/getFile?file_id={fid}").json()
                            furl = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{fi['result']['file_path']}"
                            data = requests.get(furl, timeout=30).content
                            if len(data) > 10 * 1024 * 1024:
                                send_message(cid, "❌ Фото слишком большое (>10 МБ).")
                                user_busy[uid] = False
                                return
                            b64 = base64.b64encode(data).decode()
                            process_photo_generation(cid, uid, b64, gt)
                        except Exception as e:
                            send_message(cid, f"❌ Ошибка: {e}")
                            user_busy[uid] = False


                    executor.submit(photo_task, cid, uid, gt, msg)
                    continue


        except Exception as e:
            print(f"Poll error: {e}", flush=True)
            time.sleep(5)


# ==================== FLASK ====================
flask_app = Flask(__name__)


@flask_app.route('/')
def home():
    return "Bot running"


@flask_app.route('/health')
def health():
    return "OK"


if __name__ == "__main__":
    threading.Thread(target=poll, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
