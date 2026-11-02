#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${REPO_DIR}/.venv"

echo "============================================================"
echo "  The Wildlife Desk — Local Newsroom Environment Setup"
echo "============================================================"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required." >&2
  exit 1
fi

if [ ! -d "${VENV_DIR}" ]; then
  echo "-> Creating Python virtual environment in ${VENV_DIR}..."
  python3 -m venv "${VENV_DIR}"
fi

echo "-> Installing Python dependencies (Pillow, MCP SDK, pytest)..."
"${VENV_DIR}/bin/pip" install --upgrade pip -q
"${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/requirements.txt" -q

chmod +x "${REPO_DIR}/scripts/wildlife_desk.py" "${REPO_DIR}/scripts/mcp_server.py"

if [ ! -f "${REPO_DIR}/.env" ] && [ -f "${REPO_DIR}/.env.example" ]; then
  cp "${REPO_DIR}/.env.example" "${REPO_DIR}/.env"
  chmod 600 "${REPO_DIR}/.env"
  echo "-> Created local .env from .env.example (PUBLISH_ENABLED=false)."
fi

cat <<EOF

============================================================
  Agent Integration Snippets
============================================================

1. MCP Server Config (Antigravity / Claude Code / Cursor / Windsurf):
{
  "mcpServers": {
    "wildlife-desk": {
      "command": "${VENV_DIR}/bin/python",
      "args": ["${REPO_DIR}/scripts/mcp_server.py"]
    }
  }
}

2. Agent Skill Symlink:
  mkdir -p ~/.agents/skills
  ln -sfn "${REPO_DIR}" ~/.agents/skills/wildlife-desk
============================================================
EOF
