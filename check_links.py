import urllib.request

urls = [
    "https://github.com/hevron16-gif/3d-bot/releases/download/v1.0.10/CarDiagnosticApp_Windows.zip",
    "https://github.com/hevron16-gif/3d-bot/releases/download/v1.0.10/CarDiagnosticApp.apk",
    "https://github.com/hevron16-gif/3d-bot/releases/download/v1.0.10/CarDiagnosticServer_v1.0.10.zip",
]

for url in urls:
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "AutoDiagAI/1.0")
        r = urllib.request.urlopen(req, timeout=10)
        fname = url.split("/")[-1]
        print(f"OK [{r.status}] {fname}")
    except Exception as e:
        print(f"FAIL: {url} — {e}")
