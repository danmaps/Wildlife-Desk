#!/usr/bin/env python3
"""
The Wildlife Desk — MCP Server (stdio)

Exposes the Local Newsroom Studio tools to any MCP-compatible AI agent so the
user can collaborate with the agent as their "Reporter and Social Media Manager":
- Restricted Immich album & explicit local folder intake (`discover_sightings`)
- Evidence-linked observation & privacy screening (`record_observations`)
- Archive continuity verification (`get_continuity_context`)
- Deadpan dispatch drafting & deterministic validation (`draft_dispatch`, `hold_dispatch`)
- Deterministic 1080x1350 front-page cover rendering (`render_dispatch_cover`)
- Interactive Local Web Pressroom preview (`build_pressroom`)
- Safe publication gate & timeout reconciliation (`publish_dispatch`, `reconcile_publication`)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

import wildlife_desk

mcp = FastMCP("wildlife-desk")


def _safe_json_response(fn, *args, **kwargs) -> str:
    try:
        res = fn(*args, **kwargs)
        return json.dumps(res, indent=2)
    except wildlife_desk.WildlifeDeskError as exc:
        cfg = wildlife_desk.load_config()
        return json.dumps(
            {
                "ok": False,
                "error_type": exc.__class__.__name__,
                "error": wildlife_desk.redact_secrets(str(exc), cfg),
            },
            indent=2,
        )


@mcp.tool()
def get_newsroom_status() -> str:
    """
    Call this FIRST when starting a Wildlife Desk session.
    Returns the current source mode ('immich' or 'local'), explicit selection boundary
    validation, publish kill-switch state, dispatch counts by state, species continuity
    archive, and the step-by-step Reporter workflow.
    """
    return _safe_json_response(wildlife_desk.get_newsroom_status)


@mcp.tool()
def configure_newsroom(
    wildlife_source_mode: Optional[str] = None,
    immich_base_url: Optional[str] = None,
    immich_api_key: Optional[str] = None,
    immich_wildlife_album_id: Optional[str] = None,
    local_wildlife_dir: Optional[str] = None,
    burst_window_seconds: Optional[int] = None,
    timezone: Optional[str] = None,
    publish_enabled: Optional[bool] = None,
    local_server_port: Optional[int] = None,
) -> str:
    """
    Save or update local newsroom settings in the gitignored `.env` file.
    - `wildlife_source_mode`: `'immich'` (restricted Immich album) or `'local'` (explicit local directory).
    - Missing selection boundaries (`immich_wildlife_album_id` or `local_wildlife_dir`) will halt intake safely.
    - `publish_enabled` defaults to `False`.
    """
    return _safe_json_response(
        wildlife_desk.configure_newsroom,
        wildlife_source_mode=wildlife_source_mode,
        immich_base_url=immich_base_url,
        immich_api_key=immich_api_key,
        immich_wildlife_album_id=immich_wildlife_album_id,
        local_wildlife_dir=local_wildlife_dir,
        burst_window_seconds=burst_window_seconds,
        timezone=timezone,
        publish_enabled=publish_enabled,
        local_server_port=local_server_port,
    )


@mcp.tool()
def discover_sightings() -> str:
    """
    Scan the configured wildlife selection (Immich album or local directory),
    conservatively group burst captures, strip EXIF/GPS metadata into `state/media_cache/`,
    and create or preserve idempotent sighting records.
    After calling this, inspect each sighting's `cached_previews` using your file/image viewer tool!
    """
    return _safe_json_response(wildlife_desk.discover_sightings)


@mcp.tool()
def list_dispatches(state_filter: Optional[str] = None) -> str:
    """
    List all sighting dispatches in local state.
    Optional `state_filter`: `'discovered'`, `'observed'`, `'drafted'`, `'ready'`, `'held'`, `'published'`, `'failed'`.
    """
    return _safe_json_response(wildlife_desk.list_dispatches, state_filter=state_filter)


@mcp.tool()
def get_dispatch(event_id: str) -> str:
    """
    Return the full structured dispatch record (`state/dispatches/<event_id>.json`)
    including source asset metadata, cached preview paths, observations, uncertainties,
    editorial copy, rendered cover paths, and eligibility hold reasons.
    """
    return _safe_json_response(wildlife_desk.load_dispatch_record, event_id=event_id)


@mcp.tool()
def get_continuity_context(
    species: Optional[str] = None,
    exclude_event_id: Optional[str] = None,
) -> str:
    """
    Check the local dispatch archive for prior confirmed/likely sightings of a species.
    Always check this before writing headlines with premises like 'RETURNS TO PROPERTY'
    or 'THIRD VISIT'.
    """
    return _safe_json_response(
        wildlife_desk.get_continuity_context,
        species=species,
        exclude_event_id=exclude_event_id,
    )


@mcp.tool()
def record_observations(
    event_id: str,
    observations: List[Dict[str, Any]],
    uncertainties: Optional[List[str]] = None,
    contains_people: bool = False,
    contains_identifying_property: bool = False,
    is_empty_frame: bool = False,
    privacy_notes: Optional[str] = None,
    synthetic_example: Optional[bool] = None,
) -> str:
    """
    Record structured observations and privacy screening after visually inspecting a sighting's frames.
    Each item in `observations` should include:
    - `claim` (str): Factual description of only what is visibly supported by the frame(s).
    - `source_asset_ids` (list[str]): Asset IDs from `source.asset_ids` supporting the claim.
    - `species` (str | None): Lowercase species name (e.g. `'raccoon'`, `'deer'`, `'fox'`) or `'unknown'`.
    - `visible_behavior` (str | None): Short description of visible posture/movement.
    - `certainty` (str): `'confirmed'`, `'likely'`, or `'uncertain'`.
    - `focal_point` (list[float] | None): Optional `[x, y]` in `0.0..1.0` indicating the animal's position in frame.
    """
    return _safe_json_response(
        wildlife_desk.record_observations,
        event_id=event_id,
        observations=observations,
        uncertainties=uncertainties,
        contains_people=contains_people,
        contains_identifying_property=contains_identifying_property,
        is_empty_frame=is_empty_frame,
        privacy_notes=privacy_notes,
        synthetic_example=synthetic_example,
    )


@mcp.tool()
def draft_dispatch(
    event_id: str,
    headline: str,
    caption: str,
    section: str = "Local Affairs",
    character_label: Optional[str] = None,
    selected_asset_id: Optional[str] = None,
    layout_position: str = "auto",
    crop_focus_x: Optional[float] = None,
    crop_focus_y: Optional[float] = None,
    auto_render_cover: bool = True,
) -> str:
    """
    Save the deadpan newspaper headline (5-12 words) and short Instagram caption article
    (60-120 words, 2-3 paragraphs), validate factual support and privacy eligibility,
    and render the 1080x1350 front-page cover.
    `section` must be one of: `'The Night Shift'`, `'Local Affairs'`, `'Developing Story'`, `'Weekend Edition'`.
    `layout_position` can be `'auto'`, `'bottom-left'`, `'bottom-right'`, `'top-left'`, or `'top-right'`.
    """
    return _safe_json_response(
        wildlife_desk.draft_dispatch,
        event_id=event_id,
        headline=headline,
        caption=caption,
        section=section,
        character_label=character_label,
        selected_asset_id=selected_asset_id,
        layout_position=layout_position,
        crop_focus_x=crop_focus_x,
        crop_focus_y=crop_focus_y,
        auto_render_cover=auto_render_cover,
    )


@mcp.tool()
def render_dispatch_cover(
    event_id: str,
    layout_position: Optional[str] = None,
    crop_focus_x: Optional[float] = None,
    crop_focus_y: Optional[float] = None,
) -> str:
    """
    Re-render the 1080x1350 cover, 1080x1080 1:1 grid crop preview, and 270x338 thumbnail
    with updated text placement (`'bottom-left'`, `'bottom-right'`, `'top-left'`, `'top-right'`)
    or crop focus coordinates (`crop_focus_x`, `crop_focus_y` in `0.0..1.0`).
    """
    return _safe_json_response(
        wildlife_desk.render_dispatch_cover,
        event_id=event_id,
        layout_position=layout_position,
        crop_focus_x=crop_focus_x,
        crop_focus_y=crop_focus_y,
    )


@mcp.tool()
def hold_dispatch(
    event_id: str,
    reason: str,
    is_empty_frame: Optional[bool] = None,
) -> str:
    """
    Place a sighting on hold with an explicit reason (e.g. empty wind-triggered frame,
    human/vehicle visible, or insufficient visual evidence for a story).
    """
    return _safe_json_response(
        wildlife_desk.hold_dispatch,
        event_id=event_id,
        reason=reason,
        is_empty_frame=is_empty_frame,
    )


@mcp.tool()
def build_pressroom(start_server: bool = True) -> str:
    """
    Compile the Local Web Pressroom (`output/pressroom/index.html`) and optionally
    start the local HTTP server (`http://localhost:8766/`) so the user can inspect
    the front-page covers, 1:1 profile crops, thumbnails, captions, and evidence ledger.
    """
    return _safe_json_response(wildlife_desk.build_pressroom, start_server=start_server)


@mcp.tool()
def publish_dispatch(event_id: str, dry_run: bool = False) -> str:
    """
    Attempt to publish an eligible `ready` dispatch.
    Honors the `PUBLISH_ENABLED=false` kill-switch, atomic lock claims, and timeout
    reconciliation requirements.
    """
    return _safe_json_response(
        wildlife_desk.publish_dispatch,
        event_id=event_id,
        dry_run=dry_run,
    )


@mcp.tool()
def reconcile_publication(
    event_id: str,
    remote_was_created: bool,
    remote_post_id: Optional[str] = None,
) -> str:
    """
    Reconcile an ambiguous publication timeout before retrying.
    Set `remote_was_created=True` if the post appeared on Instagram, or `False` if verified absent.
    """
    return _safe_json_response(
        wildlife_desk.reconcile_publication,
        event_id=event_id,
        remote_was_created=remote_was_created,
        remote_post_id=remote_post_id,
    )


if __name__ == "__main__":
    mcp.run()
