# Deploying Warden to Fly.io

The repo is dockerized (`Dockerfile`, `.dockerignore`, `fly.toml`). Fly reads both automatically.

## What you need to do

These require your Fly.io account and can't be delegated:

1. **Install flyctl:**
   ```bash
   curl -L https://fly.io/install.sh | sh
   ```
   (or `brew install flyctl` on Mac)

2. **Log in:**
   ```bash
   fly auth login
   ```
   Opens a browser to sign up/log in. Free, but a card is required for verification — the free monthly allowance covers this app's size.

3. **Launch:**
   ```bash
   cd /path/to/warden   # your local clone, main branch
   fly launch
   ```
   It detects the existing `fly.toml` and `Dockerfile` and asks a few questions:
   - **App name** — `warden-demo` (from `fly.toml`) may already be taken globally; pick another or let it auto-generate one.
   - **Region** — defaults to `iad` (Virginia), change if you want.
   - **Postgres/Redis** — say no, not needed.

   It then builds the `Dockerfile` remotely and deploys.

4. **Get your URL:**
   ```bash
   fly status
   ```
   Gives you `<your-app-name>.fly.dev`.

5. **Send me that app name** so `web/grid_view.js` can be updated to point at it (see below).

6. **Open `web/index.html` locally in a browser** once the URL is wired in — that's the dashboard.

## What Claude can do

- Wrote and maintains `Dockerfile`, `.dockerignore`, and `fly.toml`.
- Made `server/ws_server.py`'s host configurable via `WARDEN_HOST` so the container binds `0.0.0.0` without further edits.
- Once you have the app name from `fly launch`, can update `web/grid_view.js:8` to:
  ```js
  const WS_URL = "wss://<your-app-name>.fly.dev";
  ```
  (Fly terminates TLS, so it's `wss://` not `ws://`.)
- Can write a `fly deploy`-based redeploy step, or a GitHub Actions workflow that deploys on push to `main`.
- Can debug `fly launch`/`fly deploy` output with you if you paste errors in.
- Cannot run `fly` commands against your account myself — I have no Fly.io credentials or access to your account.

## Notes
- `fly.toml` sets `auto_stop_machines = false` so the machine doesn't scale to zero and drop live WebSocket connections while idle.
- Redeploying after code changes: `fly deploy` (rebuilds the Dockerfile and rolls out).
