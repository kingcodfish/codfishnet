from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional
import json, os, secrets, asyncio
from datetime import datetime, timedelta

# ── Load .env file (stdlib only, no extra packages) ───────────────────────────
def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())

_load_env()

app = FastAPI(title="CODFISHNET TERMINAL")

# ── Config ────────────────────────────────────────────────────────────────────
KEYS_FILE      = "private/api_keys.json"
HISTORY_FILE   = "private/chat_history.json"
JOIN_CODE_TTL  = 900    # seconds (15 minutes)
HISTORY_LIMIT  = 200
HISTORY_SHOWN  = 50
CLEAR_INTERVAL = os.environ.get("CLEAR_INTERVAL", "daily")

ADMIN_SECRET = os.environ.get("CODFISHNET_ADMIN")
if not ADMIN_SECRET:
    raise RuntimeError(
        "CODFISHNET_ADMIN environment variable is not set. "
        "Copy .env.example to .env and set a strong secret."
    )

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "817"))

# ── Runtime state ─────────────────────────────────────────────────────────────
active_connections:  dict[str, WebSocket] = {}   # api_key   → chat WebSocket
pending_connections: dict[str, WebSocket] = {}   # join_code → waiting page WebSocket
api_keys:            dict[str, dict]      = {}
join_codes:          dict[str, dict]      = {}   # code → {api_key, expires}
chat_history:        list[dict]           = []
last_cleared:        datetime             = datetime.now()
heartbeat_times:     dict[str, datetime]  = {}   # api_key → last heartbeat time

# ── Persistence ───────────────────────────────────────────────────────────────
def _load_keys():
    global api_keys
    if os.path.exists(KEYS_FILE):
        with open(KEYS_FILE) as f:
            api_keys = json.load(f)

def save_keys():
    with open(KEYS_FILE, "w") as f:
        json.dump(api_keys, f, indent=2)

def _load_history():
    global chat_history, last_cleared
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            data = json.load(f)
        chat_history = data.get("messages", [])
        try:
            last_cleared = datetime.fromisoformat(
                data.get("last_cleared", datetime.now().isoformat())
            )
        except ValueError:
            last_cleared = datetime.now()

def save_history():
    with open(HISTORY_FILE, "w") as f:
        json.dump(
            {"messages": chat_history[-HISTORY_LIMIT:],
             "last_cleared": last_cleared.isoformat()},
            f, indent=2
        )

_load_keys()
_load_history()

# ── Admin auth ────────────────────────────────────────────────────────────────
def require_admin(x_admin_secret: Optional[str] = Header(None)):
    if x_admin_secret != ADMIN_SECRET:
        raise HTTPException(403, "Admin access denied")

# ── Request models ────────────────────────────────────────────────────────────
class LoginBody(BaseModel):
    join_code: str
    api_key:   str

class SendBody(BaseModel):
    text: str
    key:  str

class LogoutBody(BaseModel):
    key: str

class GenerateKeyBody(BaseModel):
    name: str = "ghost"
    one_time_use: bool = False
    expires_at: str | None = None  # ISO datetime string

# ── Background task ───────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    asyncio.create_task(_background_loop())

async def _background_loop():
    global last_cleared
    while True:
        await asyncio.sleep(15)
        now = datetime.now()

        # Expire join codes and close any page still waiting on one
        expired = [c for c, d in list(join_codes.items()) if now > d["expires"]]
        for c in expired:
            join_codes.pop(c, None)
            if c in pending_connections:
                try:
                    await pending_connections[c].close(4002)
                except Exception:
                    pass
                pending_connections.pop(c, None)

        # Close any WebSocket whose key was revoked (fallback if close failed at revoke time)
        orphaned = [k for k in list(active_connections) if k not in api_keys]
        for k in orphaned:
            ws = active_connections.pop(k, None)
            heartbeat_times.pop(k, None)
            if ws:
                try:
                    await ws.send_json({"type": "disconnected", "reason": "revoked"})
                except Exception:
                    pass
                try:
                    await ws.close(4003)
                except Exception:
                    pass

        # Heartbeat staleness check — disconnect keys with no heartbeat in >30s
        stale_keys = [
            k for k, t in list(heartbeat_times.items())
            if (now - t).total_seconds() > 30 and k in active_connections
        ]
        for k in stale_keys:
            name = api_keys.get(k, {}).get("name", k)
            await _close_user_session(k, name=name, reason="timeout",
                                      broadcast_msg=f"{name} lost connection")

        # Expire timed API keys
        expired_keys = [
            k for k, v in list(api_keys.items())
            if v.get("expires_at") and now > datetime.fromisoformat(v["expires_at"])
        ]
        for k in expired_keys:
            name = api_keys[k]["name"]
            api_keys.pop(k, None)
            save_keys()
            await _close_user_session(k, name=name, reason="expired",
                                      broadcast_msg=f"{name}'s access has expired")

        # Auto-clear history
        delta = (now - last_cleared).total_seconds()
        if (CLEAR_INTERVAL == "daily" and delta >= 86_400) or \
           (CLEAR_INTERVAL == "weekly" and delta >= 604_800):
            chat_history.clear()
            last_cleared = now
            save_history()

# ── HTML pages ────────────────────────────────────────────────────────────────
@app.get("/download/cli")
async def download_cli():
    return FileResponse("public/cli.py", filename="codfishnet_cli.py",
                        media_type="text/plain")

@app.get("/", response_class=HTMLResponse)
@app.get("/chat", response_class=HTMLResponse)
async def chat_page():
    with open("public/chat.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    with open("public/admin.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

# ── Auth flow ─────────────────────────────────────────────────────────────────
@app.get("/get_join_code")
async def get_join_code():
    code = secrets.token_hex(4).upper()
    join_codes[code] = {
        "api_key": None,
        "expires": datetime.now() + timedelta(seconds=JOIN_CODE_TTL),
    }
    return {"join_code": code, "expires_in": JOIN_CODE_TTL}

@app.post("/validate_login")
async def validate_login(body: LoginBody):
    """
    CLI posts {join_code, api_key}. On success, the server pushes the key
    through the pending WebSocket so the browser page unlocks itself in-place.
    The CLI no longer needs to open a browser tab.
    """
    code = body.join_code.upper().strip()

    if code not in join_codes:
        raise HTTPException(400, "Invalid or expired join code")
    if datetime.now() > join_codes[code]["expires"]:
        join_codes.pop(code, None)
        raise HTTPException(400, "Join code has expired")
    if body.api_key not in api_keys:
        raise HTTPException(403, "Unknown API key")

    key_data = api_keys[body.api_key]
    if key_data.get("expires_at") and datetime.now() > datetime.fromisoformat(key_data["expires_at"]):
        raise HTTPException(403, "API key has expired")

    # Push the key to the page that's showing this join code
    if code in pending_connections:
        try:
            await pending_connections[code].send_json({
                "type": "auth",
                "key":  body.api_key,
                "name": api_keys[body.api_key]["name"],
            })
        except Exception:
            pass
        pending_connections.pop(code, None)

    # Consume the join code immediately — it's single-use
    join_codes.pop(code, None)

    return {"status": "ok", "name": api_keys[body.api_key]["name"]}

# ── Pending WebSocket ─────────────────────────────────────────────────────────
@app.websocket("/ws/pending")
async def pending_ws(websocket: WebSocket, code: str):
    """
    The chat page opens this socket immediately after displaying its join code.
    It waits silently until /validate_login sends an 'auth' event, at which
    point the page switches to live chat mode on its own.
    """
    code = code.upper()
    if code not in join_codes:
        await websocket.close(4004)
        return

    await websocket.accept()
    pending_connections[code] = websocket

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pending_connections.pop(code, None)

# ── Chat WebSocket ─────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, key: str | None = None):
    if not key or key not in api_keys:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    active_connections[key] = websocket
    name = api_keys[key]["name"]

    await websocket.send_json({"type": "history", "messages": chat_history[-HISTORY_SHOWN:]})
    await _broadcast(_sys_msg(f"{name} connected to relay"), exclude=key)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_connections.pop(key, None)
        heartbeat_times.pop(key, None)
        await _broadcast(_sys_msg(f"{name} left the relay"))
        if api_keys.get(key, {}).get("one_time_use"):
            api_keys.pop(key, None)
            save_keys()

# ── Helpers ───────────────────────────────────────────────────────────────────
async def _broadcast(msg: dict, exclude: str | None = None):
    for k, ws in list(active_connections.items()):
        if k == exclude:
            continue
        try:
            await ws.send_json(msg)
        except Exception:
            active_connections.pop(k, None)

def _sys_msg(text: str) -> dict:
    return {"type": "system", "time": datetime.now().strftime("%H:%M:%S"), "text": text}

async def _close_user_session(key: str, name: str | None = None,
                               reason: str = "disconnected",
                               broadcast_msg: str | None = None):
    """Send a reconnect code to the user's WebSocket then close it cleanly."""
    ws = active_connections.get(key)
    heartbeat_times.pop(key, None)
    if not ws:
        return
    if name is None:
        name = api_keys.get(key, {}).get("name", "unknown")

    # Generate a fresh join code only when the key is still valid (not revoked/expired)
    payload: dict = {"type": "disconnected", "reason": reason}
    if key in api_keys:
        reconnect = secrets.token_hex(4).upper()
        join_codes[reconnect] = {
            "api_key": None,
            "expires": datetime.now() + timedelta(seconds=JOIN_CODE_TTL),
        }
        payload["join_code"] = reconnect

    try:
        await ws.send_json(payload)
    except Exception:
        pass
    try:
        await ws.close(4000)
    except Exception:
        pass

    active_connections.pop(key, None)
    msg_text = broadcast_msg if broadcast_msg is not None else f"{name} left the relay"
    await _broadcast(_sys_msg(msg_text))

# ── Messaging ─────────────────────────────────────────────────────────────────
@app.post("/send")
async def send_message(body: SendBody):
    if body.key not in api_keys:
        raise HTTPException(403, "Access Denied")
    if body.key not in active_connections:
        raise HTTPException(403, "Not connected to relay")
    key_data = api_keys[body.key]
    if key_data.get("expires_at") and datetime.now() > datetime.fromisoformat(key_data["expires_at"]):
        raise HTTPException(403, "API key has expired")
    if not body.text.strip():
        raise HTTPException(400, "Message cannot be empty")

    msg = {
        "type":   "message",
        "time":   datetime.now().strftime("%H:%M:%S"),
        "sender": api_keys[body.key]["name"],
        "text":   body.text.strip(),
    }
    chat_history.append(msg)
    save_history()
    await _broadcast(msg)
    return {"status": "sent"}

# ── Logout ────────────────────────────────────────────────────────────────────
@app.post("/logout")
async def logout(body: LogoutBody):
    if body.key not in api_keys:
        raise HTTPException(403, "Unknown API key")
    await _close_user_session(body.key, reason="logged_out")
    return {"status": "ok"}

# ── Heartbeat ─────────────────────────────────────────────────────────────────
@app.post("/heartbeat")
async def heartbeat(body: LogoutBody):
    if body.key not in api_keys:
        raise HTTPException(403, "Unknown API key")
    heartbeat_times[body.key] = datetime.now()
    return {"status": "ok"}

# ── Admin: keys ───────────────────────────────────────────────────────────────
@app.post("/generate_key", dependencies=[Depends(require_admin)])
async def generate_key(body: GenerateKeyBody):
    name = body.name.strip() or "ghost"
    key  = secrets.token_hex(16)

    expires_at = None
    if body.expires_at:
        raw = body.expires_at.strip()
        # Normalise YYYY-MM-DDTHH:MM (no seconds) → YYYY-MM-DDTHH:MM:00
        if len(raw) == 16:
            raw = raw + ":00"
        expires_at = datetime.fromisoformat(raw).isoformat()

    api_keys[key] = {
        "name": name,
        "created": datetime.now().isoformat(),
        "one_time_use": body.one_time_use,
        "expires_at": expires_at,
    }
    save_keys()
    return {"key": key, "name": name}

@app.delete("/revoke_key/{key}", dependencies=[Depends(require_admin)])
async def revoke_key(key: str):
    if key not in api_keys:
        raise HTTPException(404, "Key not found")
    name = api_keys.pop(key)["name"]
    save_keys()
    # Key is now gone → _close_user_session won't generate a reconnect code
    await _close_user_session(key, name=name, reason="revoked",
                               broadcast_msg=f"{name}'s access was revoked")
    return {"status": "revoked", "name": name}

@app.get("/list_keys", dependencies=[Depends(require_admin)])
async def list_keys():
    return {"keys": [
        {
            "key": k,
            "name": v["name"],
            "created": v["created"],
            "online": k in active_connections,
            "one_time_use": v.get("one_time_use", False),
            "expires_at": v.get("expires_at"),
        }
        for k, v in api_keys.items()
    ]}

# ── Admin: join codes ─────────────────────────────────────────────────────────
@app.delete("/revoke_join_code/{code}", dependencies=[Depends(require_admin)])
async def revoke_join_code(code: str):
    code = code.upper()
    if code not in join_codes:
        raise HTTPException(404, "Join code not found")
    join_codes.pop(code)
    if code in pending_connections:
        try:
            await pending_connections[code].close(4002)
        except Exception:
            pass
        pending_connections.pop(code, None)
    return {"status": "revoked"}

@app.get("/list_sessions", dependencies=[Depends(require_admin)])
async def list_sessions():
    now = datetime.now()
    return {
        "join_codes": [
            {"code": c, "expires_in": max(0, int((d["expires"] - now).total_seconds())),
             "claimed": d["api_key"] is not None}
            for c, d in join_codes.items()
        ],
        "online_users": [
            {"name": api_keys[k]["name"], "key_preview": k[:8] + "...", "key": k}
            for k in active_connections if k in api_keys
        ],
    }

@app.post("/kick/{key}", dependencies=[Depends(require_admin)])
async def kick_user(key: str):
    if key not in active_connections:
        raise HTTPException(404, "User not connected")
    name = api_keys.get(key, {}).get("name", key)
    await _close_user_session(key, name=name, reason="kicked",
                               broadcast_msg=f"{name} was disconnected by admin")
    return {"status": "ok", "name": name}

# ── Admin: history ────────────────────────────────────────────────────────────
@app.get("/history", dependencies=[Depends(require_admin)])
async def get_history():
    return {"messages": chat_history, "last_cleared": last_cleared.isoformat()}

@app.delete("/clear_history", dependencies=[Depends(require_admin)])
async def clear_history_endpoint():
    global last_cleared
    chat_history.clear()
    last_cleared = datetime.now()
    save_history()
    return {"status": "cleared"}

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)