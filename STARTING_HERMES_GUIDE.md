# Starting the Hermes service (manual)

How to bring the whole thing back up after a reboot. **Nothing auto-starts** — you start two things by
hand. (A third piece, the `docs` MCP search server, is launched **automatically** by the gateway — no
separate step.)

## What runs
| # | Service | Script | What it is |
|---|---------|--------|------------|
| 1 | **llama-server** | `start-llama-server.ps1` | The local Qwen model (port 8080). The bot's brain. |
| 2 | **Hermes gateway** | `start-hermes-gateway.ps1` | The Discord bot (**Lucy**), running the `locked-rag` profile. |
| — | docs MCP (search_docs) | *(auto)* | Spawned by the gateway on first search; loads the embeddings (~12 s). |

> The bot on Discord = the **`locked-rag`** profile: a locked-down **corpus-search bot** (it can only
> search your research documents and reply — no files/terminal/web). That's by design.

---

## Steps after a reboot

**1. Open a fresh PowerShell in the project folder** (your OWN terminal — Windows Terminal or VS Code
Terminal). *Not* from inside Claude Code, or the bot picks up the wrong persona.
```powershell
cd C:\Users\jcvia\PyCharmMiscProject\ProjectY
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
```

**2. Start the model first** (wait ~15 s for it to load before step 3):
```powershell
.\start-llama-server.ps1
```

**3. Start the Discord bot:**
```powershell
.\start-hermes-gateway.ps1
```

**4. Verify both are up:**
```powershell
curl http://127.0.0.1:8080/v1/models   # llama-server: should return JSON (not an error)
hermes gateway status                  # should say: Gateway is running
hermes profile list                    # ◆ locked-rag, running
```

That's it — Lucy is back online on Discord.

---

## Test it
On **Discord** (the locked bot only answers about your corpus):
- `How many chunks are in my local document index?`  → should reply **353,604**
- `According to my documents, how many does per breed were monitored in the goat study?`  → cited answer

> First corpus question after a fresh start is slow (~15–30 s — the search model cold-loads). Ask it once
> more if it times out. It tries Opus → OpenAI (both unfunded, so they fail fast) → then answers on **local
> Qwen**, so llama-server MUST be up.

**Optional — interactive test in the terminal** (also runs the `locked-rag` profile = corpus-only):
```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\jcvia\PyCharmMiscProject\ProjectY\start-hermes.ps1"
```
Type `/exit` (or Ctrl+C) to quit.

---

## Stop / restart
```powershell
hermes gateway stop            # stop the Discord bot
# stop the model:
taskkill /IM llama-server.exe /F
```
Restart = just re-run steps 2–3. (The `locked-rag` profile is sticky — you never need to re-select it.)

---

## Troubleshooting
- **Bot is silent on Discord** → 99% of the time it's one of two things:
  1. **llama-server isn't up** — `curl http://127.0.0.1:8080/v1/models`; if it errors, run `.\start-llama-server.ps1`.
  2. **Pairing** (only after profile changes) — `hermes pairing list` should show your user *approved*.
- **`... cannot be loaded because running scripts is disabled`** → you skipped the
  `Set-ExecutionPolicy -Scope Process ... Bypass` line (it only lasts for that terminal session).
- **Persona looks like "Claude Code" instead of Hermes** → the gateway was launched from inside Claude
  Code. Stop it and relaunch from your own PowerShell.
- **Two gateway PIDs** is normal (parent + worker = ONE gateway). Don't `taskkill /T` it — use
  `hermes gateway stop`.

## Switch back to the full assistant (if ever needed)
```powershell
hermes profile use default
hermes gateway stop
.\start-hermes-gateway.ps1
```
Undo with `hermes profile use locked-rag` + restart. (See `HERMES_SECURITY_LOCKDOWN_2026-07-09.md` for
what each profile is.)

---

Her name is Lucy! 💛
