"""Update GitHub release body for v1.0.15."""
import json, urllib.request

TOKEN = "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"
REPO = "hevron16-gif/3d-bot"
TAG = "v1.0.15"

# Get release first
get_url = f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}"
get_req = urllib.request.Request(get_url)
get_req.add_header("Authorization", f"token {TOKEN}")
get_req.add_header("Accept", "application/vnd.github+json")
get_req.add_header("User-Agent", "Python")
resp = urllib.request.urlopen(get_req)
release = json.loads(resp.read())

# Update body
update_url = f"https://api.github.com/repos/{REPO}/releases/{release['id']}"
body = """## CarDiagnosticApp v1.0.15

### Ключевые изменения

- **Self-Contained Windows сборка**: приложение включает .NET runtime (278 MB), установка .NET не требуется — просто распакуйте и запустите
- **Исправлен краш на Windows**: PlatformPermissionService теперь корректно обёрнут в `#if ANDROID`, что устранило падение при запуске на Windows
- Убрано требование установленного .NET 10 Runtime для Windows-сборки

### Загрузки

| Платформа | Файл | Размер |
|-----------|------|--------|
| Android | `CarDiagnosticApp.apk` | 52.5 MB |
| Windows | `CarDiagnosticApp_Windows.zip` | 104.6 MB |

### Инструкция (Windows)
1. Скачайте и распакуйте `CarDiagnosticApp_Windows.zip`
2. Запустите `CarDiagnosticApp.exe`
3. .NET Runtime не требуется — всё включено в архив
"""

data = json.dumps({"body": body}).encode()
update_req = urllib.request.Request(update_url, data=data, method="PATCH")
update_req.add_header("Authorization", f"token {TOKEN}")
update_req.add_header("Accept", "application/vnd.github+json")
update_req.add_header("User-Agent", "Python")
update_req.add_header("Content-Type", "application/json")

try:
    resp = urllib.request.urlopen(update_req)
    result = json.loads(resp.read())
    print(f"Updated: {result['html_url']}")
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()}")
