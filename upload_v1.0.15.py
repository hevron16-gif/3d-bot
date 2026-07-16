"""Update v1.0.15 release assets on GitHub — Windows App SDK bootstrap fix."""
import json, urllib.request

TOKEN = "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"
REPO = "hevron16-gif/3d-bot"
TAG = "v1.0.15"

RELEASE_DIR = r"C:\Users\User\.nanobot\workspace\release_build"

def api_request(url, method="GET", data=None, content_type=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "Python")
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        resp = urllib.request.urlopen(req)
        body = resp.read()
        if not body:
            return None
        return json.loads(body)
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return None
        raise

# Get release
print("Fetching release...")
release = api_request(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}")
release_id = release["id"]

# Delete old assets
print("\nDeleting old assets...")
for asset in release.get("assets", []):
    print(f"  Deleting: {asset['name']}")
    try:
        api_request(asset["url"], method="DELETE")
    except Exception as e:
        print(f"  Error: {e}")

# Upload Windows zip
import os
win_path = os.path.join(RELEASE_DIR, "CarDiagnosticApp_Windows.zip")
win_size = os.path.getsize(win_path)
print(f"\nUploading Windows zip ({win_size/1024/1024:.1f} MB)...")

upload_url = release["upload_url"].replace("{?name,label}", "")
win_url = f"{upload_url}?name=CarDiagnosticApp_Windows.zip"
with open(win_path, "rb") as f:
    win_data = f.read()

win_req = urllib.request.Request(win_url, data=win_data, method="POST")
win_req.add_header("Authorization", f"token {TOKEN}")
win_req.add_header("Accept", "application/vnd.github+json")
win_req.add_header("User-Agent", "Python")
win_req.add_header("Content-Type", "application/zip")
resp = urllib.request.urlopen(win_req)
print(f"  OK: {json.loads(resp.read())['name']}")

# Update release body
print("\nUpdating release body...")
body = """## CarDiagnosticApp v1.0.15 (Hotfix 2)

### Исправление краша 0xc000027b (Microsoft.UI.Xaml.dll)

**Корень проблемы:** self-contained Windows App SDK не инициализировал bootstrapper,
из-за чего WinUI не мог найти свои компоненты и падал с `0xc000027b`.

**Исправление:** добавлен `[ModuleInitializer]` с вызовом 
`Bootstrap.Initialize(0x00010007)` до запуска WinUI.

### Другие изменения

- Crash-лог в `%LOCALAPPDATA%\\CarDiagnosticApp\\crash.log`
- Self-Contained .NET Runtime (не требует установки .NET)
- Self-Contained Windows App SDK (не требует отдельной установки WinAppRuntime)

### Загрузки

| Платформа | Файл | Размер |
|-----------|------|--------|
| Windows | `CarDiagnosticApp_Windows.zip` | 104.6 MB |

### Инструкция (Windows)
1. Скачайте и распакуйте `CarDiagnosticApp_Windows.zip`
2. Запустите `CarDiagnosticApp.exe`
3. Никакие runtime/фреймворки не требуются — всё включено
"""

data = json.dumps({"body": body}).encode()
update_req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/{release_id}", data=data, method="PATCH")
update_req.add_header("Authorization", f"token {TOKEN}")
update_req.add_header("Accept", "application/vnd.github+json")
update_req.add_header("User-Agent", "Python")
update_req.add_header("Content-Type", "application/json")
resp = urllib.request.urlopen(update_req)
print(f"  Updated: {json.loads(resp.read())['html_url']}")

print("\nDONE!")
