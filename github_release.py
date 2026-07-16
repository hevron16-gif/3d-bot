"""Create GitHub release and upload assets."""
import json, os, urllib.request, mimetypes

GITHUB_TOKEN = "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"
REPO = "hevron16-gif/3d-bot"
TAG = "v1.0.10"
ASSETS_DIR = "release_build"

def api_call(method, path, data=None, json_data=None, content_type=None):
    url = f"https://api.github.com/repos/{REPO}/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "User-Agent": "AutoDiagAI/1.0",
        "Accept": "application/vnd.github.v3+json",
    }
    if json_data:
        data = json.dumps(json_data).encode()
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return json.loads(r.read()) if r.status != 204 else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"API ERROR {e.code}: {body[:500]}")
        raise

# --- Step 1: Create Release ---
print("[1/3] Creating GitHub release...")
release_body = """## AutoDiag AI v1.0.10 — Protected (WAF-Bypass)

### Что нового
- **WAF-bypass**: полный обход Cloudflare WAF для POST /diagnose
  - Base64-кодирование параметров с авто-padding
  - Fallback на URL-параметр `payload` при 422
  - DiagnoseWAFShield middleware — проверка токсичных паттернов
  - WAFBypassMiddleware — декодирование из скрытых каналов
- **CloudflareMiddleware**: обработка preflight, CF-заголовки, OPTIONS 204
- Увеличен MAX_BODY_SIZE до 10 МБ
- Исправлен 403 → подсказка использовать GET
- Все 32 теста пройдены

### Файлы
- `CarDiagnosticApp_Windows.zip` — приложение для Windows (распаковать, запустить setup.vbs)
- `CarDiagnosticApp.apk` — приложение для Android 8.0+
- `CarDiagnosticServer_v1.0.10.zip` — исходный код сервера (FastAPI)

### Сервер
https://car-diagnostic-ai.onrender.com
"""

payload = {
    "tag_name": TAG,
    "target_commitish": "main",
    "name": "AutoDiag AI v1.0.10 — Protected",
    "body": release_body,
    "draft": False,
    "prerelease": False,
}

try:
    release = api_call("POST", "releases", json_data=payload)
    release_id = release["id"]
    print(f"  Release ID: {release_id}")
    print(f"  URL: {release['html_url']}")
except urllib.error.HTTPError as e:
    if "already_exists" in str(e.read() if hasattr(e, 'read') else ""):
        print("  Release already exists, fetching...")
        release = api_call("GET", f"releases/tags/{TAG}")
        release_id = release["id"]
        print(f"  Existing release ID: {release_id}")
    else:
        raise

# --- Step 2: Upload Assets ---
print("\n[2/3] Uploading assets...")
assets = [
    "CarDiagnosticApp_Windows.zip",
    "CarDiagnosticApp.apk",
    "CarDiagnosticServer_v1.0.10.zip",
]

for asset_name in assets:
    asset_path = os.path.join(ASSETS_DIR, asset_name)
    if not os.path.exists(asset_path):
        print(f"  SKIP: {asset_name} (not found)")
        continue

    sz = os.path.getsize(asset_path)
    print(f"  Uploading: {asset_name} ({sz/1024/1024:.1f} MB)...")

    with open(asset_path, "rb") as fh:
        data = fh.read()

    upload_url = f"releases/{release_id}/assets?name={asset_name}"
    mime, _ = mimetypes.guess_type(asset_name)
    mime = mime or "application/octet-stream"

    result = api_call("POST", upload_url, data=data, content_type=mime)
    print(f"    Uploaded: {result.get('browser_download_url', 'OK')}")

# --- Step 3: Verify ---
print("\n[3/3] Verifying release...")
release = api_call("GET", f"releases/tags/{TAG}")
print(f"  Assets in release: {len(release.get('assets', []))}")
for a in release.get("assets", []):
    print(f"    - {a['name']}: {a['browser_download_url']}")

print("\n" + "=" * 60)
print("RELEASE CREATED SUCCESSFULLY")
print(f"URL: {release['html_url']}")
print("=" * 60)
