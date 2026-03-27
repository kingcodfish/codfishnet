# CODFISHNET

```
█▓▒░ CODFISHNET TERMINAL ░▒▓█
Secure Relay // Node-47 // Encryption Active
```

> *Built out of boredom — a retro, hacker-aesthetic chatroom that probably nobody will use. If you stumbled across it anyway, feel free to fork it, tear it apart, and build something far better for your own late-night project.*

A self-hosted terminal-themed web chatroom. Users authenticate through a standalone CLI client that unlocks a read-only browser relay. Messages are sent exclusively from the CLI — the webpage is a passive viewer only.

---

## Features

- Green phosphor terminal aesthetic
- CLI-only messaging — the browser just displays the relay
- Join-code authentication flow (webpage shows a code, CLI redeems it)
- Admin panel — manage keys, kick sessions, view history
- One-time-use and time-expiring API keys
- Heartbeat-based disconnect detection (force-close the CLI → webpage updates)
- No database — pure JSON file storage
- Single Python file server, single Python file CLI (stdlib only, no pip needed for CLI)

---

## Prerequisites

- Python 3.11+
- pip (for the server only)

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/your-username/codfishnet.git
cd codfishnet
```

### 2. Install server dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Open `.env` and set `CODFISHNET_ADMIN` to a strong secret. You can generate one with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

The server **will not start** without this variable set.

### 4. Start the server

```bash
python main.py
```

The relay will be running at `http://localhost:817` (or whatever port you configured).

### 5. Create your first API key

Open `http://localhost:817/admin` in a browser, log in with your admin secret, and generate a key for yourself.

### 6. Connect via CLI

Download the CLI from the chat page (`↓ DOWNLOAD CLI` button), or copy it from `public/cli.py`.

Open the chat page in a browser: `http://localhost:817`

In your terminal, run:

```
python codfishnet_cli.py
$ login <join-code> <api-key>
```

The browser page unlocks automatically once the CLI authenticates.

---

## Configuration

All configuration is done via `.env` (copy from `.env.example`):

| Variable            | Default     | Description                                      |
|---------------------|-------------|--------------------------------------------------|
| `CODFISHNET_ADMIN`  | *required*  | Admin panel password — make it strong            |
| `HOST`              | `0.0.0.0`   | Address the server binds to                      |
| `PORT`              | `817`        | Port the server listens on                       |
| `CLEAR_INTERVAL`    | `daily`     | How often chat history auto-clears (`daily` / `weekly`) |

### Changing the CLI server address

If your server is not on `localhost:817` (e.g. you're hosting it remotely), edit the top two lines of `cli.py` before distributing it:

```python
SERVER_PORT = 817
BASE_URL    = f"http://your-server-address:{SERVER_PORT}"
```

---

## CLI Commands

```
login <join-code> <api-key>   Authenticate and unlock the browser page
msg "text here"               Send a message to the relay
help                          Show available commands
exit                          Disconnect and quit
```

---

## Directory Structure

```
codfishnet/
├── main.py              ← FastAPI server (entry point)
├── .env.example         ← Environment variable template
├── .env                 ← Your secrets (gitignored, never committed)
├── requirements.txt
├── README.md
│
├── public/              ← Files served over HTTP
│   ├── chat.html        ← Main relay viewer (the webpage users see)
│   ├── admin.html       ← Admin control panel
│   └── cli.py           ← Downloadable CLI client
│
└── private/             ← Runtime data (gitignored, never committed)
    ├── api_keys.json    ← Generated API keys
    └── chat_history.json
```

---

## Admin Panel

Available at `/admin`. Log in with your `CODFISHNET_ADMIN` secret.

- **Generate keys** — standard, one-time-use, or time-expiring
- **Kick sessions** — disconnect a user without revoking their key
- **Revoke keys** — permanently remove a key and close any active session
- **Manage join codes** — view and revoke pending auth codes
- **Chat history** — view or clear stored messages

---

## Security Notes

- The admin secret is never stored in code — only read from the environment
- `private/` and `.env` are gitignored and will not be committed
- API keys are passed as WebSocket query parameters (fine for a private/LAN deployment; add a reverse proxy with TLS for public exposure)
- No rate limiting is implemented — intended for private/trusted use

---

## AI Disclosure

AI was used during parts of this project to help troubleshoot bugs, suggest refactors, and generate this readme. It wasn't heavily involved in the design or core decisions — just a useful tool for knocking out the tedious bits faster.

---

*PRs and issues welcome, or just use it as a starting point for something weirder.*
