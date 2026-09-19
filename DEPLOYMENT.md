# Deploying Warden to GCP

The repo is dockerized (`Dockerfile`, `.dockerignore`). This is the split of who does what.

## What you need to do

These require your GCP account/billing and can't be delegated:

1. **Create a GCP account and project**, enable billing (free tier still needs a card on file).
2. **Run `gcloud auth login`** yourself (interactive — paste `! gcloud auth login` into the session if you want it in this terminal).
3. **Create the VM:**
   ```bash
   gcloud compute instances create warden-demo \
     --zone=us-central1-a --machine-type=e2-micro \
     --image-family=debian-12 --image-project=debian-cloud \
     --boot-disk-size=30GB
   ```
4. **Open the WebSocket port:**
   ```bash
   gcloud compute firewall-rules create allow-warden-ws \
     --allow=tcp:8765 --source-ranges=0.0.0.0/0
   ```
5. **SSH in and give the go-ahead to build/run** (or hand me the VM's IP and SSH access and I can run the remaining commands with you):
   ```bash
   gcloud compute ssh warden-demo --zone=us-central1-a
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER && newgrp docker
   git clone <your-repo-url> warden && cd warden
   docker build -t warden .
   docker run -d --restart=always -p 8765:8765 --name warden warden
   ```
6. **Merge the `worktree-gcp-docker-deploy` PR into `main`** yourself (or tell me to, and I will — I don't merge to main without being asked each time).
7. **Get the VM's external IP** and decide how you want to share the dashboard (send people `web/index.html` + the IP, or host the static file somewhere).

## What I can do

- Wrote and can keep maintaining the `Dockerfile` / `.dockerignore`.
- Made `server/ws_server.py`'s host configurable via `WARDEN_HOST` so the container binds `0.0.0.0` without further edits.
- Can update `web/grid_view.js`'s `WS_URL` to point at your VM's IP once you have it (one-line change).
- Can write the systemd/Docker restart policy, a `gcloud`-based deploy script, or a GitHub Actions workflow that builds and redeploys on push — say the word.
- Can walk through SSH output with you and debug build/run failures in real time if you paste them in.
- Cannot run `gcloud`/`docker` against your GCP account myself — I have no credentials or access to your cloud project.
