import shutil
from datetime import datetime
from pathlib import Path
from ruamel.yaml import YAML

p = Path.home() / "AppData" / "Local" / "hermes" / "config.yaml"
bak = p.with_suffix(".yaml.bak.aux." + datetime.now().strftime("%Y%m%d_%H%M%S"))
shutil.copy2(p, bak)

yaml = YAML()
yaml.preserve_quotes = True
data = yaml.load(p.read_text(encoding="utf-8"))

aux = data.get("auxiliary") or {}
changed = []
for name, sub in aux.items():
    if isinstance(sub, dict) and "provider" in sub:
        sub["provider"] = "custom"
        sub["model"] = "qwen3-30b"
        sub["base_url"] = "http://127.0.0.1:8080/v1"
        sub["api_key"] = "sk-local"
        changed.append(name)

with p.open("w", encoding="utf-8") as f:
    yaml.dump(data, f)

print("backup:", bak.name)
print("updated auxiliary entries (%d):" % len(changed), ", ".join(changed))
