"""Build release packages and create GitHub release."""
import json, os, shutil, tempfile, urllib.request, glob

# --- Config ---
GITHUB_TOKEN = "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"
REPO = "hevron16-gif/3d-bot"
TAG = "v1.0.10"
BUILD_DIR = r"C:\Users\User\source\repos\CarDiagnosticApp\installer_build"
OUT_DIR = "release_build"

os.makedirs(OUT_DIR, exist_ok=True)

print("=" * 60)
print("BUILDING RELEASE PACKAGES")
print("=" * 60)

# --- 1. Windows Package ---
print("\n[1/3] Windows package...")
win_zip = os.path.join(OUT_DIR, "CarDiagnosticApp_Windows.zip")

# Extract app.zip
app_zip = os.path.join(BUILD_DIR, "app.zip")
shutil.copy(app_zip, os.path.join(OUT_DIR, "_app.zip"))

# Create temp dir with proper structure
tmp = tempfile.mkdtemp()
shutil.unpack_archive(app_zip, os.path.join(tmp, "app"))

# Copy setup.vbs
shutil.copy(os.path.join(BUILD_DIR, "setup.vbs"), os.path.join(tmp, "setup.vbs"))

# Create final zip
shutil.make_archive(win_zip.replace(".zip", ""), "zip", tmp)
shutil.rmtree(tmp)

sz = os.path.getsize(win_zip)
print(f"  Created: {win_zip} ({sz/1024/1024:.1f} MB)")

# --- 2. Android Package ---
print("\n[2/3] Android package...")
apk_src = r"C:\Users\User\source\repos\CarDiagnosticApp\bin\Release\net10.0-android\com.companyname.cardiognosticapp-Signed.apk"
# Try different paths
apk_paths = [
    r"C:\Users\User\source\repos\CarDiagnosticApp\bin\Release\net10.0-android\publish\com.companyname.cardiagnosticapp-Signed.apk",
    r"C:\Users\User\source\repos\CarDiagnosticApp\bin\Release\net10.0-android\com.companyname.cardiagnosticapp-Signed.apk",
]
# List what we actually have
android_dir = r"C:\Users\User\source\repos\CarDiagnosticApp\bin\Release\net10.0-android"
for f in glob.glob(android_dir + "/**/*.apk", recursive=True):
    print(f"  Found APK: {f} ({os.path.getsize(f)/1024/1024:.1f} MB)")
    apk_paths.insert(0, f)

apk_dest = os.path.join(OUT_DIR, "CarDiagnosticApp.apk")
found = False
for apk_src in apk_paths:
    if os.path.exists(apk_src):
        shutil.copy(apk_src, apk_dest)
        sz = os.path.getsize(apk_dest)
        print(f"  Created: {apk_dest} ({sz/1024/1024:.1f} MB)")
        found = True
        break

if not found:
    print("  WARNING: No APK found! Searching...")
    for root, dirs, files in os.walk(android_dir):
        for f in files:
            if f.endswith(".apk"):
                p = os.path.join(root, f)
                print(f"  Found: {p}")

# --- 3. Server Source Package ---
print("\n[3/3] Server source package...")
server_zip = os.path.join(OUT_DIR, "CarDiagnosticServer_v1.0.10.zip")
workspace = "."

tmp = tempfile.mkdtemp()
server_files = glob.glob("*.py", root_dir=workspace) + [
    "requirements.txt", "requirements_server.txt", "render.yaml",
    "Procfile", "start.sh", "VERSION", ".python-version",
]
for f in server_files:
    if os.path.exists(f):
        dest = os.path.join(tmp, f)
        if os.path.isdir(f):
            shutil.copytree(f, dest)
        else:
            shutil.copy(f, dest)

# Copy schemas/ module
schemas_dir = "schemas"
if os.path.isdir(schemas_dir):
    shutil.copytree(schemas_dir, os.path.join(tmp, "schemas"))

shutil.make_archive(server_zip.replace(".zip", ""), "zip", tmp)
shutil.rmtree(tmp)

sz = os.path.getsize(server_zip)
print(f"  Created: {server_zip} ({sz/1024/1024:.1f} MB)")

print("\n" + "=" * 60)
print("ALL PACKAGES BUILT")
print("=" * 60)

# Print summary
for f in os.listdir(OUT_DIR):
    if f.endswith((".zip", ".apk")):
        sz = os.path.getsize(os.path.join(OUT_DIR, f))
        print(f"  {f}: {sz/1024/1024:.1f} MB")
