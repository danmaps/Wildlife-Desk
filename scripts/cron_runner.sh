#!/usr/bin/env bash
# The Wildlife Desk — Automated Batch Intake Gate + Multi-Week Drip Publisher
# Invoked by cron (e.g. twice daily at 08:05 and 12:05 local time).
#
# Stage 1 (Zero-AI Gate): Runs deterministic `discover` + `pair-companions`.
# Stage 2 (Conditional Headless Agent): ONLY if new `discovered` sightings exist,
#          launches a headless Codex/AGY session to visually inspect captures,
#          hold empty/private/redundant frames, and draft READY 2-slide Carousel posts.
# Stage 3 (Fixed-Schedule Drip Publisher): On Tue / Thu / Sat (at or after 08:00 local)
#          with >= 36h cooldown since the last post, publishes at most 1 READY dispatch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PATH="${HOME}/.npm-global/bin:${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export TZ="${WILDLIFE_DESK_TZ:-America/Los_Angeles}"

PYTHON_BIN="${REPO_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="$(command -v python3)"
fi

CLI="${REPO_DIR}/scripts/wildlife_desk.py"
LOG_TIME="$(date -Iseconds)"
echo "[${LOG_TIME}] === Wildlife Desk Cron Runner starting ==="

# 1. Deterministic Discovery & Photo+Video Companion Pairing (zero AI cost)
"${PYTHON_BIN}" "${CLI}" discover >/dev/null
"${PYTHON_BIN}" "${CLI}" pair-companions >/dev/null

DISCOVERED_COUNT="$(
    "${PYTHON_BIN}" -c '
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/scripts")
import wildlife_desk
res = wildlife_desk.list_dispatches(state_filter="discovered", repo_dir=Path(sys.argv[1]))
print(res["total_count"])
' "${REPO_DIR}"
)"

echo "[$(date -Iseconds)] Discovered (uncurated) sightings count: ${DISCOVERED_COUNT}"

# 2. Trigger Headless AI Curation ONLY when new uncurated captures exist
if [[ "${DISCOVERED_COUNT}" -gt 0 ]]; then
    echo "[$(date -Iseconds)] New captures detected (${DISCOVERED_COUNT}). Launching headless editorial curation..."
    AGENT_PROMPT="You are operating The Wildlife Desk in ${REPO_DIR}. Follow SKILL.md and docs/EDITORIAL.md strictly. Run list_dispatches(state_filter='discovered'). For each discovered sighting, visually inspect its cached preview image (and companion video preview if paired), read the exact date/time/temp stamp from the bottom HUD bar of the raw capture, screen for privacy/empty frames/redundant same-visit captures (call hold_dispatch when appropriate), and for distinct wildlife sightings record asset-linked observations and draft READY 2-slide Carousel dispatches (draft_dispatch with camera_stamp='<MM/DD/YYYY HH:MM:SS • temp>' and auto_render_cover=True). Finally run build_pressroom."

    if command -v codex >/dev/null 2>&1; then
        codex exec --full-auto -C "${REPO_DIR}" "${AGENT_PROMPT}" || echo "[$(date -Iseconds)] WARNING: headless codex run exited non-zero"
    elif command -v agy >/dev/null 2>&1; then
        agy -p "${AGENT_PROMPT}" || echo "[$(date -Iseconds)] WARNING: headless agy run exited non-zero"
    else
        echo "[$(date -Iseconds)] WARNING: Neither codex nor agy CLI found on PATH; leaving ${DISCOVERED_COUNT} sightings in discovered state."
    fi
fi

# 3. Fixed-Schedule Drip Publish (Tue / Thu / Sat @ 09:00 local time, 20h cooldown)
DRIP_DAYS="${DRIP_SCHEDULE_DAYS:-tue,thu,sat}"
DRIP_HOUR="${DRIP_SCHEDULE_HOUR:-9}"
DRIP_COOLDOWN="${DRIP_COOLDOWN_HOURS:-20}"

DRIP_OUT="$("${PYTHON_BIN}" "${CLI}" drip-publish --days "${DRIP_DAYS}" --hour "${DRIP_HOUR}" --cooldown-hours "${DRIP_COOLDOWN}")"
echo "[$(date -Iseconds)] Drip publish result: ${DRIP_OUT}"
echo "[$(date -Iseconds)] === Wildlife Desk Cron Runner finished ==="
