import glob, json, os, urllib.request, urllib.error

home = os.path.expandvars(r"%LOCALAPPDATA%\hermes\sessions")
dump = sorted(glob.glob(os.path.join(home, "request_dump_*.json")), key=os.path.getmtime)[-1]
print("dump:", os.path.basename(dump))
d = json.load(open(dump, encoding="utf-8"))
body = d["request"]["body"]
URL = "http://127.0.0.1:8080/v1/chat/completions"

def post(payload, label):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(URL, data=data, headers={"Content-Type": "application/json", "Authorization": "Bearer sk-local"})
    try:
        r = urllib.request.urlopen(req, timeout=120)
        print(f"[{label}] HTTP {r.status} OK")
        return True
    except urllib.error.HTTPError as e:
        print(f"[{label}] HTTP {e.code}: {e.read().decode()[:300]}")
        return False
    except Exception as e:
        print(f"[{label}] ERROR {type(e).__name__}: {e}")
        return False

n_tools = len(body.get("tools", []))
print(f"messages={len(body.get('messages', []))} tools={n_tools} max_tokens={body.get('max_tokens')}")

# A: exact replay
post(dict(body), "A: exact replay")

# B: tools removed
b = dict(body); b.pop("tools", None); b.pop("tool_choice", None)
post(b, "B: no tools")

# C: bisect tools — first half vs second half
if n_tools > 1:
    half = n_tools // 2
    b1 = dict(body); b1["tools"] = body["tools"][:half]
    post(b1, f"C1: first {half} tools")
    b2 = dict(body); b2["tools"] = body["tools"][half:]
    post(b2, f"C2: last {n_tools-half} tools")
