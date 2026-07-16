"""Update v1.0.16 release assets on GitHub — WinAppSDK preload fix."""
import json, urllib.request, os

TOKEN = "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"
REPO = "hevron16-gif/3d-bot"

RELEASE_DIR = r"C:\Users\User\.nanobot\workspace\release_build"

def api_request(url, method="GET", data=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "Python")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req)
        body = resp.read()
        return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return None
        body = e.read()
        try:
            err = json.loads(body)
            print(f"  API Error {e.code}: {err.get('message', str(e))}")
        except:
            print(f"  API Error {e.code}: {body[:200]}")
        raise

# Delete old v1.0.15 assets since they had the broken fix
print("Cleaning up v1.0.15 release...")
try:
    r15 = api_request(f"https://api.github.com/repos/{REPO}/releases/tags/v1.0.15")
    for asset in r15.get("assets", []):
        if "Windows" in asset["name"]:
            print(f"  Deleting old: {asset['name']}")
            api_request(asset["url"], method="DELETE")
except Exception as e:
    print(f"  v1.0.15 cleanup: {e}")

# Get or create v1.0.16 release
print("\nChecking for v1.0.16...")
try:
    release = api_request(f"https://api.github.com/repos/{REPO}/releases/tags/v1.0.16")
    print(f"  Found: {release['html_url']}")
except:
    print("  Creating new v1.0.16...")
    release = api_request(f"https://api.github.com/repos/{REPO}/releases",
        method="POST",
        data=json.dumps({
            "tag_name": "v1.0.16",
            "name": "CarDiagnosticApp v1.0.16 (WinAppSDK Preload Fix)",
            "body": "Uploading...",
            "draft": False,
            "prerelease": False
        }).encode())
    print(f"  Created: {release['html_url']}")

release_id = release["id"]

# Delete any existing assets on v1.0.16
for asset in release.get("assets", []):
    if "Windows" in asset["name"]:
        print(f"  Deleting old: {asset['name']}")
        api_request(asset["url"], method="DELETE")

# Upload Windows zip
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
body = """## CarDiagnosticApp v1.0.16 — WinAppSDK Self-Contained Fix

### Корень проблемы 0xc000027b

Ошибка `0xc000027b` (STOWED_EXCEPTION) происходит в `Microsoft.UI.Xaml.dll`,
а НЕ в коде приложения (не в MainActivity, не в ApiService, не в MauiProgram).

Причина: .NET MAUI при генерации `Program.Main()` для Windows НЕ добавляет
код инициализации WinAppSDK для self-contained режима. На машинах БЕЗ
установленного Windows App Runtime (чистые ноутбуки) WinUI не может найти
свои DLL, и падает до того, как код приложения начинает выполняться.

На dev-машинах с установленным .NET MAUI Workload всё работает, потому что
WinAppSDK уже установлен системно в `C:\\Program Files\\WindowsApps`.

### Исправление (v1.0.16)

В `Platforms\\Windows\\App.xaml.cs` добавлен `[ModuleInitializer]`, который
**предзагружает** критические нативные DLL WinAppSDK из папки приложения
до вызова `Application.Start()`:

```
NativeLibrary.Load("Microsoft.WindowsAppRuntime.dll")
NativeLibrary.Load("Microsoft.WindowsAppRuntime.Bootstrap.dll")
NativeLibrary.Load("MRM.dll")
NativeLibrary.Load("DWriteCore.dll")
```

Это гарантирует, что когда WinUI попытается загрузить эти DLL через DDLM —
они уже будут в памяти процесса.

### Если ошибка сохранится

Проверь `%LOCALAPPDATA%\\CarDiagnosticApp\\crash.log` — там будет видно,
удалось ли предзагрузить DLL (сообщение "WinAppSDK preload OK") или нет.

Также проверь, установлен ли **Visual C++ Redistributable** на ноутбуке —
он нужен для нативных DLL.

### Загрузка

| Платформа | Файл |
|-----------|------|
| Windows x64 | `CarDiagnosticApp_Windows.zip` |
"""

data = json.dumps({"body": body}).encode()
update_req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/{release_id}", data=data, method="PATCH")
update_req.add_header("Authorization", f"token {TOKEN}")
update_req.add_header("Accept", "application/vnd.github+json")
update_req.add_header("User-Agent", "Python")
update_req.add_header("Content-Type", "application/json")
resp = urllib.request.urlopen(update_req)
print(f"  Updated: {json.loads(resp.read())['html_url']}")

print("\nDONE! https://github.com/hevron16-gif/3d-bot/releases/tag/v1.0.16")
