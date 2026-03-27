import json
import threading
import time
import urllib.error
import urllib.request

SERVER_PORT = 817
BASE_URL    = f"http://localhost:{SERVER_PORT}"

_heartbeat_key: str | None = None

HELP = """
  login <join-code> <api-key>   Authenticate (unlocks the open browser page)
  msg "text here"               Send a message to the relay
  help                          Show this help
  exit                          Disconnect and quit
"""

# ── HTTP helper ───────────────────────────────────────────────────────────────
def _post(path: str, body: dict) -> tuple[int, dict]:
    """POST JSON to the server. Returns (status_code, response_dict)."""
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}
    except Exception:
        return 0, {}

# ── Heartbeat thread ──────────────────────────────────────────────────────────
def _run_heartbeat():
    while True:
        time.sleep(10)
        key = _heartbeat_key
        if key:
            _post("/heartbeat", {"key": key})

# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    global _heartbeat_key
    print("█▓▒░ CODFISHNET TERMINAL v0.8 ░▒▓█")
    print("Relay standing by. Type 'help' for commands.\n")

    threading.Thread(target=_run_heartbeat, daemon=True).start()

    current_key  = None
    current_name = None

    while True:
        try:
            cmd_line = input("$ ").strip()
            if not cmd_line:
                continue

            if cmd_line.lower() in ("exit", "quit"):
                print("→ Disconnecting from relay.")
                _heartbeat_key = None
                if current_key:
                    _post("/logout", {"key": current_key})
                break

            if cmd_line.lower() == "help":
                print(HELP)
                continue

            parts  = cmd_line.split(maxsplit=1)
            action = parts[0].lower()

            # ── login <join-code> <api-key> ──────────────────────────────
            if action == "login":
                if len(parts) < 2:
                    print("Usage: login <join-code> <api-key>")
                    continue
                args = parts[1].split(maxsplit=1)
                if len(args) != 2:
                    print("Usage: login <join-code> <api-key>")
                    continue

                join_code, api_key = args
                print(f"→ Authenticating [{join_code.upper()}]...")

                status, data = _post("/validate_login",
                                     {"join_code": join_code, "api_key": api_key})
                if status == 200:
                    current_key  = api_key
                    current_name = data.get("name", api_key)
                    _heartbeat_key = api_key
                    print(f"→ Access granted. Welcome, [{current_name}].")
                    print("→ The browser page should now be unlocked.")
                else:
                    detail = data.get("detail", "Unknown error")
                    print(f"→ Login failed: {detail}")

            # ── msg "text" ───────────────────────────────────────────────
            elif action == "msg":
                if not current_key:
                    print("→ Not authenticated. Use 'login' first.")
                    continue
                if len(parts) < 2:
                    print('Usage: msg "your message"')
                    continue

                text = parts[1].strip().strip('"\'')
                if not text:
                    print("→ Message cannot be empty.")
                    continue

                status, data = _post("/send", {"key": current_key, "text": text})
                if status == 200:
                    print("→ Transmitted.")
                else:
                    detail = data.get("detail", str(status) if status else "Server unreachable")
                    print(f"→ Send failed: {detail}")

            else:
                if current_key:
                    print("→ Unknown command. Type 'help'.")
                else:
                    print("→ Unknown command. Use 'login' first or type 'help'.")

        except KeyboardInterrupt:
            print("\n→ Interrupted. Disconnecting.")
            _heartbeat_key = None
            if current_key:
                _post("/logout", {"key": current_key})
            break
        except Exception as e:
            print(f"→ Error: {e}")


if __name__ == "__main__":
    main()
