"""Upload assets to existing GitHub release."""
import json, os, urllib.request, mimetypes
import urllib.parse

GITHUB_TOKEN = "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"
REPO = "hevron16-gif/3d-bot"
RELEASE_ID = "354176707"
ASSETS_DIR = "release_build"

def gh_request(method, url, data=None, content_type=None):
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "User-Agent": "AutoDiagAI/1.0",
        "Accept": "application/vnd.github.v3+json",
    }
    if content_type:
        headers["Content-Type"] = content_type

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=120)
        return json.loads(r.read()) if r.status != 204 else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  API ERROR {e.code}: {body[:300]}")
        raise

# First, get the release to find upload_url
print("Getting release info...")
release = gh_request("GET", f"https://api.github.com/repos/{REPO}/releases/{RELEASE_ID}")
upload_url = release["upload_url"]
print(f"Upload URL template: {upload_url}")

# Strip the template part {?name,label}
upload_url = upload_url.replace("{?name,label}", "")

# Delete existing assets if any
print("\nChecking existing assets...")
for a in release.get("assets", []):
    print(f"  Deleting old: {a['name']}")
    gh_request("DELETE", f"https://api.github.com/repos/{REPO}/releases/assets/{a['id']}")

# Upload new assets
assets = [
    "CarDiagnosticApp_Windows.zip",
    "CarDiagnosticApp.apk",
    "CarDiagnosticServer_v1.0.10.zip",
]

for asset_name in assets:
    asset_path = os.path.join(ASSETS_DIR, asset_name)
    if not os.path.exists(asset_path):
        print(f"  SKIP: {asset_name}")
        continue

    sz = os.path.getsize(asset_path)
    print(f"\nUploading {asset_name} ({sz/1024/1024:.1f} MB)...")

    with open(asset_path, "rb") as fh:
        data = fh.read()

    mime, _ = mimetypes.guess_type(asset_name)
    mime = mime or "application/octet-stream"

    # Proper upload URL
    asset_url = f"{upload_url}?name={urllib.parse.quote(asset_name)}"
    print(f"  URL: {asset_url[:80]}...")

    result = gh_request("POST", asset_url, data=data, content_type=mime)
    print(f"  OK: {result.get('browser_download_url', '?')}")
    print(f"  State: {result.get('state')}")

print("\nDone! Verifying...")
release = gh_request("GET", f"https://api.github.com/repos/{REPO}/releases/{RELEASE_ID}")
print(f"Assets: {len(release.get('assets', []))}")
for a in release.get("assets", []):
    print(f"  {a['name']}: {a.get('size', 0)} bytes")

print(f"\nRelease URL: https://github.com/hevron16-gif/3d-bot/releases/tag/v1.0.10")
