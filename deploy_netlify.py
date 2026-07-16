"""Deploy landing page to Netlify."""
import json, os, urllib.request

TOKEN = "nfp_t4aUbBFq7w4p8kT7HdkquaHbPe9ZSAnXac52"
SITE_ID = "6db08d89-fb07-4aac-8abd-33fd04a106f1"
SITE_DIR = r"C:\Users\User\.nanobot\workspace\site"

# Step 1: Create deploy
url = f"https://api.netlify.com/api/v1/sites/{SITE_ID}/deploys"
data = json.dumps({"draft": False}).encode()

req = urllib.request.Request(url, data=data, method="POST")
req.add_header("Authorization", f"Bearer {TOKEN}")
req.add_header("Content-Type", "application/json")
req.add_header("User-Agent", "Python")

try:
    resp = urllib.request.urlopen(req)
    deploy = json.loads(resp.read())
    deploy_id = deploy["id"]
    print(f"Deploy created: {deploy_id}")
except urllib.error.HTTPError as e:
    print(f"Deploy creation failed: HTTP {e.code}: {e.read().decode()}")
    exit(1)

# Step 2: Upload all files
files = []
for root, dirs, filenames in os.walk(SITE_DIR):
    for filename in filenames:
        filepath = os.path.join(root, filename)
        relpath = os.path.relpath(filepath, SITE_DIR).replace("\\", "/")
        files.append((relpath, filepath))

# Upload in reverse order (css, js, img first, then html last for proper rendering)
for relpath, filepath in files:
    with open(filepath, "rb") as f:
        content = f.read()
    
    file_url = f"https://api.netlify.com/api/v1/deploys/{deploy_id}/files/{relpath}"
    file_req = urllib.request.Request(file_url, data=content, method="PUT")
    file_req.add_header("Authorization", f"Bearer {TOKEN}")
    file_req.add_header("Content-Type", "application/octet-stream")
    file_req.add_header("User-Agent", "Python")
    
    try:
        urllib.request.urlopen(file_req)
        print(f"  OK: {relpath} ({len(content)} bytes)")
    except urllib.error.HTTPError as e:
        print(f"  FAIL {e.code}: {relpath}")
        if e.code == 401:
            print("  Token expired or invalid")
            exit(1)

print(f"\nDone! https://avtodiagnostika-ai.netlify.app")
