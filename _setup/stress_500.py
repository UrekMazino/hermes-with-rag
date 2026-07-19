import json, os, urllib.request, urllib.error, time

URL = "http://127.0.0.1:8080/v1/chat/completions"
tools = [{"type": "function", "function": {
    "name": "list_directory",
    "description": "List entries in a directory",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]

def one(i):
    payload = {
        "model": "qwen3-30b",
        "messages": [{"role": "user", "content": f"Call list_directory on path C:/Users/jcvia/PyCharmMiscProject/ProjectY (request #{i})."}],
        "tools": tools,
        "max_tokens": 8192,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(URL, data=data, headers={"Content-Type": "application/json", "Authorization": "Bearer sk-local"})
    t = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=120)
        j = json.loads(r.read())
        fr = j["choices"][0].get("finish_reason")
        return f"#{i} HTTP {r.status} finish={fr} ({time.time()-t:.1f}s)"
    except urllib.error.HTTPError as e:
        return f"#{i} HTTP {e.code}: {e.read().decode()[:200]} <<< FAIL"
    except Exception as e:
        return f"#{i} {type(e).__name__}: {e} <<< FAIL"

fails = 0
for i in range(1, 16):
    msg = one(i)
    print(msg, flush=True)
    if "FAIL" in msg:
        fails += 1
print(f"\nTOTAL FAILURES: {fails}/15")
