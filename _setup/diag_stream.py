import glob, json, os, urllib.request, urllib.error

home = os.path.expandvars(r"%LOCALAPPDATA%\hermes\sessions")
dump = sorted(glob.glob(os.path.join(home, "request_dump_*.json")), key=os.path.getmtime)[-1]
print("dump:", os.path.basename(dump))
body = json.load(open(dump, encoding="utf-8"))["request"]["body"]
URL = "http://127.0.0.1:8080/v1/chat/completions"

def post(payload, label):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(URL, data=data, headers={"Content-Type": "application/json", "Authorization": "Bearer sk-local"})
    try:
        r = urllib.request.urlopen(req, timeout=180)
        chunks = 0
        for line in r:
            if line.strip():
                chunks += 1
        print(f"[{label}] HTTP {r.status} OK, {chunks} sse lines")
    except urllib.error.HTTPError as e:
        print(f"[{label}] HTTP {e.code}: {e.read().decode()[:300]} <<< FAIL")
    except Exception as e:
        print(f"[{label}] {type(e).__name__}: {e} <<< FAIL")

# Replay with streaming, several times (intermittent)
for i in range(6):
    b = dict(body); b["stream"] = True; b["stream_options"] = {"include_usage": True}
    post(b, f"stream run {i+1}")
