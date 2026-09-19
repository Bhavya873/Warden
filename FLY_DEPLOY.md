# Deploying Warden to Fly.io

The repo is dockerized (`Dockerfile`, `.dockerignore`, `fly.toml`). Fly reads both automatically.

## 1. Install flyctl
```bash
curl -L https://fly.io/install.sh | sh
```
(or `brew install flyctl` on Mac)

## 2. Log in
```bash
fly auth login
```
Opens a browser to sign up/log in. Free, but a card is required for verification — the free monthly allowance covers this app's size.

## 3. Launch
```bash
cd /path/to/warden   # your local clone, main branch
fly launch
```
It detects the existing `fly.toml` and `Dockerfile` and asks a few questions:
- **App name** — `warden-demo` (from `fly.toml`) may already be taken globally; pick another or let it auto-generate one.
- **Region** — defaults to `iad` (Virginia), change if you want.
- **Postgres/Redis** — say no, not needed.

It then builds the `Dockerfile` remotely and deploys.

## 4. Get your URL
```bash
fly status
```
Gives you `<your-app-name>.fly.dev`.

## 5. Point the dashboard at it
Fly terminates TLS, so the WebSocket URL is `wss://`, not `ws://`. Edit `web/grid_view.js:8`:
```js
const WS_URL = "wss://<your-app-name>.fly.dev";
```
Then open `web/index.html` locally in a browser.

## Notes
- `fly.toml` sets `auto_stop_machines = false` so the machine doesn't scale to zero and drop live WebSocket connections while idle.
- Redeploying after code changes: `fly deploy` (rebuilds the Dockerfile and rolls out).
