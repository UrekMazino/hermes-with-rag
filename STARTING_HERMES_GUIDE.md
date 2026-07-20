# Starting the Hermes service (manual)

How to bring the whole thing back up after a reboot. **Nothing auto-starts** — you start these by hand.
(The `docs` MCP search server is launched **automatically** by the gateway — no separate step.)

**Which do you actually need?**
- Discord bot (**Lucy**) → **1 + 2**
- eLibrary **“AI mode”** (the OPAC AI search) → **1 + 3**
- Both → all three

## What runs
| # | Service | Script | Port | What it is |
|---|---------|--------|------|------------|
| 1 | **llama-server** | `start-llama-server.ps1` | 8080 | The local Qwen model. The brain — **both** the bot and AI mode need it. |
| 2 | **Hermes gateway** | `start-hermes-gateway.ps1` | — | The Discord bot (**Lucy**), running the `locked-rag` profile. |
| 3 | **RAG API** | `start-rag-api.ps1` | 8090 | Serves the eLibrary **“AI mode”**. Laravel is its only caller. **Not needed for Discord.** |
| — | docs MCP (search_docs) | *(auto)* | — | Spawned by the gateway on first search; loads the embeddings (~12 s). |

> **AI mode chain:** eLibrary (Laravel) → **rag_api `:8090`** → **llama-server `:8080`**.
> If either is down, the OPAC shows *“AI mode is temporarily unavailable.”*

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

**2. Start the model first** (wait ~15 s for it to load before steps 3–4 — both depend on it):
```powershell
.\start-llama-server.ps1
```

**3. Start the Discord bot** *(skip if you only need eLibrary AI mode)*:
```powershell
.\start-hermes-gateway.ps1
```

**4. Start the eLibrary AI gateway** *(required for **AI mode** — skip if you only use Discord)*:
```powershell
.\start-rag-api.ps1
```
> Runs in the **foreground — leave that window open.** It picks up `HERMES_GATEWAY_TOKEN` from the
> eLibrary `.env` automatically, and needs llama-server (step 2) to synthesise answers.
> Use a **separate PowerShell window** from the gateway (each of these blocks its terminal).

**5. Verify what you started:**
```powershell
curl http://127.0.0.1:8080/v1/models   # llama-server: should return JSON (not an error)
curl http://127.0.0.1:8090/healthz     # RAG API (AI mode): should return JSON, not an error
hermes gateway status                  # should say: Gateway is running
hermes profile list                    # ◆ locked-rag, running
```

That's it — Lucy is back on Discord, and the eLibrary AI mode works again.

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
# stop the RAG API (AI mode): press Ctrl+C in ITS window
# stop the model:
taskkill /IM llama-server.exe /F
```
Restart = just re-run steps 2–4. (The `locked-rag` profile is sticky — you never need to re-select it.)

> **Tired of doing this every reboot?** These can be registered as logon scheduled tasks so they come
> back on their own — see `_setup/register_logon_tasks.ps1` / `_setup/register_gateway_task.ps1` for the
> pattern used elsewhere (e.g. `register-ocr-task.ps1`). Nothing is registered today, which is why a
> restart always leaves AI mode down until you run step 4.

---

## Troubleshooting
- **eLibrary says “AI mode is temporarily unavailable”** → the **RAG API (port 8090) is not running.**
  It is a *separate* service from the Discord bot, it does **not** auto-start, and the older version of
  this guide didn't mention it — this is the #1 cause after a reboot.
  1. Check it: `curl http://127.0.0.1:8090/healthz` → unreachable means it's down.
  2. Start it: `.\start-rag-api.ps1` (keep that window open), then reload the OPAC page.
  3. Still failing? Check the other half of the chain: `curl http://127.0.0.1:8080/v1/models`.
     AI mode needs **both** — Laravel → `rag_api :8090` → `llama-server :8080`.
  4. `Refusing to start open` on launch → `HERMES_GATEWAY_TOKEN` wasn't found. The script reads it from
     `C:\laragon\www\elibrary\.env`; make sure that line exists and matches what Laravel uses.
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
