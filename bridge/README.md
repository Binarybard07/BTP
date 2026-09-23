# BTP Local Read-Only Context Bridge

The **Context Bridge** is a lightweight, zero-dependency HTTP server that exposes project documentation to external AI agents (such as ChatGPT) over a secure, read-only API.

---

## 🚀 1. How to Start the Bridge

Start the server using UV:

```bash
uv run python bridge/server.py
```

Optional environment variables:
```bash
# Custom host or port
BRIDGE_HOST=127.0.0.1 BRIDGE_PORT=8080 uv run python bridge/server.py
```

---

## 📍 2. Default Local Address & Port

- **Host**: `127.0.0.1` (Localhost only)
- **Port**: `8000`
- **Base URL**: `http://127.0.0.1:8000`

---

## 🔗 3. Available Read-Only Endpoints

| Endpoint | Method | Response Content-Type | Target File / Payload |
|---|---|---|---|
| `GET /state` | `GET`, `HEAD` | `text/markdown` | `docs/project_state.md` |
| `GET /decisions` | `GET`, `HEAD` | `text/markdown` | `docs/decisions.md` |
| `GET /research` | `GET`, `HEAD` | `text/markdown` | `docs/research_log.md` |
| `GET /implementation` | `GET`, `HEAD` | `text/markdown` | `docs/implementation_log.md` |
| `GET /experiments` | `GET`, `HEAD` | `text/markdown` | `docs/experiments.md` |
| `GET /todo` | `GET`, `HEAD` | `text/markdown` | `docs/todo.md` |
| `GET /context` | `GET`, `HEAD` | `application/json` | Combined JSON object of all 6 docs |

---

## 🛡️ 4. What Information is Exposed

**ONLY** the 6 Markdown documentation files under `docs/` are accessible via the bridge:
1. `docs/project_state.md`
2. `docs/decisions.md`
3. `docs/research_log.md`
4. `docs/implementation_log.md`
5. `docs/experiments.md`
6. `docs/todo.md`

---

## 🔒 5. What Information is Intentionally NOT Exposed

The bridge strictly blocks access to the rest of the repository:
- **No Source Code**: `src/` directory is hidden.
- **No Market Data**: `data/raw/` and other data directories are hidden.
- **No Credentials / Config**: `.env`, API keys, `pyproject.toml`, `.git/` are hidden.
- **No Arbitrary File Access**: Directory traversal attacks (`/../`, `/src/`, etc.) return `404` error responses.

---

## 🌐 6. How to Put the Bridge Behind an HTTPS Tunnel (For ChatGPT Access)

To allow ChatGPT or custom GPT actions to read the context bridge, expose the local server using an HTTPS tunnel:

### Option A: Using ngrok
```bash
ngrok http 8000
```
*Copy the generated `https://xxxx.ngrok-free.app` URL and provide it to ChatGPT.*

### Option B: Using Cloudflare Tunnels (`cloudflared`)
```bash
cloudflared tunnel --url http://127.0.0.1:8000
```
*Copy the generated `https://xxxx.trycloudflare.com` URL and provide it to ChatGPT.*

---

## 🔐 7. Security Considerations

1. **Strict Read-Only Enforcement**: Only `GET` and `HEAD` methods are accepted. `POST`, `PUT`, `DELETE`, and `PATCH` requests are rejected with `405 Method Not Allowed`.
2. **Hardcoded Routing**: Endpoints are strictly mapped to fixed relative filenames (`project_state.md`, `decisions.md`, etc.). User-supplied URL path parameters are never interpolated into filesystem calls.
3. **Local Host Binding**: Server binds exclusively to `127.0.0.1` by default to prevent unauthorized network interface exposure.
