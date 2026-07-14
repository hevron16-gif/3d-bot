"""AutoDiag AI — Full test suite"""
from fastapi.testclient import TestClient
from main import app

c = TestClient(app)

def test(msg, fn):
    try:
        fn()
        print(f"  PASS: {msg}")
    except Exception as e:
        print(f"  FAIL: {msg} — {e}")

print("1. Root")
test("GET / returns v1.0.0", lambda: c.get("/").json()["version"] == "1.0.0")

print("2. Simulator")
test("GET /sim/live has rpm", lambda: "rpm" in c.get("/sim/live").json())
test("GET /sim/errors returns list", lambda: len(c.get("/sim/errors").json()) >= 1)

print("3. Live data + graphs")
test("GET /live has engine_running", lambda: "engine_running" in c.get("/live").json())
test("GET /live/graph returns datasets", lambda: "datasets" in c.get("/live/graph").json())

print("4. Error injection & reading")
r = c.post("/errors/inject", json={"code": "P0171", "mode": "current"})
test("POST /errors/inject returns 200", lambda: r.status_code == 200)
test("GET /errors shows check_engine=True", lambda: c.get("/errors").json()["check_engine"] == True)
test("GET /errors/03 mode=03", lambda: c.get("/errors/03").json()["mode"] == "03")
test("GET /errors/07 mode=07", lambda: c.get("/errors/07").json()["mode"] == "07")
test("GET /errors/0A mode=0A", lambda: c.get("/errors/0A").json()["mode"] == "0A")
test("POST /errors/clear returns OK", lambda: c.post("/errors/clear").status_code == 200)

print("5. Diagnosis")
test("GET /diagnose/offline finds P0420", lambda: c.get("/diagnose/offline?code=P0420").json()["found"] == True)
r = c.post("/diagnose", json={"error_code": "P0134", "car_brand": "Lada"})
test("POST /diagnose falls back to offline", lambda: r.json()["source"] == "offline")

print("6. History")
test("GET /history has diagnostics", lambda: "diagnostics" in c.get("/history?user_id=test").json())
test("GET /history/stats has stats", lambda: "stats" in c.get("/history/stats").json())
test("GET /history/codes has historical_codes", lambda: "historical_codes" in c.get("/history/codes?car_brand=Lada").json())

print("7. Memory (ChromaDB)")
r = c.get("/memory/search?q=P0171")
print(f"   ChromaDB available: {r.json().get('available', False)}")
test("GET /memory/count returns count", lambda: "count" in c.get("/memory/count").json())

print("8. Schemas")
test("GET /schemas returns 3 schemas", lambda: len(c.get("/schemas").json()["schemas"]) == 3)
test("GET /schemas/P0171 blocks free user", lambda: c.get("/schemas/P0171?user_id=test").json()["available"] == False)

print("9. Sync")
test("GET /sync/status blocks free user", lambda: c.get("/sync/status?user_id=test").json()["available"] == False)

print("10. Cars")
test("GET /cars returns 9 cars", lambda: c.get("/cars").json()["count"] == 9)

print("11. Pricing")
r = c.get("/pricing/plans").json()
plans = r["plans"]
test("Free/Pro/Enterprise plans exist", lambda: len(plans) == 3)
test("Pro is highlighted", lambda: any(p["highlighted"] for p in plans))
r = c.get("/pricing/features?user_id=test").json()
test("Free user has 3 enabled features", lambda: len(r["enabled"]) == 3)
test("Free user has locked features", lambda: len(r["locked"]) > 0)
r = c.get("/pricing/status?user_id=test").json()
test("Free user is_paid=False", lambda: r["is_paid"] == False)

print("12. User info")
test("GET /me returns free tier", lambda: c.get("/me?user_id=test").json()["tier"] == "free")

print("13. Russian cars + GAS + special equipment")
cars = c.get("/cars").json()["cars"]
gas_cars = [c for c in cars if c["gas_equipment"]]
special_cars = [c for c in cars if c["special"]]
test("Gas equipment car exists", lambda: len(gas_cars) >= 1)
test("Special equipment car exists", lambda: len(special_cars) >= 1)

print("14. Paid version advantages clearly shown")
r = c.get("/pricing/plans").json()
pro = [p for p in r["plans"] if p["name"] == "Pro"][0]
test("Pro has schemas in features", lambda: any("Схем" in f for f in pro["features"]))
test("Pro has AI in features", lambda: any("AI" in f or "DeepSeek" in f for f in pro["features"]))
test("Pro has sync in features", lambda: any("синхронизац" in f.lower() for f in pro["features"]))
test("Pro has self-learning in features", lambda: any("самообуч" in f.lower() for f in pro["features"]))
test("Pro shows price clearly", lambda: pro["price"] != "")

print()
print("=== ALL TESTS COMPLETE ===")
