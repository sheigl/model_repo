#!/usr/bin/env bash
# Run once after every container restart. Everything here is the ONLY thing that
# does not survive on the persistent volume (/home/openchamber/code is on /dev/md0).
set -e
cd /home/openchamber/code/model_repo
export PLAYWRIGHT_BROWSERS_PATH=/home/openchamber/code/model_repo/.pwb

# 1. Reinstall the system shared libraries Chromium needs (in /usr/lib, wiped each restart).
sudo .venv/bin/python -m playwright install-deps chromium

# 2. Ensure the uv binary is present (it lives on the wiped overlay, so may be gone).
if ! command -v uv >/dev/null 2>&1; then
  echo "uv missing -> reinstalling..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 3. Launch the app server (deps already persist in .venv; browsers persist in .pwb).
MODEL_REPO_CONFIG=/tmp/test_config.yaml .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8321

# Optional: set MODELF_REPO_CONFIG to /home/openchamber/code/model_repo/app/config.yaml (persistent)
# instead of /tmp (wiped) if you want the config + job history to survive restarts.
