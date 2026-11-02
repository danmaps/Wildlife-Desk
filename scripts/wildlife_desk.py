#!/usr/bin/env python3
"""
The Wildlife Desk — Local Newsroom Studio & Core Pipeline
"Real animals. Unattended camera. Artificial management."

Provides deterministic intake (Immich restricted album or explicit local folder),
conservative burst grouping, evidence-backed observation recording, editorial
validation, deterministic 1080x1350 cover rendering with measured typography,
local Pressroom HTML studio generation, and atomic publication gating.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.server
import io
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import ExifTags, Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

REPO_DIR = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = "1.0"
RENDERER_TEMPLATE_VERSION = "1.0.0"

VALID_STATES = (
    "discovered",
    "observed",
    "drafted",
    "ready",
    "publishing",
    "published",
    "held",
    "failed",
)

VALID_SECTIONS = (
    "The Night Shift",
    "Local Affairs",
    "Developing Story",
    "Weekend Edition",
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}

RETURN_PREMISE_PATTERNS = [
    r"\breturns?\b",
    r"\breturned\b",
    r"\breturning\b",
    r"\bagain\b",
    r"\bsecond\s+visit\b",
    r"\bthird\s+visit\b",
    r"\bfourth\s+visit\b",
    r"\banother\s+visit\b",
    r"\brepeat\s+visit\b",
]

TIME_SPECIFIC_PATTERNS = [
    r"\bbefore\s+dawn\b",
    r"\bafter\s+midnight\b",
    r"\bat\s+midnight\b",
    r"\bovernight\b",
    r"\bafter\s+dark\b",
    r"\bat\s+dusk\b",
    r"\bat\s+dawn\b",
    r"\b\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)\b",
]

BANNED_HYPE_PATTERNS = [
    r"#\w+",  # hashtags in article body
    r"\bsmash\s+that\s+like\b",
    r"\bwait\s+for\s+it\b",
    r"\byou\s+won'?t\s+believe\b",
    r"\blink\s+in\s+bio\b",
]

EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002600-\U000027BF"
    "\U0001FA00-\U0001FAFF"
    "]+",
    flags=re.UNICODE,
)


class WildlifeDeskError(Exception):
    """Base error for Wildlife Desk operations."""


class SelectionBoundaryError(WildlifeDeskError):
    """Raised when source selection configuration is missing, empty, or unsafe."""


class ValidationHoldError(WildlifeDeskError):
    """Raised when a dispatch cannot advance due to eligibility/validation holds."""


class PublicationGateError(WildlifeDeskError):
    """Raised when publication is disabled, locked, or blocked by reconciliation."""


# ---------------------------------------------------------------------------
# Configuration & Secret Redaction
# ---------------------------------------------------------------------------


def _parse_env_file(env_path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
            val = val[1:-1]
        values[key] = val
    return values


def _resolve_dir(raw_path: str, base_dir: Path) -> Path:
    p = Path(raw_path).expanduser()
    if not p.is_absolute():
        p = (base_dir / p).resolve()
    return p


def load_config(env_override: Optional[Dict[str, str]] = None, repo_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Load configuration from `.env` and environment variables with safe defaults."""
    base = repo_dir or REPO_DIR
    env_file_vars = _parse_env_file(base / ".env")

    def get_val(key: str, default: str = "") -> str:
        if env_override and key in env_override:
            return str(env_override[key])
        if key in os.environ:
            return os.environ[key]
        return env_file_vars.get(key, default)

    publish_raw = get_val("PUBLISH_ENABLED", "false").strip().lower()
    publish_enabled = publish_raw in ("true", "1", "yes")

    source_mode = get_val("WILDLIFE_SOURCE_MODE", "").strip().lower()
    immich_album_id = get_val("IMMICH_WILDLIFE_ALBUM_ID", "").strip()
    local_wildlife_dir = get_val("LOCAL_WILDLIFE_DIR", "").strip()

    if not source_mode:
        if local_wildlife_dir and not immich_album_id:
            source_mode = "local"
        else:
            source_mode = "immich"

    state_dir = _resolve_dir(get_val("STATE_DIR", "./state"), base)
    output_dir = _resolve_dir(get_val("OUTPUT_DIR", "./output"), base)

    try:
        burst_window = max(0, int(get_val("BURST_WINDOW_SECONDS", "180")))
    except ValueError:
        burst_window = 180

    try:
        max_posts_per_day = max(0, int(get_val("MAX_POSTS_PER_DAY", "1")))
    except ValueError:
        max_posts_per_day = 1

    try:
        local_server_port = int(get_val("LOCAL_SERVER_PORT", "8766"))
    except ValueError:
        local_server_port = 8766

    cfg: Dict[str, Any] = {
        "repo_dir": str(base),
        "wildlife_source_mode": source_mode,
        "immich_base_url": get_val("IMMICH_BASE_URL", "https://immich.example.invalid").strip().rstrip("/"),
        "immich_api_key": get_val("IMMICH_API_KEY", "").strip(),
        "immich_wildlife_album_id": immich_album_id,
        "local_wildlife_dir": local_wildlife_dir,
        "burst_window_seconds": burst_window,
        "ai_base_url": get_val("AI_BASE_URL", "").strip(),
        "ai_api_key": get_val("AI_API_KEY", "").strip(),
        "ai_model": get_val("AI_MODEL", "").strip(),
        "instagram_account_id": get_val("INSTAGRAM_ACCOUNT_ID", "").strip(),
        "instagram_access_token": get_val("INSTAGRAM_ACCESS_TOKEN", "").strip(),
        "instagram_stage_dir": get_val("INSTAGRAM_STAGE_DIR", "").strip(),
        "instagram_stage_base_url": get_val("INSTAGRAM_STAGE_BASE_URL", "").strip().rstrip("/"),
        "publish_enabled": publish_enabled,
        "max_posts_per_day": max_posts_per_day,
        "max_model_calls_per_run": int(get_val("MAX_MODEL_CALLS_PER_RUN", "10") or "10"),
        "max_model_cost_per_run_usd": float(get_val("MAX_MODEL_COST_PER_RUN_USD", "1.00") or "1.00"),
        "timezone": get_val("TIMEZONE", "UTC").strip() or "UTC",
        "state_dir": str(state_dir),
        "output_dir": str(output_dir),
        "local_server_port": local_server_port,
        "log_level": get_val("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    }
    return cfg


def redact_secrets(text: str, config: Optional[Dict[str, Any]] = None) -> str:
    """Redact credentials, API keys, and tokens from logs, errors, and state output."""
    if not text:
        return text
    redacted = str(text)
    if config:
        for secret_key in ("immich_api_key", "ai_api_key", "instagram_access_token"):
            secret_val = config.get(secret_key, "")
            if secret_val and len(secret_val) >= 4:
                redacted = redacted.replace(secret_val, "[REDACTED_SECRET]")
    # Redact Bearer tokens, apiKey query params, and x-api-key patterns
    redacted = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._\-~+/]+=*", r"\1[REDACTED_TOKEN]", redacted)
    redacted = re.sub(
        r"(?i)((?:api[_-]?key|access[_-]?token|token|secret|password)\s*[=:]\s*)([^\s&\"',;]+)",
        r"\1[REDACTED]",
        redacted,
    )
    return redacted


def ensure_runtime_dirs(config: Dict[str, Any]) -> Dict[str, Path]:
    """Ensure local ignored state and output directories exist."""
    state_dir = Path(config["state_dir"])
    output_dir = Path(config["output_dir"])
    dirs = {
        "state": state_dir,
        "dispatches": state_dir / "dispatches",
        "media_cache": state_dir / "media_cache",
        "locks": state_dir / "locks",
        "receipts": state_dir / "receipts",
        "output": output_dir,
        "pressroom": output_dir / "pressroom",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def configure_newsroom(
    wildlife_source_mode: Optional[str] = None,
    immich_base_url: Optional[str] = None,
    immich_api_key: Optional[str] = None,
    immich_wildlife_album_id: Optional[str] = None,
    local_wildlife_dir: Optional[str] = None,
    burst_window_seconds: Optional[int] = None,
    timezone: Optional[str] = None,
    publish_enabled: Optional[bool] = None,
    instagram_account_id: Optional[str] = None,
    instagram_access_token: Optional[str] = None,
    instagram_stage_dir: Optional[str] = None,
    instagram_stage_base_url: Optional[str] = None,
    local_server_port: Optional[int] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create or update local gitignored `.env` with newsroom settings."""
    base = repo_dir or REPO_DIR
    env_path = base / ".env"
    example_path = base / ".env.example"

    if not env_path.exists() and example_path.exists():
        shutil.copyfile(example_path, env_path)
    elif not env_path.exists():
        env_path.write_text("", encoding="utf-8")

    current = _parse_env_file(env_path)
    updates: Dict[str, str] = {}
    if wildlife_source_mode is not None:
        mode = wildlife_source_mode.strip().lower()
        if mode not in ("immich", "local"):
            raise WildlifeDeskError("wildlife_source_mode must be 'immich' or 'local'.")
        updates["WILDLIFE_SOURCE_MODE"] = mode
    if immich_base_url is not None:
        updates["IMMICH_BASE_URL"] = immich_base_url.strip()
    if immich_api_key is not None:
        updates["IMMICH_API_KEY"] = immich_api_key.strip()
    if immich_wildlife_album_id is not None:
        updates["IMMICH_WILDLIFE_ALBUM_ID"] = immich_wildlife_album_id.strip()
    if local_wildlife_dir is not None:
        updates["LOCAL_WILDLIFE_DIR"] = local_wildlife_dir.strip()
    if burst_window_seconds is not None:
        updates["BURST_WINDOW_SECONDS"] = str(max(0, int(burst_window_seconds)))
    if timezone is not None:
        updates["TIMEZONE"] = timezone.strip()
    if publish_enabled is not None:
        updates["PUBLISH_ENABLED"] = "true" if publish_enabled else "false"
    if instagram_account_id is not None:
        updates["INSTAGRAM_ACCOUNT_ID"] = instagram_account_id.strip()
    if instagram_access_token is not None:
        updates["INSTAGRAM_ACCESS_TOKEN"] = instagram_access_token.strip()
    if instagram_stage_dir is not None:
        updates["INSTAGRAM_STAGE_DIR"] = instagram_stage_dir.strip()
    if instagram_stage_base_url is not None:
        updates["INSTAGRAM_STAGE_BASE_URL"] = instagram_stage_base_url.strip()
    if local_server_port is not None:
        updates["LOCAL_SERVER_PORT"] = str(int(local_server_port))

    current.update(updates)

    lines = [
        "# Local Wildlife Desk configuration (gitignored - do not commit)",
        f"WILDLIFE_SOURCE_MODE={current.get('WILDLIFE_SOURCE_MODE', 'immich')}",
        f"IMMICH_BASE_URL={current.get('IMMICH_BASE_URL', 'https://immich.example.invalid')}",
        f"IMMICH_API_KEY={current.get('IMMICH_API_KEY', '')}",
        f"IMMICH_WILDLIFE_ALBUM_ID={current.get('IMMICH_WILDLIFE_ALBUM_ID', '')}",
        f"LOCAL_WILDLIFE_DIR={current.get('LOCAL_WILDLIFE_DIR', '')}",
        f"BURST_WINDOW_SECONDS={current.get('BURST_WINDOW_SECONDS', '180')}",
        "",
        f"AI_BASE_URL={current.get('AI_BASE_URL', '')}",
        f"AI_API_KEY={current.get('AI_API_KEY', '')}",
        f"AI_MODEL={current.get('AI_MODEL', '')}",
        "",
        f"INSTAGRAM_ACCOUNT_ID={current.get('INSTAGRAM_ACCOUNT_ID', '')}",
        f"INSTAGRAM_ACCESS_TOKEN={current.get('INSTAGRAM_ACCESS_TOKEN', '')}",
        f"INSTAGRAM_STAGE_DIR={current.get('INSTAGRAM_STAGE_DIR', '')}",
        f"INSTAGRAM_STAGE_BASE_URL={current.get('INSTAGRAM_STAGE_BASE_URL', '')}",
        f"PUBLISH_ENABLED={current.get('PUBLISH_ENABLED', 'false')}",
        f"MAX_POSTS_PER_DAY={current.get('MAX_POSTS_PER_DAY', '1')}",
        f"MAX_MODEL_CALLS_PER_RUN={current.get('MAX_MODEL_CALLS_PER_RUN', '10')}",
        f"MAX_MODEL_COST_PER_RUN_USD={current.get('MAX_MODEL_COST_PER_RUN_USD', '1.00')}",
        "",
        f"TIMEZONE={current.get('TIMEZONE', 'UTC')}",
        f"STATE_DIR={current.get('STATE_DIR', './state')}",
        f"OUTPUT_DIR={current.get('OUTPUT_DIR', './output')}",
        f"LOCAL_SERVER_PORT={current.get('LOCAL_SERVER_PORT', '8766')}",
        f"LOG_LEVEL={current.get('LOG_LEVEL', 'INFO')}",
        "",
    ]
    env_path.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(env_path, 0o600)
    return get_newsroom_status(repo_dir=base)


# ---------------------------------------------------------------------------
# Selection Boundary Validation
# ---------------------------------------------------------------------------


def validate_selection_boundary(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministically validate the configured wildlife selection boundary.
    Missing or overly broad selection configuration MUST stop intake immediately.
    """
    mode = config.get("wildlife_source_mode", "immich")
    base_repo = Path(config["repo_dir"]).resolve()

    if mode == "immich":
        base_url = config.get("immich_base_url", "")
        api_key = config.get("immich_api_key", "")
        album_id = config.get("immich_wildlife_album_id", "")

        if not album_id:
            raise SelectionBoundaryError(
                "IMMICH_WILDLIFE_ALBUM_ID is not configured. Intake is halted to prevent "
                "scanning the entire Immich library."
            )
        if not re.match(r"^[A-Za-z0-9_\-]{8,64}$", album_id):
            raise SelectionBoundaryError(
                f"IMMICH_WILDLIFE_ALBUM_ID '{album_id}' is malformed. Expected an explicit album identifier."
            )
        if not base_url or ".invalid" in base_url:
            raise SelectionBoundaryError(
                "IMMICH_BASE_URL is not configured or points to a placeholder (.invalid) domain."
            )
        if not api_key:
            raise SelectionBoundaryError("IMMICH_API_KEY is missing for Immich intake.")
        return {
            "valid": True,
            "mode": "immich",
            "selection_id": album_id,
            "description": f"Restricted Immich Album ({album_id})",
        }

    elif mode == "local":
        raw_dir = config.get("local_wildlife_dir", "").strip()
        if not raw_dir:
            raise SelectionBoundaryError(
                "LOCAL_WILDLIFE_DIR is not configured. Intake is halted; missing source "
                "configuration must never widen selection."
            )
        resolved = _resolve_dir(raw_dir, base_repo)
        home_dir = Path.home().resolve()
        forbidden_roots = {
            Path("/").resolve(),
            Path("/home").resolve(),
            Path("/Users").resolve(),
            Path("/tmp").resolve(),
            Path("/var").resolve(),
            Path("/mnt").resolve(),
            home_dir,
            base_repo,
        }
        if resolved in forbidden_roots:
            raise SelectionBoundaryError(
                f"LOCAL_WILDLIFE_DIR ({resolved}) points to a top-level system or workspace root. "
                "Configure a dedicated wildlife capture directory."
            )
        if not resolved.exists() or not resolved.is_dir():
            raise SelectionBoundaryError(
                f"Configured LOCAL_WILDLIFE_DIR does not exist or is not a directory: {resolved}"
            )
        return {
            "valid": True,
            "mode": "local",
            "selection_id": str(resolved),
            "description": f"Explicit Local Wildlife Directory ({resolved.name})",
        }

    else:
        raise SelectionBoundaryError(
            f"Unsupported WILDLIFE_SOURCE_MODE '{mode}'. Must be 'immich' or 'local'."
        )


# ---------------------------------------------------------------------------
# EXIF & Timestamp Provenance Helpers
# ---------------------------------------------------------------------------


def _parse_exif_datetime(raw_dt: str, offset_str: Optional[str] = None) -> Tuple[Optional[str], bool, Optional[float]]:
    """
    Parse EXIF DateTimeOriginal or ISO string.
    Returns (iso_string, timezone_known, epoch_seconds_for_relative_delta).
    Note: Trail cameras usually write naive local timestamps ('YYYY:MM:DD HH:MM:SS')
    without timezone offset. We never pretend timezone is known unless an offset is present.
    """
    if not raw_dt or not isinstance(raw_dt, str):
        return None, False, None
    s = raw_dt.strip()
    if not s or s.startswith("0000:00:00"):
        return None, False, None

    # Check ISO format first (e.g. from Immich API or sidecar JSON)
    try:
        if "T" in s or "-" in s[:10]:
            has_tz = s.endswith("Z") or bool(re.search(r"[+-]\d{2}:?\d{2}$", s))
            norm = s[:-1] + "+00:00" if s.endswith("Z") else s
            dt_obj = dt.datetime.fromisoformat(norm)
            if has_tz and dt_obj.tzinfo is not None:
                return dt_obj.isoformat(), True, dt_obj.timestamp()
            else:
                # Naive ISO string: do not attach UTC or assume timezone!
                naive_iso = dt_obj.strftime("%Y-%m-%dT%H:%M:%S")
                fake_epoch = dt_obj.replace(tzinfo=dt.timezone.utc).timestamp()
                return naive_iso, False, fake_epoch
    except ValueError:
        pass

    # Check standard EXIF format 'YYYY:MM:DD HH:MM:SS'
    m = re.match(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$", s)
    if m:
        try:
            year, month, day, hour, minute, sec = (int(x) for x in m.groups())
            dt_naive = dt.datetime(year, month, day, hour, minute, sec)
            if offset_str and re.match(r"^[+-]\d{2}:\d{2}$", offset_str.strip()):
                off = offset_str.strip()
                iso_with_tz = f"{dt_naive.strftime('%Y-%m-%dT%H:%M:%S')}{off}"
                dt_aware = dt.datetime.fromisoformat(iso_with_tz)
                return dt_aware.isoformat(), True, dt_aware.timestamp()
            else:
                naive_iso = dt_naive.strftime("%Y-%m-%dT%H:%M:%S")
                fake_epoch = dt_naive.replace(tzinfo=dt.timezone.utc).timestamp()
                return naive_iso, False, fake_epoch
        except ValueError:
            return None, False, None

    return None, False, None


def inspect_local_image_metadata(file_path: Path) -> Dict[str, Any]:
    """
    Extract safe metadata from a local image file without leaking GPS coordinates.
    Distinguishes capture time (EXIF) from file import/modification time.
    """
    stat = file_path.stat()
    import_time_iso = dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc).isoformat()
    sha256_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()

    capture_time: Optional[str] = None
    capture_timezone_known = False
    capture_epoch: Optional[float] = None
    provenance = "import_only_no_exif"
    has_gps = False
    width = 0
    height = 0

    # Check optional sidecar metadata `.meta.json` (useful for deterministic fixtures/tests)
    sidecar_path = file_path.with_suffix(file_path.suffix + ".meta.json")
    if sidecar_path.exists():
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            raw_cap = sidecar.get("capture_time")
            raw_tz_known = sidecar.get("capture_timezone_known")
            if raw_cap:
                parsed_iso, tz_known, epoch = _parse_exif_datetime(
                    raw_cap, sidecar.get("offset_time")
                )
                capture_time = parsed_iso
                capture_timezone_known = bool(raw_tz_known) if raw_tz_known is not None else tz_known
                capture_epoch = epoch
                provenance = sidecar.get(
                    "capture_time_provenance",
                    "exif_with_tz" if capture_timezone_known else "exif_naive_clock",
                )
            if sidecar.get("has_gps_metadata"):
                has_gps = True
        except Exception:
            pass

    if file_path.suffix.lower() in IMAGE_EXTENSIONS:
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                exif = img.getexif()
                if exif:
                    # 34853 is GPSInfo tag
                    if 34853 in exif:
                        has_gps = True
                    if capture_time is None:
                        # Check Exif IFD (0x8769) as well as top-level tags
                        exif_ifd = {}
                        try:
                            exif_ifd = exif.get_ifd(0x8769) or {}
                        except Exception:
                            exif_ifd = {}
                        dt_orig = (
                            exif_ifd.get(36867)
                            or exif.get(36867)
                            or exif_ifd.get(36868)
                            or exif.get(36868)
                            or exif.get(306)
                        )
                        offset_orig = exif_ifd.get(36881) or exif.get(36881)
                        if dt_orig:
                            parsed_iso, tz_known, epoch = _parse_exif_datetime(
                                str(dt_orig), str(offset_orig) if offset_orig else None
                            )
                            if parsed_iso:
                                capture_time = parsed_iso
                                capture_timezone_known = tz_known
                                capture_epoch = epoch
                                provenance = "exif_with_tz" if tz_known else "exif_naive_clock"
        except Exception:
            pass

    asset_id = f"local-{file_path.stem}-{sha256_hash[:10]}"
    return {
        "asset_id": asset_id,
        "filename": file_path.name,
        "local_source_path": str(file_path.resolve()),
        "sha256": sha256_hash,
        "media_type": "video" if file_path.suffix.lower() in VIDEO_EXTENSIONS else "image",
        "width": width,
        "height": height,
        "capture_time": capture_time,
        "capture_timezone_known": capture_timezone_known,
        "capture_time_provenance": provenance,
        "capture_epoch": capture_epoch,
        "import_time": import_time_iso,
        "has_gps_metadata": has_gps,
    }


def cache_clean_inspection_image(source_path: Path, dest_path: Path, max_dim: int = 1600) -> Path:
    """
    Create a clean RGB copy in `state/media_cache/` with all EXIF/GPS metadata removed.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as img:
        img = ImageOps.exif_transpose(img)
        rgb = img.convert("RGB")
        if max(rgb.size) > max_dim:
            rgb.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        # Creating a fresh Image copy guarantees no EXIF/XMP/IPTC metadata carries over
        clean = Image.new("RGB", rgb.size)
        clean.paste(rgb)
        clean.save(dest_path, format="JPEG", quality=92, optimize=True)
    return dest_path


# ---------------------------------------------------------------------------
# Immich Restricted Album Adapter
# ---------------------------------------------------------------------------


def fetch_immich_album_assets(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Fetch assets ONLY from the configured `IMMICH_WILDLIFE_ALBUM_ID`.
    Verifies the album boundary at the application level before returning any asset.
    """
    boundary = validate_selection_boundary(config)
    if boundary["mode"] != "immich":
        raise SelectionBoundaryError("fetch_immich_album_assets called when mode is not 'immich'.")

    album_id = config["immich_wildlife_album_id"]
    base_url = config["immich_base_url"]
    api_key = config["immich_api_key"]

    album_url = f"{base_url}/api/albums/{urllib.parse.quote(album_id)}"
    req = urllib.request.Request(
        album_url,
        headers={
            "Accept": "application/json",
            "x-api-key": api_key,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        safe_msg = redact_secrets(str(exc), config)
        raise WildlifeDeskError(f"Failed to read restricted Immich album {album_id}: {safe_msg}") from exc

    # Application-level selection boundary verification
    returned_id = str(payload.get("id", ""))
    if returned_id != album_id:
        raise SelectionBoundaryError(
            f"Immich album response ID '{returned_id}' did not match configured "
            f"IMMICH_WILDLIFE_ALBUM_ID '{album_id}'. Aborting intake."
        )

    raw_assets = payload.get("assets") or []

    # Immich v3 omits inline `assets` on GET /api/albums/{id}; fetch via POST /api/search/metadata
    if not raw_assets and int(payload.get("assetCount") or 0) > 0:
        page = 1
        search_url = f"{base_url}/api/search/metadata"
        while True:
            search_body = json.dumps(
                {
                    "albumIds": [album_id],
                    "withExif": True,
                    "page": page,
                    "size": 250,
                }
            ).encode("utf-8")
            s_req = urllib.request.Request(
                search_url,
                data=search_body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(s_req, timeout=20) as s_resp:
                    s_payload = json.loads(s_resp.read().decode("utf-8"))
            except Exception as exc:
                safe_msg = redact_secrets(str(exc), config)
                raise WildlifeDeskError(
                    f"Failed to query Immich metadata for restricted album {album_id}: {safe_msg}"
                ) from exc

            assets_block = s_payload.get("assets") or {}
            items = assets_block.get("items") or []
            if not items:
                break
            raw_assets.extend(items)
            next_page = assets_block.get("nextPage")
            if not next_page:
                break
            page = int(next_page)

    normalized: List[Dict[str, Any]] = []

    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("id", "")).strip()
        if not asset_id:
            continue
        asset_type = str(item.get("type", "IMAGE")).upper()
        exif_info = item.get("exifInfo") or {}
        raw_dt_orig = exif_info.get("dateTimeOriginal")
        tz_str = exif_info.get("timeZone")
        has_gps = bool(exif_info.get("latitude") is not None or exif_info.get("longitude") is not None)

        capture_time = None
        tz_known = False
        capture_epoch = None
        provenance = "import_only_no_exif"

        if raw_dt_orig:
            parsed_iso, parsed_tz_known, epoch = _parse_exif_datetime(str(raw_dt_orig))
            capture_time = parsed_iso
            # Only trust timezone if Immich explicitly recorded an exifInfo.timeZone
            tz_known = bool(tz_str) and parsed_tz_known
            capture_epoch = epoch
            provenance = "immich_exif_tz" if tz_known else "immich_exif_naive"

        import_time = item.get("fileCreatedAt") or item.get("createdAt")
        checksum = item.get("checksum") or hashlib.sha256(asset_id.encode("utf-8")).hexdigest()

        normalized.append(
            {
                "asset_id": f"immich-{asset_id}",
                "immich_raw_id": asset_id,
                "album_id": album_id,
                "filename": item.get("originalFileName") or f"{asset_id}.jpg",
                "sha256": str(checksum),
                "media_type": "video" if asset_type == "VIDEO" else "image",
                "width": int(exif_info.get("exifImageWidth") or 0),
                "height": int(exif_info.get("exifImageHeight") or 0),
                "capture_time": capture_time,
                "capture_timezone_known": tz_known,
                "capture_time_provenance": provenance,
                "capture_epoch": capture_epoch,
                "import_time": import_time,
                "has_gps_metadata": has_gps,
            }
        )

    return normalized


def download_immich_asset_to_cache(
    config: Dict[str, Any], immich_raw_id: str, dest_path: Path, media_type: str = "image"
) -> Path:
    """Download an approved Immich asset preview/original into local EXIF-stripped cache."""
    validate_selection_boundary(config)
    base_url = config["immich_base_url"]
    api_key = config["immich_api_key"]
    if media_type == "video":
        url = f"{base_url}/api/assets/{urllib.parse.quote(immich_raw_id)}/thumbnail?size=preview"
    else:
        url = f"{base_url}/api/assets/{urllib.parse.quote(immich_raw_id)}/original"
    req = urllib.request.Request(
        url,
        headers={"x-api-key": api_key, "Accept": "application/octet-stream"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw_bytes = resp.read()
    except Exception as exc:
        safe_msg = redact_secrets(str(exc), config)
        raise WildlifeDeskError(f"Failed to download Immich asset {immich_raw_id}: {safe_msg}") from exc

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(raw_bytes)
        tmp_path = Path(tmp.name)
    try:
        return cache_clean_inspection_image(tmp_path, dest_path)
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Conservative Burst Grouping & Sighting Discovery
# ---------------------------------------------------------------------------


def group_assets_into_sightings(
    assets: List[Dict[str, Any]], burst_window_seconds: int = 180
) -> List[List[Dict[str, Any]]]:
    """
    Conservatively group related captures into sightings.
    - Deduplicates identical asset SHA-256 hashes or asset IDs.
    - Groups adjacent captures ONLY when both have parseable capture timestamps
      with the same provenance and `0 <= delta <= burst_window_seconds`.
    - Captures without reliable capture timestamps are never merged into multi-asset bursts.
    """
    seen_ids = set()
    seen_hashes = set()
    unique_assets: List[Dict[str, Any]] = []

    # Sort deterministically by (has_capture_time, capture_epoch, filename, asset_id)
    sorted_input = sorted(
        assets,
        key=lambda a: (
            0 if a.get("capture_epoch") is not None else 1,
            a.get("capture_epoch") if a.get("capture_epoch") is not None else float("inf"),
            str(a.get("filename", "")),
            str(a.get("asset_id", "")),
        ),
    )

    for asset in sorted_input:
        aid = asset.get("asset_id")
        sha = asset.get("sha256")
        if aid in seen_ids or (sha and sha in seen_hashes):
            continue
        seen_ids.add(aid)
        if sha:
            seen_hashes.add(sha)
        unique_assets.append(asset)

    groups: List[List[Dict[str, Any]]] = []
    for asset in unique_assets:
        if not groups:
            groups.append([asset])
            continue

        prev_group = groups[-1]
        prev_asset = prev_group[-1]

        cur_epoch = asset.get("capture_epoch")
        prev_epoch = prev_asset.get("capture_epoch")
        cur_prov = asset.get("capture_time_provenance")
        prev_prov = prev_asset.get("capture_time_provenance")

        can_group = (
            cur_epoch is not None
            and prev_epoch is not None
            and cur_prov == prev_prov
            and cur_prov != "import_only_no_exif"
            and 0 <= (cur_epoch - prev_epoch) <= burst_window_seconds
        )
        if can_group:
            prev_group.append(asset)
        else:
            groups.append([asset])

    return groups


def compute_event_id(group: List[Dict[str, Any]]) -> str:
    """Compute a stable, deterministic event ID from sorted asset hashes/IDs."""
    tokens = sorted(str(a.get("sha256") or a.get("asset_id")) for a in group)
    digest = hashlib.sha256("|".join(tokens).encode("utf-8")).hexdigest()[:12]
    return f"evt-{digest}"


def discover_sightings(
    env_override: Optional[Dict[str, str]] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Run incremental, idempotent intake from the configured wildlife selection.
    Creates or updates `discovered` records in `state/dispatches/<event_id>.json`
    without duplicating existing sightings or regressing drafted/published states.
    """
    config = load_config(env_override=env_override, repo_dir=repo_dir)
    boundary = validate_selection_boundary(config)
    dirs = ensure_runtime_dirs(config)

    raw_assets: List[Dict[str, Any]] = []
    if boundary["mode"] == "local":
        sel_dir = Path(boundary["selection_id"])
        for entry in sorted(sel_dir.iterdir()):
            if entry.is_file() and entry.suffix.lower() in (IMAGE_EXTENSIONS | VIDEO_EXTENSIONS):
                raw_assets.append(inspect_local_image_metadata(entry))
    elif boundary["mode"] == "immich":
        raw_assets = fetch_immich_album_assets(config)

    groups = group_assets_into_sightings(raw_assets, config["burst_window_seconds"])

    created_ids: List[str] = []
    existing_ids: List[str] = []
    sighting_summaries: List[Dict[str, Any]] = []

    for group in groups:
        event_id = compute_event_id(group)
        dispatch_path = dirs["dispatches"] / f"{event_id}.json"
        event_cache_dir = dirs["media_cache"] / event_id
        event_cache_dir.mkdir(parents=True, exist_ok=True)

        cached_files: Dict[str, str] = {}
        for asset in group:
            aid = asset["asset_id"]
            dest_file = event_cache_dir / f"{aid}.jpg"
            if not dest_file.exists():
                if boundary["mode"] == "local" and asset.get("local_source_path") and asset["media_type"] == "image":
                    cache_clean_inspection_image(Path(asset["local_source_path"]), dest_file)
                elif boundary["mode"] == "immich" and asset.get("immich_raw_id"):
                    download_immich_asset_to_cache(
                        config, asset["immich_raw_id"], dest_file, media_type=asset.get("media_type", "image")
                    )
            if dest_file.exists():
                cached_files[aid] = str(dest_file)

        if dispatch_path.exists():
            record = json.loads(dispatch_path.read_text(encoding="utf-8"))
            existing_ids.append(event_id)
        else:
            first = group[0]
            all_tz_known = all(bool(a.get("capture_timezone_known")) for a in group)
            any_gps = any(bool(a.get("has_gps_metadata")) for a in group)
            ordering_confidence = (
                "high_burst_timestamps"
                if len(group) > 1 and first.get("capture_time")
                else ("single_frame" if len(group) == 1 else "uncertain")
            )

            default_uncertainties: List[str] = []
            if len(group) == 1:
                default_uncertainties.append(
                    "A single still image cannot establish duration, direction of travel, or events before and after the frame."
                )
            if not first.get("capture_time"):
                default_uncertainties.append(
                    "Capture timestamp is absent from EXIF metadata; only file import time is recorded."
                )
            elif not all_tz_known:
                default_uncertainties.append(
                    "Camera clock timestamp lacks a verified timezone offset; exact wall-clock time is uncertain."
                )

            record = {
                "schema_version": SCHEMA_VERSION,
                "synthetic_example": False,
                "event_id": event_id,
                "state": "discovered",
                "discovered_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "source": {
                    "selection_mode": boundary["mode"],
                    "selection_description": boundary["description"],
                    "asset_ids": [a["asset_id"] for a in group],
                    "assets": [
                        {
                            "asset_id": a["asset_id"],
                            "filename": a["filename"],
                            "sha256": a["sha256"],
                            "media_type": a["media_type"],
                            "width": a["width"],
                            "height": a["height"],
                            "capture_time": a["capture_time"],
                            "capture_timezone_known": a["capture_timezone_known"],
                            "capture_time_provenance": a["capture_time_provenance"],
                            "import_time": a["import_time"],
                            "cached_preview_path": cached_files.get(a["asset_id"]),
                        }
                        for a in group
                    ],
                    "capture_time": first.get("capture_time"),
                    "capture_timezone_known": all_tz_known,
                    "capture_time_provenance": first.get("capture_time_provenance"),
                    "ordering_confidence": ordering_confidence,
                    "gps_metadata_stripped": any_gps,
                    "public_location": None,
                },
                "observations": [],
                "uncertainties": default_uncertainties,
                "privacy_screening": {
                    "evaluated": False,
                    "contains_people": "not_evaluated",
                    "contains_identifying_property": "not_evaluated",
                    "is_empty_frame": False,
                    "notes": None,
                },
                "editorial": {
                    "section": None,
                    "headline": None,
                    "caption": None,
                    "character_label": None,
                    "selected_asset_id": first["asset_id"],
                },
                "production": {
                    "cover_path": None,
                    "square_preview_path": None,
                    "thumb_preview_path": None,
                    "layout_position": "bottom-left",
                    "crop_focus_x": 0.5,
                    "crop_focus_y": 0.5,
                    "font_size_px": None,
                    "wrapped_lines": [],
                    "overflow_rejected": False,
                    "output_sha256": None,
                    "template_version": RENDERER_TEMPLATE_VERSION,
                },
                "eligibility": {
                    "privacy_check": "not_evaluated",
                    "factual_support_check": "not_evaluated",
                    "publishable": False,
                    "hold_reasons": ["Newly discovered sighting; observations and editorial review pending."],
                },
                "publication": {
                    "destination": "instagram",
                    "publication_key": f"instagram:{event_id}",
                    "remote_post_id": None,
                    "published_at": None,
                    "reconcile_required": False,
                    "attempts": [],
                },
            }
            _save_dispatch_record(dispatch_path, record, config)
            created_ids.append(event_id)

        sighting_summaries.append(
            {
                "event_id": event_id,
                "state": record["state"],
                "asset_count": len(record["source"]["asset_ids"]),
                "capture_time": record["source"]["capture_time"],
                "capture_timezone_known": record["source"]["capture_timezone_known"],
                "cached_previews": [
                    a.get("cached_preview_path")
                    for a in record["source"].get("assets", [])
                    if a.get("cached_preview_path")
                ],
                "headline": (record.get("editorial") or {}).get("headline"),
                "publishable": (record.get("eligibility") or {}).get("publishable", False),
            }
        )

    return {
        "selection_boundary": boundary,
        "total_assets_scanned": len(raw_assets),
        "total_sightings": len(groups),
        "new_sightings_created": len(created_ids),
        "existing_sightings_preserved": len(existing_ids),
        "created_event_ids": created_ids,
        "existing_event_ids": existing_ids,
        "sightings": sighting_summaries,
    }


# ---------------------------------------------------------------------------
# State Persistence & Archive Continuity
# ---------------------------------------------------------------------------


def _save_dispatch_record(path: Path, record: Dict[str, Any], config: Dict[str, Any]) -> None:
    record["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    raw_json = json.dumps(record, indent=2)
    clean_json = redact_secrets(raw_json, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(clean_json + "\n", encoding="utf-8")


def load_dispatch_record(
    event_id: str, env_override: Optional[Dict[str, str]] = None, repo_dir: Optional[Path] = None
) -> Dict[str, Any]:
    config = load_config(env_override=env_override, repo_dir=repo_dir)
    dirs = ensure_runtime_dirs(config)
    path = dirs["dispatches"] / f"{event_id}.json"
    if not path.exists():
        raise WildlifeDeskError(f"Dispatch record not found for event_id '{event_id}'.")
    return json.loads(path.read_text(encoding="utf-8"))


def list_dispatches(
    state_filter: Optional[str] = None,
    env_override: Optional[Dict[str, str]] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    config = load_config(env_override=env_override, repo_dir=repo_dir)
    dirs = ensure_runtime_dirs(config)
    items: List[Dict[str, Any]] = []
    counts_by_state: Dict[str, int] = {s: 0 for s in VALID_STATES}

    for p in sorted(dirs["dispatches"].glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        st = rec.get("state", "discovered")
        counts_by_state[st] = counts_by_state.get(st, 0) + 1
        if state_filter and st != state_filter:
            continue
        items.append(rec)

    return {
        "total_count": len(items),
        "counts_by_state": counts_by_state,
        "dispatches": items,
    }


def get_continuity_context(
    species: Optional[str] = None,
    exclude_event_id: Optional[str] = None,
    env_override: Optional[Dict[str, str]] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Check the local dispatch archive for prior confirmed/likely sightings of a species.
    Used to verify whether headlines like 'RACCOON RETURNS' or 'THIRD VISIT' have factual support.
    """
    all_records = list_dispatches(env_override=env_override, repo_dir=repo_dir)["dispatches"]
    species_counts: Dict[str, List[str]] = {}

    for rec in all_records:
        eid = rec.get("event_id")
        if exclude_event_id and eid == exclude_event_id:
            continue
        if rec.get("synthetic_example"):
            continue
        if rec.get("state") == "held" and (rec.get("privacy_screening") or {}).get("is_empty_frame"):
            continue
        seen_in_event = set()
        for obs in rec.get("observations") or []:
            sp = str(obs.get("species") or "").strip().lower()
            cert = str(obs.get("certainty") or "confirmed").strip().lower()
            if sp and sp != "unknown" and cert in ("confirmed", "likely"):
                seen_in_event.add(sp)
        for sp in seen_in_event:
            species_counts.setdefault(sp, []).append(eid)

    if species:
        sp_key = species.strip().lower()
        prior_events = species_counts.get(sp_key, [])
        return {
            "species": sp_key,
            "prior_sighting_count": len(prior_events),
            "prior_event_ids": prior_events,
            "can_claim_return": len(prior_events) >= 1,
            "visit_number_if_included": len(prior_events) + 1,
            "identity_rule": (
                "Repeat species sightings support 'returns to property' or 'another visit', "
                "but individual identity must remain uncertain unless distinctive physical evidence is visible."
            ),
        }

    return {
        "species_archive": {
            sp: {
                "sighting_count": len(eids),
                "event_ids": eids,
                "can_claim_return": len(eids) >= 1,
            }
            for sp, eids in sorted(species_counts.items())
        },
        "available_sections": list(VALID_SECTIONS),
    }


# ---------------------------------------------------------------------------
# Observations & Privacy Screening
# ---------------------------------------------------------------------------


def record_observations(
    event_id: str,
    observations: List[Dict[str, Any]],
    uncertainties: Optional[List[str]] = None,
    contains_people: Any = False,
    contains_identifying_property: Any = False,
    is_empty_frame: bool = False,
    privacy_notes: Optional[str] = None,
    synthetic_example: Optional[bool] = None,
    env_override: Optional[Dict[str, str]] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Record structured, asset-linked observations and privacy screening for a sighting.
    Validates that every observation claim cites valid `source_asset_ids` belonging to the sighting.
    """
    config = load_config(env_override=env_override, repo_dir=repo_dir)
    dirs = ensure_runtime_dirs(config)
    dispatch_path = dirs["dispatches"] / f"{event_id}.json"
    record = load_dispatch_record(event_id, env_override=env_override, repo_dir=repo_dir)

    valid_asset_ids = set(record["source"]["asset_ids"])
    normalized_obs: List[Dict[str, Any]] = []

    for idx, obs in enumerate(observations):
        claim = str(obs.get("claim") or "").strip()
        if not claim:
            raise WildlifeDeskError(f"Observation #{idx + 1} is missing a 'claim' description.")
        cited_ids = obs.get("source_asset_ids") or record["source"]["asset_ids"]
        if isinstance(cited_ids, str):
            cited_ids = [cited_ids]
        unknown_ids = [aid for aid in cited_ids if aid not in valid_asset_ids]
        if not cited_ids or unknown_ids:
            raise WildlifeDeskError(
                f"Observation #{idx + 1} references invalid source_asset_ids {unknown_ids}. "
                f"Valid asset IDs for {event_id}: {sorted(valid_asset_ids)}"
            )
        certainty = str(obs.get("certainty") or "confirmed").strip().lower()
        if certainty not in ("confirmed", "likely", "uncertain", "illustrative-only"):
            raise WildlifeDeskError(
                f"Observation #{idx + 1} certainty '{certainty}' must be 'confirmed', 'likely', or 'uncertain'."
            )
        species = str(obs.get("species") or "").strip().lower() or None
        focal_point = obs.get("focal_point")  # Optional [x_norm, y_norm] in 0..1

        normalized_obs.append(
            {
                "claim": claim,
                "source_asset_ids": list(cited_ids),
                "species": species,
                "visible_behavior": str(obs.get("visible_behavior") or "").strip() or None,
                "certainty": certainty,
                "focal_point": focal_point,
            }
        )

    merged_uncertainties = list(record.get("uncertainties") or [])
    for u in uncertainties or []:
        u_clean = str(u).strip()
        if u_clean and u_clean not in merged_uncertainties:
            merged_uncertainties.append(u_clean)

    # Always note individual identity uncertainty unless explicitly proven
    if not any("individual identity" in u.lower() for u in merged_uncertainties) and not is_empty_frame:
        merged_uncertainties.append("Individual animal identity cannot be established from camera evidence alone.")

    if synthetic_example is not None:
        record["synthetic_example"] = bool(synthetic_example)

    record["observations"] = normalized_obs
    record["uncertainties"] = merged_uncertainties
    record["privacy_screening"] = {
        "evaluated": True,
        "contains_people": contains_people,
        "contains_identifying_property": contains_identifying_property,
        "is_empty_frame": bool(is_empty_frame),
        "notes": privacy_notes,
    }

    # Auto-adjust focal point if first observation supplies one
    for obs in normalized_obs:
        fp = obs.get("focal_point")
        if isinstance(fp, (list, tuple)) and len(fp) == 2:
            try:
                record["production"]["crop_focus_x"] = max(0.0, min(1.0, float(fp[0])))
                record["production"]["crop_focus_y"] = max(0.0, min(1.0, float(fp[1])))
                break
            except (TypeError, ValueError):
                pass

    # Evaluate eligibility after observation update
    _recompute_eligibility(record, env_override=env_override, repo_dir=repo_dir)
    if record["eligibility"]["privacy_check"] != "passed" or is_empty_frame:
        record["state"] = "held"
    elif record["state"] == "discovered":
        record["state"] = "observed"

    _save_dispatch_record(dispatch_path, record, config)
    return record


# ---------------------------------------------------------------------------
# Editorial Validation & Eligibility Gate
# ---------------------------------------------------------------------------


def _recompute_eligibility(
    record: Dict[str, Any],
    env_override: Optional[Dict[str, str]] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Deterministically evaluate `privacy_check`, `factual_support_check`, `publishable`,
    and `hold_reasons` according to `docs/EDITORIAL.md` and `docs/IMPLEMENTATION.md`.
    Missing or uncertain checks MUST hold the post.
    """
    hold_reasons: List[str] = []

    # 1. Synthetic check
    if record.get("synthetic_example"):
        hold_reasons.append("Synthetic example or test fixture. Never publish as a real sighting.")

    # 2. Privacy screening check
    priv = record.get("privacy_screening") or {}
    if not priv.get("evaluated"):
        privacy_status = "not_evaluated"
        hold_reasons.append("Privacy screening has not been evaluated.")
    else:
        people = priv.get("contains_people")
        prop = priv.get("contains_identifying_property")
        if people is True:
            privacy_status = "failed"
            hold_reasons.append("Hold: Capture contains a person or human activity.")
        elif prop is True:
            privacy_status = "failed"
            hold_reasons.append("Hold: Capture contains identifying property details, addresses, or vehicles.")
        elif people is not False or prop is not False:
            privacy_status = "uncertain"
            hold_reasons.append("Hold: Privacy status is uncertain; ambiguous material stays local.")
        else:
            privacy_status = "passed"

    # 3. Factual support & editorial rules check
    observations = record.get("observations") or []
    editorial = record.get("editorial") or {}
    headline = str(editorial.get("headline") or "").strip()
    caption = str(editorial.get("caption") or "").strip()
    is_empty = bool(priv.get("is_empty_frame"))

    factual_issues: List[str] = []

    if is_empty:
        factual_issues.append("No story: Frame is empty or triggered by wind/foliage with no visible wildlife.")

    if not observations:
        factual_issues.append("No recorded observations linked to source assets.")
    else:
        valid_assets = set((record.get("source") or {}).get("asset_ids") or [])
        supported_obs = [
            o
            for o in observations
            if o.get("certainty") in ("confirmed", "likely")
            and o.get("source_asset_ids")
            and set(o.get("source_asset_ids")).issubset(valid_assets)
        ]
        if not supported_obs:
            factual_issues.append(
                "No confirmed or likely wildlife observation backed by valid source assets."
            )

    if not headline or not caption:
        factual_issues.append("Editorial headline and caption draft are incomplete.")
    else:
        # Check headline length & style
        headline_words = [w for w in headline.split() if w.strip()]
        if len(headline_words) < 3 or len(headline_words) > 14:
            factual_issues.append(
                f"Headline length ({len(headline_words)} words) is outside the editorial bound (3-14 words; target 5-12)."
            )
        if EMOJI_RE.search(headline) or EMOJI_RE.search(caption):
            factual_issues.append("Emojis are not permitted in Wildlife Desk headlines or captions.")
        if '"' in headline or "“" in headline or "”" in headline:
            factual_issues.append("Fabricated quotations are not permitted in headlines.")
        for pat in BANNED_HYPE_PATTERNS:
            if re.search(pat, caption, flags=re.IGNORECASE) or re.search(pat, headline, flags=re.IGNORECASE):
                factual_issues.append(f"Disallowed engagement bait or hashtag pattern matched: {pat}")

        combined_text = f"{headline}\n{caption}"

        # Check return / repeat visit premise against archive continuity
        if any(re.search(pat, combined_text, flags=re.IGNORECASE) for pat in RETURN_PREMISE_PATTERNS):
            confirmed_species = [
                str(o.get("species")).strip().lower()
                for o in observations
                if o.get("species")
                and str(o.get("species")).strip().lower() != "unknown"
                and o.get("certainty") in ("confirmed", "likely")
            ]
            has_return_support = False
            for sp in set(confirmed_species):
                ctx = get_continuity_context(
                    species=sp,
                    exclude_event_id=record.get("event_id"),
                    env_override=env_override,
                    repo_dir=repo_dir,
                )
                if ctx.get("can_claim_return"):
                    has_return_support = True
                    break
            # Check specific "third visit" premise
            if re.search(r"\bthird\s+visit\b", combined_text, flags=re.IGNORECASE):
                has_third_support = False
                for sp in set(confirmed_species):
                    ctx = get_continuity_context(
                        species=sp,
                        exclude_event_id=record.get("event_id"),
                        env_override=env_override,
                        repo_dir=repo_dir,
                    )
                    if ctx.get("prior_sighting_count", 0) >= 2:
                        has_third_support = True
                        break
                if not has_third_support:
                    factual_issues.append(
                        "Unsupported premise: 'Third visit' requires at least two prior recorded sightings of that species."
                    )
            elif not has_return_support:
                factual_issues.append(
                    "Unsupported premise: Headline or caption claims animal 'returns' or repeats a visit without an earlier recorded sighting of that species."
                )

        # Check time-of-day claims when capture_time is completely missing
        source_info = record.get("source") or {}
        if not source_info.get("capture_time"):
            if any(re.search(pat, combined_text, flags=re.IGNORECASE) for pat in TIME_SPECIFIC_PATTERNS):
                # Allow 'after dark' / 'overnight' ONLY if an observation explicitly notes infrared night mode
                obs_text = " ".join(str(o.get("claim", "")) for o in observations).lower()
                if "infrared" not in obs_text and "night" not in obs_text:
                    factual_issues.append(
                        "Unsupported time claim: Capture timestamp is missing and infrared night evidence was not recorded."
                    )

        # Check precise clock claims when capture_timezone_known is False
        if not source_info.get("capture_timezone_known"):
            if re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\b", combined_text, flags=re.IGNORECASE):
                factual_issues.append(
                    "Unsupported clock precision: Camera timezone is unverified; omit exact clock hours like '2 a.m.'."
                )

    if not observations and not headline:
        factual_status = "not_evaluated"
    elif factual_issues:
        factual_status = "failed"
        hold_reasons.extend(factual_issues)
    else:
        factual_status = "passed"

    # 4. Production / cover rendering check
    prod = record.get("production") or {}
    if prod.get("overflow_rejected"):
        hold_reasons.append("Cover renderer rejected headline due to text overflow.")
    cover_ready = bool(
        prod.get("cover_path")
        and Path(prod["cover_path"]).exists()
        and not prod.get("overflow_rejected")
    )
    if headline and caption and not cover_ready and not factual_issues and privacy_status == "passed":
        hold_reasons.append("Front-page cover image has not been rendered yet.")

    publishable = (
        not record.get("synthetic_example")
        and privacy_status == "passed"
        and factual_status == "passed"
        and cover_ready
        and len(hold_reasons) == 0
    )

    record["eligibility"] = {
        "privacy_check": privacy_status,
        "factual_support_check": factual_status,
        "publishable": publishable,
        "hold_reasons": hold_reasons,
    }
    return record["eligibility"]


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
    env_override: Optional[Dict[str, str]] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Save an editorial headline and caption article for a sighting, validate factual
    and privacy rules, and optionally render the 1080x1350 front-page cover.
    """
    config = load_config(env_override=env_override, repo_dir=repo_dir)
    dirs = ensure_runtime_dirs(config)
    dispatch_path = dirs["dispatches"] / f"{event_id}.json"
    record = load_dispatch_record(event_id, env_override=env_override, repo_dir=repo_dir)

    clean_section = section.strip() if section else "Local Affairs"
    if clean_section not in VALID_SECTIONS:
        raise WildlifeDeskError(
            f"Invalid section '{clean_section}'. Choose from: {', '.join(VALID_SECTIONS)}"
        )

    valid_assets = record["source"]["asset_ids"]
    chosen_asset = selected_asset_id or record["editorial"].get("selected_asset_id") or valid_assets[0]
    if chosen_asset not in valid_assets:
        raise WildlifeDeskError(
            f"selected_asset_id '{chosen_asset}' is not in event {event_id} assets: {valid_assets}"
        )

    record["editorial"] = {
        "section": clean_section,
        "headline": headline.strip().upper(),
        "caption": caption.strip(),
        "character_label": character_label.strip() if character_label else None,
        "selected_asset_id": chosen_asset,
        "word_count": len(caption.strip().split()),
        "headline_word_count": len(headline.strip().split()),
    }

    if crop_focus_x is not None:
        record["production"]["crop_focus_x"] = max(0.0, min(1.0, float(crop_focus_x)))
    if crop_focus_y is not None:
        record["production"]["crop_focus_y"] = max(0.0, min(1.0, float(crop_focus_y)))
    if layout_position:
        record["production"]["layout_position"] = layout_position

    _recompute_eligibility(record, env_override=env_override, repo_dir=repo_dir)
    if record["eligibility"]["privacy_check"] == "passed" and record["eligibility"]["factual_support_check"] == "passed":
        record["state"] = "drafted"
    else:
        record["state"] = "held"

    _save_dispatch_record(dispatch_path, record, config)

    if auto_render_cover:
        record = render_dispatch_cover(
            event_id=event_id,
            layout_position=layout_position,
            crop_focus_x=record["production"]["crop_focus_x"],
            crop_focus_y=record["production"]["crop_focus_y"],
            env_override=env_override,
            repo_dir=repo_dir,
        )

    return record


def hold_dispatch(
    event_id: str,
    reason: str,
    is_empty_frame: Optional[bool] = None,
    env_override: Optional[Dict[str, str]] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Explicitly place a sighting on hold (e.g., empty frame, human visible, or no story)."""
    config = load_config(env_override=env_override, repo_dir=repo_dir)
    dirs = ensure_runtime_dirs(config)
    dispatch_path = dirs["dispatches"] / f"{event_id}.json"
    record = load_dispatch_record(event_id, env_override=env_override, repo_dir=repo_dir)

    if is_empty_frame is not None:
        record["privacy_screening"]["evaluated"] = True
        record["privacy_screening"]["is_empty_frame"] = bool(is_empty_frame)
        if record["privacy_screening"]["contains_people"] == "not_evaluated":
            record["privacy_screening"]["contains_people"] = False
        if record["privacy_screening"]["contains_identifying_property"] == "not_evaluated":
            record["privacy_screening"]["contains_identifying_property"] = False

    _recompute_eligibility(record, env_override=env_override, repo_dir=repo_dir)
    clean_reason = reason.strip()
    if clean_reason and clean_reason not in record["eligibility"]["hold_reasons"]:
        record["eligibility"]["hold_reasons"].insert(0, clean_reason)
    record["eligibility"]["publishable"] = False
    record["state"] = "held"

    _save_dispatch_record(dispatch_path, record, config)
    return record


# ---------------------------------------------------------------------------
# Deterministic 1080x1350 Front-Page Cover Renderer
# ---------------------------------------------------------------------------


def _load_font(size: int, bold: bool = True, repo_dir: Optional[Path] = None) -> ImageFont.FreeTypeFont:
    base = repo_dir or REPO_DIR
    candidates = [
        base / "assets" / "fonts" / ("BarlowCondensed-Bold.ttf" if bold else "BarlowCondensed-SemiBold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text_measured(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width_px: int,
) -> Tuple[List[str], bool]:
    """
    Wrap uppercase headline text deterministically by measured pixel width.
    Returns `(lines, overflowed)`. If any single word exceeds `max_width_px`, `overflowed` is True.
    """
    words = [w for w in text.strip().split() if w]
    if not words:
        return [], True

    lines: List[str] = []
    current_line = ""

    for word in words:
        word_w = draw.textlength(word, font=font)
        if word_w > max_width_px:
            return lines, True
        candidate = f"{current_line} {word}".strip() if current_line else word
        cand_w = draw.textlength(candidate, font=font)
        if cand_w <= max_width_px:
            current_line = candidate
        else:
            if current_line:
                lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)

    return lines, False


def _choose_auto_layout_position(crop_focus_x: float, crop_focus_y: float) -> str:
    """
    Choose text placement ('bottom-left', 'bottom-right', 'top-left', 'top-right')
    so the headline overlay avoids covering the animal's focal region.
    """
    # Default newspaper layout is bottom-left unless the animal is low-left in frame
    if crop_focus_y > 0.62 and crop_focus_x < 0.48:
        return "top-left" if crop_focus_y > 0.72 else "bottom-right"
    if crop_focus_x < 0.35 and crop_focus_y > 0.50:
        return "bottom-right"
    return "bottom-left"


def _crop_to_aspect_focus(
    img: Image.Image,
    target_w: int,
    target_h: int,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
    trim_bottom_hud: bool = True,
) -> Image.Image:
    """Crop source image to target aspect ratio centered around `(focus_x, focus_y)` and resize."""
    src_w, src_h = img.size
    # Trail-camera landscape frames (16:9) often burn a status bar into the bottom ~6.8%.
    # Trim that bottom strip on rendered covers while keeping the raw frame intact in the evidence cache.
    if trim_bottom_hud and (src_w / float(max(1, src_h))) > 1.45 and src_h >= 600:
        usable_h = int(round(src_h * 0.928))
        img = img.crop((0, 0, src_w, usable_h))
        src_w, src_h = img.size

    target_ratio = target_w / target_h
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        new_w = int(round(src_h * target_ratio))
        new_h = src_h
        center_x = int(round(focus_x * src_w))
        left = max(0, min(src_w - new_w, center_x - new_w // 2))
        top = 0
    else:
        new_w = src_w
        new_h = int(round(src_w / target_ratio))
        center_y = int(round(focus_y * src_h))
        left = 0
        top = max(0, min(src_h - new_h, center_y - new_h // 2))

    cropped = img.crop((left, top, left + new_w, top + new_h))
    return cropped.resize((target_w, target_h), Image.Resampling.LANCZOS)


def render_dispatch_cover(
    event_id: str,
    layout_position: Optional[str] = None,
    crop_focus_x: Optional[float] = None,
    crop_focus_y: Optional[float] = None,
    env_override: Optional[Dict[str, str]] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Deterministically render the 1080x1350 front-page cover, 1080x1080 profile-grid crop preview,
    and 270x338 thumbnail for a dispatch.
    Enforces measured font wrapping, outer margins, and overflow rejection.
    """
    config = load_config(env_override=env_override, repo_dir=repo_dir)
    dirs = ensure_runtime_dirs(config)
    dispatch_path = dirs["dispatches"] / f"{event_id}.json"
    record = load_dispatch_record(event_id, env_override=env_override, repo_dir=repo_dir)

    editorial = record.get("editorial") or {}
    headline = str(editorial.get("headline") or "").strip().upper()
    section = str(editorial.get("section") or "LOCAL AFFAIRS").strip().upper()
    if not headline:
        raise WildlifeDeskError(f"Cannot render cover for {event_id}: editorial.headline is empty.")

    selected_aid = editorial.get("selected_asset_id") or record["source"]["asset_ids"][0]
    source_path: Optional[Path] = None
    for asset in record["source"].get("assets", []):
        if asset.get("asset_id") == selected_aid:
            preview = asset.get("cached_preview_path")
            if preview and Path(preview).exists():
                source_path = Path(preview)
                break
    if source_path is None:
        # Fallback to cache path convention
        candidate = dirs["media_cache"] / event_id / f"{selected_aid}.jpg"
        if candidate.exists():
            source_path = candidate

    if source_path is None or not source_path.exists():
        raise WildlifeDeskError(
            f"Source image for asset '{selected_aid}' not found in local media cache."
        )

    fx = crop_focus_x if crop_focus_x is not None else float(record["production"].get("crop_focus_x", 0.5))
    fy = crop_focus_y if crop_focus_y is not None else float(record["production"].get("crop_focus_y", 0.5))
    pos = (layout_position or record["production"].get("layout_position") or "auto").strip().lower()
    if pos == "auto":
        pos = _choose_auto_layout_position(fx, fy)
    if pos not in ("bottom-left", "bottom-right", "top-left", "top-right"):
        pos = "bottom-left"

    canvas_w, canvas_h = 1080, 1350
    # 1:1 profile-grid crop spans y=135..1215. Keep masthead and headline inside safe zone!
    square_crop_margin_y = (canvas_h - canvas_w) // 2  # 135px
    margin_x = 76
    margin_top = square_crop_margin_y + 32  # 167px (safe inside 1:1 profile crop)
    margin_bottom = square_crop_margin_y + 42  # 177px (safe inside 1:1 profile crop)
    max_text_width = int((canvas_w - 2 * margin_x) * 0.88)
    max_lines = 5

    with Image.open(source_path) as src_img:
        src_img = ImageOps.exif_transpose(src_img).convert("RGB")
        base_canvas = _crop_to_aspect_focus(src_img, canvas_w, canvas_h, fx, fy).convert("RGBA")

    probe_draw = ImageDraw.Draw(base_canvas)
    chosen_font_size: Optional[int] = None
    chosen_font: Optional[ImageFont.FreeTypeFont] = None
    wrapped_lines: List[str] = []

    for candidate_size in (86, 78, 70, 62, 54):
        fnt = _load_font(candidate_size, bold=True, repo_dir=repo_dir)
        lines, word_overflow = _wrap_text_measured(probe_draw, headline, fnt, max_text_width)
        if not word_overflow and 1 <= len(lines) <= max_lines:
            chosen_font_size = candidate_size
            chosen_font = fnt
            wrapped_lines = lines
            break

    if chosen_font is None or chosen_font_size is None:
        # Deterministic overflow rejection
        record["production"]["overflow_rejected"] = True
        record["production"]["cover_path"] = None
        _recompute_eligibility(record, env_override=env_override, repo_dir=repo_dir)
        record["state"] = "held"
        _save_dispatch_record(dispatch_path, record, config)
        raise ValidationHoldError(
            f"Headline overflow rejected: '{headline}' exceeds {max_lines} lines or {max_text_width}px width "
            "even at minimum 54px font size. Shorten the headline (target 5-12 words)."
        )

    masthead_font = _load_font(28, bold=False, repo_dir=repo_dir)
    kicker_font = _load_font(26, bold=True, repo_dir=repo_dir)
    footer_font = _load_font(20, bold=False, repo_dir=repo_dir)

    # Build smooth contrast gradient overlay
    overlay = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)

    # Always draw a subtle top vignette for the masthead + bottom/top gradient for headline
    for y in range(0, 320):
        alpha = int(165 * (1.0 - (y / 320.0)) ** 1.5)
        ov_draw.line([(0, y), (canvas_w, y)], fill=(8, 10, 12, alpha))

    if pos.startswith("bottom"):
        grad_start = int(canvas_h * 0.42)
        for y in range(grad_start, canvas_h):
            t = (y - grad_start) / float(canvas_h - grad_start)
            alpha = int(220 * (t ** 1.35))
            ov_draw.line([(0, y), (canvas_w, y)], fill=(8, 10, 12, alpha))
    else:
        grad_end = int(canvas_h * 0.60)
        for y in range(0, grad_end):
            t = 1.0 - (y / float(grad_end))
            alpha = int(215 * (t ** 1.35))
            ov_draw.line([(0, y), (canvas_w, y)], fill=(8, 10, 12, alpha))

    composited = Image.alpha_composite(base_canvas, overlay)
    draw = ImageDraw.Draw(composited)

    # 1. Draw THE WILDLIFE DESK masthead at top-left (inside 1:1 profile-grid safe zone)
    masthead_text = "THE WILDLIFE DESK"
    draw.text((margin_x, margin_top), masthead_text, font=masthead_font, fill=(245, 247, 250, 245))
    mh_w = int(draw.textlength(masthead_text, font=masthead_font))
    rule_y = margin_top + 38
    draw.line([(margin_x, rule_y), (margin_x + mh_w + 18, rule_y)], fill=(245, 247, 250, 210), width=2)

    # 2. Compute headline block geometry
    line_spacing = int(round(chosen_font_size * 1.08))
    total_headline_h = len(wrapped_lines) * line_spacing
    kicker_gap = 42

    if pos.startswith("bottom"):
        block_bottom = canvas_h - margin_bottom
        headline_start_y = block_bottom - total_headline_h
        kicker_y = headline_start_y - kicker_gap
    else:
        kicker_y = rule_y + 36
        headline_start_y = kicker_y + kicker_gap

    align_right = pos.endswith("right")

    # 3. Draw section kicker (e.g. THE NIGHT SHIFT)
    kicker_w = draw.textlength(section, font=kicker_font)
    kicker_x = (canvas_w - margin_x - kicker_w) if align_right else margin_x
    draw.text((kicker_x, kicker_y), section, font=kicker_font, fill=(212, 220, 228, 235))

    # 4. Draw headline lines with subtle drop shadow for crisp thumbnail legibility
    for i, line in enumerate(wrapped_lines):
        y = headline_start_y + i * line_spacing
        line_w = draw.textlength(line, font=chosen_font)
        x = (canvas_w - margin_x - line_w) if align_right else margin_x
        draw.text((x + 2, y + 3), line, font=chosen_font, fill=(0, 0, 0, 180))
        draw.text((x, y), line, font=chosen_font, fill=(255, 255, 255, 255))

    # 5. If synthetic example, stamp required provenance footer
    if record.get("synthetic_example"):
        footer_text = "ILLUSTRATIVE MOCKUP • SYNTHETIC TEST FIXTURE"
        draw.text(
            (margin_x, canvas_h - 46),
            footer_text,
            font=footer_font,
            fill=(200, 206, 212, 215),
        )

    # Save clean RGB outputs (zero EXIF metadata)
    event_out_dir = dirs["output"] / event_id
    event_out_dir.mkdir(parents=True, exist_ok=True)
    cover_path = event_out_dir / "cover-1080x1350.jpg"
    square_path = event_out_dir / "crop-preview-1080x1080.jpg"
    thumb_path = event_out_dir / "thumb-270x338.jpg"

    final_rgb = Image.new("RGB", (canvas_w, canvas_h))
    final_rgb.paste(composited.convert("RGB"))
    final_rgb.save(cover_path, format="JPEG", quality=94, optimize=True)

    # Center 1:1 profile-grid crop preview (1080x1080)
    top_crop = (canvas_h - canvas_w) // 2
    square_rgb = final_rgb.crop((0, top_crop, canvas_w, top_crop + canvas_w))
    square_rgb.save(square_path, format="JPEG", quality=90, optimize=True)

    # Small thumbnail preview (270x338)
    thumb_rgb = final_rgb.resize((270, 338), Image.Resampling.LANCZOS)
    thumb_rgb.save(thumb_path, format="JPEG", quality=88, optimize=True)

    output_sha256 = hashlib.sha256(cover_path.read_bytes()).hexdigest()

    record["production"] = {
        "cover_path": str(cover_path),
        "square_preview_path": str(square_path),
        "thumb_preview_path": str(thumb_path),
        "layout_position": pos,
        "crop_focus_x": fx,
        "crop_focus_y": fy,
        "font_size_px": chosen_font_size,
        "wrapped_lines": wrapped_lines,
        "overflow_rejected": False,
        "output_sha256": output_sha256,
        "template_version": RENDERER_TEMPLATE_VERSION,
        "rendered_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    _recompute_eligibility(record, env_override=env_override, repo_dir=repo_dir)
    if record["eligibility"]["publishable"]:
        record["state"] = "ready"
    elif record["eligibility"]["privacy_check"] != "passed" or record["eligibility"]["factual_support_check"] != "passed":
        record["state"] = "held"

    _save_dispatch_record(dispatch_path, record, config)
    return record


# ---------------------------------------------------------------------------
# Publishing Gate, Atomic Claims & Timeout Reconciliation
# ---------------------------------------------------------------------------


def publish_dispatch(
    event_id: str,
    simulate_remote_timeout: bool = False,
    dry_run: bool = False,
    env_override: Optional[Dict[str, str]] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Enforce publication kill-switch, eligibility checks, atomic lock claims, duplicate
    prevention, and timeout reconciliation before any remote Instagram submission.
    """
    config = load_config(env_override=env_override, repo_dir=repo_dir)
    dirs = ensure_runtime_dirs(config)
    dispatch_path = dirs["dispatches"] / f"{event_id}.json"
    record = load_dispatch_record(event_id, env_override=env_override, repo_dir=repo_dir)

    # 1. Kill-switch check (must block every outbound attempt when disabled)
    if not config.get("publish_enabled") and not dry_run:
        raise PublicationGateError(
            "Publishing is disabled (`PUBLISH_ENABLED=false`). No outbound request was made."
        )

    # 2. Prevent duplicate publication or un-reconciled timeout retry
    pub = record.get("publication") or {}
    if record.get("state") == "published" or pub.get("remote_post_id"):
        raise PublicationGateError(
            f"Dispatch {event_id} is already published (remote_post_id={pub.get('remote_post_id')}). "
            "Duplicate post blocked."
        )
    if pub.get("reconcile_required"):
        raise PublicationGateError(
            f"Dispatch {event_id} has an unresolved prior submission timeout (`reconcile_required=True`). "
            "Call `reconcile_publication` to verify remote platform state before retrying."
        )

    # 3. Re-verify eligibility deterministically
    eligibility = _recompute_eligibility(record, env_override=env_override, repo_dir=repo_dir)
    if not eligibility.get("publishable"):
        record["state"] = "held"
        _save_dispatch_record(dispatch_path, record, config)
        raise ValidationHoldError(
            f"Dispatch {event_id} is not eligible for publication: {eligibility.get('hold_reasons')}"
        )

    # 4. Atomic claim via exclusive lock file creation (`O_CREAT | O_EXCL`)
    lock_path = dirs["locks"] / f"{event_id}.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
            lock_file.write(
                json.dumps(
                    {
                        "event_id": event_id,
                        "pid": os.getpid(),
                        "claimed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    }
                )
            )
    except FileExistsError as exc:
        raise PublicationGateError(
            f"Concurrent publication lock active for {event_id} ({lock_path.name}). "
            "Overlapping worker blocked."
        ) from exc

    attempt_time = dt.datetime.now(dt.timezone.utc).isoformat()
    pub_key = pub.get("publication_key") or f"instagram:{event_id}"

    try:
        record["state"] = "publishing"
        _save_dispatch_record(dispatch_path, record, config)

        if simulate_remote_timeout:
            pub.setdefault("attempts", []).append(
                {
                    "attempted_at": attempt_time,
                    "publication_key": pub_key,
                    "outcome": "timeout_unknown_remote_state",
                    "error": "Simulated network timeout after submission; remote creation unknown.",
                }
            )
            pub["reconcile_required"] = True
            record["publication"] = pub
            record["state"] = "failed"
            _save_dispatch_record(dispatch_path, record, config)
            raise PublicationGateError(
                f"Submission for {event_id} timed out after request send; marked `reconcile_required=True`."
            )

        if dry_run:
            pub.setdefault("attempts", []).append(
                {
                    "attempted_at": attempt_time,
                    "publication_key": pub_key,
                    "outcome": "dry_run_validated",
                    "error": None,
                }
            )
            record["publication"] = pub
            record["state"] = "ready"
            _save_dispatch_record(dispatch_path, record, config)
            return record

        # Check Instagram credentials
        ig_account = config.get("instagram_account_id", "")
        ig_token = config.get("instagram_access_token", "")
        if not ig_account or not ig_token:
            pub.setdefault("attempts", []).append(
                {
                    "attempted_at": attempt_time,
                    "publication_key": pub_key,
                    "outcome": "missing_credentials",
                    "error": "INSTAGRAM_ACCOUNT_ID or INSTAGRAM_ACCESS_TOKEN not configured.",
                }
            )
            record["publication"] = pub
            record["state"] = "ready"
            _save_dispatch_record(dispatch_path, record, config)
            raise PublicationGateError(
                "Instagram credentials (INSTAGRAM_ACCOUNT_ID / INSTAGRAM_ACCESS_TOKEN) are not configured."
            )

        # Record controlled local receipt when in local test mode (`ig_account == "local-test-receipt"`)
        if ig_account == "local-test-receipt":
            remote_id = f"ig-local-{hashlib.sha256(pub_key.encode('utf-8')).hexdigest()[:10]}"
            published_at = dt.datetime.now(dt.timezone.utc).isoformat()
            pub["remote_post_id"] = remote_id
            pub["published_at"] = published_at
            pub["reconcile_required"] = False
            pub.setdefault("attempts", []).append(
                {
                    "attempted_at": attempt_time,
                    "publication_key": pub_key,
                    "outcome": "published",
                    "remote_post_id": remote_id,
                }
            )
            record["publication"] = pub
            record["state"] = "published"
            _save_dispatch_record(dispatch_path, record, config)

            receipt_path = dirs["receipts"] / f"{event_id}-receipt.json"
            receipt_path.write_text(
                json.dumps(
                    {
                        "event_id": event_id,
                        "publication_key": pub_key,
                        "remote_post_id": remote_id,
                        "published_at": published_at,
                        "cover_sha256": record["production"].get("output_sha256"),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return record

        # Official two-phase Meta Instagram Graph API publishing with ephemeral derivative staging
        stage_dir_raw = config.get("instagram_stage_dir", "")
        stage_base_url = config.get("instagram_stage_base_url", "")
        if not stage_dir_raw or not stage_base_url:
            raise PublicationGateError(
                "INSTAGRAM_STAGE_DIR and INSTAGRAM_STAGE_BASE_URL must be configured to temporarily "
                "stage the rendered cover JPEG for Meta's container creation API."
            )

        cover_path = Path(record["production"]["cover_path"])
        stage_dir = Path(stage_dir_raw).expanduser().resolve()
        stage_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(stage_dir, 0o755)
        except Exception:
            pass
        ephemeral_token = hashlib.sha256(f"{event_id}:{attempt_time}:{os.urandom(8).hex()}".encode("utf-8")).hexdigest()[:24]
        staged_filename = f"wd-{ephemeral_token}.jpg"
        staged_file_path = stage_dir / staged_filename
        public_image_url = f"{stage_base_url}/{staged_filename}"

        # Determine Graph host based on token type (IG... -> graph.instagram.com, EAA... -> graph.facebook.com)
        graph_host = (
            "https://graph.instagram.com/v21.0"
            if ig_token.startswith("IG")
            else "https://graph.facebook.com/v21.0"
        )
        target_id = "me" if (ig_token.startswith("IG") and ig_account.lower() in ("me", "self")) else ig_account
        caption_text = str((record.get("editorial") or {}).get("caption") or "").strip()

        shutil.copyfile(cover_path, staged_file_path)
        os.chmod(staged_file_path, 0o644)
        try:
            # Phase A: Create Media Container
            container_url = f"{graph_host}/{urllib.parse.quote(target_id)}/media"
            container_payload = urllib.parse.urlencode(
                {
                    "image_url": public_image_url,
                    "caption": caption_text,
                    "access_token": ig_token,
                }
            ).encode("utf-8")
            c_req = urllib.request.Request(
                container_url,
                data=container_payload,
                headers={"Accept": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(c_req, timeout=30) as c_resp:
                    c_data = json.loads(c_resp.read().decode("utf-8"))
            except Exception as exc:
                err_detail = str(exc)
                if isinstance(exc, urllib.error.HTTPError):
                    try:
                        err_detail = f"{exc.code} {exc.read().decode('utf-8', errors='replace')}"
                    except Exception:
                        pass
                safe_err = redact_secrets(err_detail, config)
                pub.setdefault("attempts", []).append(
                    {
                        "attempted_at": attempt_time,
                        "publication_key": pub_key,
                        "outcome": "container_creation_failed",
                        "error": safe_err,
                    }
                )
                record["publication"] = pub
                record["state"] = "failed"
                _save_dispatch_record(dispatch_path, record, config)
                raise PublicationGateError(f"Instagram container creation failed: {safe_err}") from exc

            creation_id = str(c_data.get("id") or "").strip()
            if not creation_id:
                raise PublicationGateError("Instagram API did not return a creation_id for media container.")

            # Poll container status briefly until FINISHED (up to 15 seconds)
            status_url = (
                f"{graph_host}/{urllib.parse.quote(creation_id)}"
                f"?fields=status_code,status&access_token={urllib.parse.quote(ig_token)}"
            )
            for _ in range(10):
                try:
                    with urllib.request.urlopen(status_url, timeout=10) as st_resp:
                        st_data = json.loads(st_resp.read().decode("utf-8"))
                    st_code = str(st_data.get("status_code") or "").upper()
                    if st_code in ("FINISHED", ""):
                        break
                    if st_code in ("ERROR", "EXPIRED"):
                        raise PublicationGateError(f"Instagram container {creation_id} failed with status: {st_data}")
                except PublicationGateError:
                    raise
                except Exception:
                    pass
                time.sleep(1.5)

            # Phase B: Publish Media Container
            publish_url = f"{graph_host}/{urllib.parse.quote(target_id)}/media_publish"
            publish_payload = urllib.parse.urlencode(
                {
                    "creation_id": creation_id,
                    "access_token": ig_token,
                }
            ).encode("utf-8")
            p_req = urllib.request.Request(
                publish_url,
                data=publish_payload,
                headers={"Accept": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(p_req, timeout=30) as p_resp:
                    p_data = json.loads(p_resp.read().decode("utf-8"))
            except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
                safe_err = redact_secrets(str(exc), config)
                pub.setdefault("attempts", []).append(
                    {
                        "attempted_at": attempt_time,
                        "publication_key": pub_key,
                        "creation_id": creation_id,
                        "outcome": "timeout_unknown_remote_state",
                        "error": safe_err,
                    }
                )
                pub["reconcile_required"] = True
                record["publication"] = pub
                record["state"] = "failed"
                _save_dispatch_record(dispatch_path, record, config)
                raise PublicationGateError(
                    f"Instagram media_publish timed out after submission (`reconcile_required=True`): {safe_err}"
                ) from exc
            except Exception as exc:
                safe_err = redact_secrets(str(exc), config)
                pub.setdefault("attempts", []).append(
                    {
                        "attempted_at": attempt_time,
                        "publication_key": pub_key,
                        "creation_id": creation_id,
                        "outcome": "publish_failed",
                        "error": safe_err,
                    }
                )
                record["publication"] = pub
                record["state"] = "failed"
                _save_dispatch_record(dispatch_path, record, config)
                raise PublicationGateError(f"Instagram media_publish failed: {safe_err}") from exc

            remote_id = str(p_data.get("id") or "").strip()
            published_at = dt.datetime.now(dt.timezone.utc).isoformat()
            pub["remote_post_id"] = remote_id
            pub["published_at"] = published_at
            pub["reconcile_required"] = False
            pub.setdefault("attempts", []).append(
                {
                    "attempted_at": attempt_time,
                    "publication_key": pub_key,
                    "creation_id": creation_id,
                    "outcome": "published",
                    "remote_post_id": remote_id,
                }
            )
            record["publication"] = pub
            record["state"] = "published"
            _save_dispatch_record(dispatch_path, record, config)

            receipt_path = dirs["receipts"] / f"{event_id}-receipt.json"
            receipt_path.write_text(
                json.dumps(
                    {
                        "event_id": event_id,
                        "publication_key": pub_key,
                        "creation_id": creation_id,
                        "remote_post_id": remote_id,
                        "published_at": published_at,
                        "cover_sha256": record["production"].get("output_sha256"),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return record
        finally:
            # Promptly expire the temporary public derivative file
            staged_file_path.unlink(missing_ok=True)
    finally:
        lock_path.unlink(missing_ok=True)


def reconcile_publication(
    event_id: str,
    remote_was_created: bool,
    remote_post_id: Optional[str] = None,
    env_override: Optional[Dict[str, str]] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Reconcile an ambiguous publication timeout before allowing any retry.
    - If `remote_was_created=True`, transitions to `published` and records `remote_post_id` without re-sending.
    - If `remote_was_created=False`, clears `reconcile_required` and restores `ready` state so a retry can proceed safely.
    """
    config = load_config(env_override=env_override, repo_dir=repo_dir)
    dirs = ensure_runtime_dirs(config)
    dispatch_path = dirs["dispatches"] / f"{event_id}.json"
    record = load_dispatch_record(event_id, env_override=env_override, repo_dir=repo_dir)

    pub = record.get("publication") or {}
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()

    if remote_was_created:
        resolved_id = remote_post_id or f"reconciled-{event_id}"
        pub["remote_post_id"] = resolved_id
        pub["published_at"] = now_iso
        pub["reconcile_required"] = False
        pub.setdefault("attempts", []).append(
            {
                "attempted_at": now_iso,
                "publication_key": pub.get("publication_key"),
                "outcome": "reconciled_already_published",
                "remote_post_id": resolved_id,
            }
        )
        record["publication"] = pub
        record["state"] = "published"
    else:
        pub["reconcile_required"] = False
        pub.setdefault("attempts", []).append(
            {
                "attempted_at": now_iso,
                "publication_key": pub.get("publication_key"),
                "outcome": "reconciled_not_published_safe_to_retry",
                "remote_post_id": None,
            }
        )
        record["publication"] = pub
        record["state"] = "ready" if record["eligibility"].get("publishable") else "held"

    _save_dispatch_record(dispatch_path, record, config)
    return record


# ---------------------------------------------------------------------------
# Local Web Pressroom Builder & Server
# ---------------------------------------------------------------------------


def _copy_pressroom_media(src_path_str: Optional[str], media_dir: Path, dest_name: str) -> Optional[str]:
    if not src_path_str:
        return None
    p = Path(src_path_str)
    if not p.exists():
        return None
    media_dir.mkdir(parents=True, exist_ok=True)
    dest = media_dir / dest_name
    if not dest.exists() or p.stat().st_mtime > dest.stat().st_mtime:
        shutil.copyfile(p, dest)
    return f"media/{dest_name}"


def _html_escape(s: Any) -> str:
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_pressroom(
    start_server: bool = False,
    env_override: Optional[Dict[str, str]] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Build the Local Web Pressroom (`output/pressroom/index.html`)
    for reviewing front-page covers, 1:1 profile crops, thumbnails, raw burst frames,
    Instagram captions, and the factual/privacy evidence ledger.
    """
    config = load_config(env_override=env_override, repo_dir=repo_dir)
    dirs = ensure_runtime_dirs(config)
    press_media_dir = dirs["pressroom"] / "media"
    press_media_dir.mkdir(parents=True, exist_ok=True)

    listed = list_dispatches(env_override=env_override, repo_dir=repo_dir)
    all_dispatches = listed["dispatches"]

    state_priority = {
        "ready": 0,
        "drafted": 1,
        "published": 2,
        "observed": 3,
        "held": 4,
        "failed": 5,
        "publishing": 6,
        "discovered": 7,
    }
    dispatches = sorted(
        all_dispatches,
        key=lambda r: (
            state_priority.get(r.get("state", "discovered"), 9),
            str(r.get("event_id", "")),
        ),
    )

    active_cards_html: List[str] = []
    wire_cards_html: List[str] = []

    for rec in dispatches:
        eid = _html_escape(rec.get("event_id"))
        state = _html_escape(rec.get("state", "discovered"))
        synthetic = bool(rec.get("synthetic_example"))
        ed = rec.get("editorial") or {}
        prod = rec.get("production") or {}
        elig = rec.get("eligibility") or {}
        src = rec.get("source") or {}

        headline = _html_escape(ed.get("headline") or "UNHEADLINED SIGHTING")
        section = _html_escape(ed.get("section") or "Unassigned Beat")
        caption = _html_escape(ed.get("caption") or "No dispatch article drafted yet.")
        char_label = _html_escape(ed.get("character_label") or "")

        cover_uri = _copy_pressroom_media(prod.get("cover_path"), press_media_dir, f"{eid}-cover.jpg")
        square_uri = _copy_pressroom_media(prod.get("square_preview_path"), press_media_dir, f"{eid}-square.jpg")
        thumb_uri = _copy_pressroom_media(prod.get("thumb_preview_path"), press_media_dir, f"{eid}-thumb.jpg")

        raw_assets = src.get("assets") or []
        raw_uri = (
            _copy_pressroom_media(raw_assets[0].get("cached_preview_path"), press_media_dir, f"{eid}-raw.jpg")
            if raw_assets
            else None
        )

        if state == "discovered":
            fname = _html_escape(raw_assets[0].get("filename") if raw_assets else eid)
            mtype = _html_escape(raw_assets[0].get("media_type", "image").upper() if raw_assets else "IMAGE")
            wire_cards_html.append(
                f"""
                <div class="wire-item">
                  {f'<img loading="lazy" src="{raw_uri}" alt="{fname}" />' if raw_uri else ''}
                  <div class="wire-meta">
                    <strong>{eid}</strong> <span>[{mtype}] {fname}</span>
                  </div>
                </div>
                """
            )
            continue

        obs_items = []
        for o in rec.get("observations") or []:
            obs_items.append(
                f"<li><strong>[{_html_escape(o.get('certainty', 'confirmed').upper())}]</strong> "
                f"{_html_escape(o.get('claim'))} "
                f"<span class='asset-ref'>({', '.join(_html_escape(a) for a in (o.get('source_asset_ids') or []))})</span></li>"
            )
        obs_html = "".join(obs_items) or "<li>No observations recorded yet.</li>"

        unc_items = [f"<li>{_html_escape(u)}</li>" for u in (rec.get("uncertainties") or [])]
        unc_html = "".join(unc_items) or "<li>None recorded.</li>"

        hold_items = [f"<li>{_html_escape(h)}</li>" for h in (elig.get("hold_reasons") or [])]
        hold_html = (
            f"<div class='hold-box'><strong>Hold / Gate Reasons:</strong><ul>{''.join(hold_items)}</ul></div>"
            if hold_items
            else "<div class='pass-box'>All factual support, privacy, and typography checks passed. Eligible for front page.</div>"
        )

        badge_class = f"badge-{state}"
        synth_badge = "<span class='badge badge-synth'>SYNTHETIC FIXTURE</span>" if synthetic else ""
        char_badge = f"<span class='badge badge-char'>{char_label}</span>" if char_label else ""

        view_buttons = []
        if cover_uri:
            view_buttons.append(
                f"<button class='tab-btn active' onclick=\"switchView('{eid}', 'cover')\">4:5 Cover (1080×1350)</button>"
            )
        if square_uri:
            view_buttons.append(
                f"<button class='tab-btn' onclick=\"switchView('{eid}', 'square')\">1:1 Grid Crop</button>"
            )
        if thumb_uri:
            view_buttons.append(
                f"<button class='tab-btn' onclick=\"switchView('{eid}', 'thumb')\">Small Thumbnail</button>"
            )
        if raw_uri:
            active_cls = " active" if not cover_uri else ""
            view_buttons.append(
                f"<button class='tab-btn{active_cls}' onclick=\"switchView('{eid}', 'raw')\">Raw Camera Frame</button>"
            )

        active_cards_html.append(
            f"""
            <article class="dispatch-card state-{state}" id="card-{eid}">
              <header class="card-header">
                <div>
                  <span class="kicker">{section}</span>
                  <h2>{headline}</h2>
                </div>
                <div class="badges">
                  <span class="badge {badge_class}">{state.upper()}</span>
                  {synth_badge}
                  {char_badge}
                  <span class="event-id">{eid}</span>
                </div>
              </header>

              <div class="card-grid">
                <div class="visual-col">
                  <div class="view-tabs">{''.join(view_buttons)}</div>
                  <div class="frame-stage">
                    {f'<img id="img-{eid}-cover" class="stage-img visible" src="{cover_uri}" alt="Rendered Cover" />' if cover_uri else ''}
                    {f'<img id="img-{eid}-square" class="stage-img" src="{square_uri}" alt="1:1 Profile Crop" />' if square_uri else ''}
                    {f'<img id="img-{eid}-thumb" class="stage-img thumb-size" src="{thumb_uri}" alt="Small Thumbnail" />' if thumb_uri else ''}
                    {f'<img id="img-{eid}-raw" class="stage-img {"visible" if not cover_uri else ""}" src="{raw_uri}" alt="Raw Capture" />' if raw_uri else ''}
                  </div>
                  <div class="meta-strip">
                    <span>Assets: {len(raw_assets)}</span>
                    <span>File Import/EXIF: {_html_escape(src.get('capture_time') or 'Unknown')}</span>
                    <span>TZ Verified: {'Yes' if src.get('capture_timezone_known') else 'No (Naive Camera Clock)'}</span>
                    <span>Layout: {_html_escape(prod.get('layout_position') or 'n/a')} ({_html_escape(prod.get('font_size_px') or '-')}px)</span>
                  </div>
                </div>

                <div class="editorial-col">
                  <div class="section-block">
                    <div class="block-title-row">
                      <h3>Instagram Caption Article ({_html_escape(ed.get('word_count') or 0)} words)</h3>
                      <button class="copy-btn" onclick="copyCaption('{eid}')">Copy Caption</button>
                    </div>
                    <pre class="caption-box" id="caption-{eid}">{caption}</pre>
                  </div>

                  {hold_html}

                  <div class="section-block">
                    <h3>Evidence Ledger (Observations &amp; Asset Citations)</h3>
                    <ul class="evidence-list">{obs_html}</ul>
                  </div>

                  <div class="section-block">
                    <h3>Recorded Uncertainties</h3>
                    <ul class="uncertainty-list">{unc_html}</ul>
                  </div>

                  <div class="section-block check-row">
                    <span>Privacy Check: <strong>{_html_escape(elig.get('privacy_check'))}</strong></span>
                    <span>Factual Support: <strong>{_html_escape(elig.get('factual_support_check'))}</strong></span>
                    <span>Publishable: <strong>{'YES' if elig.get('publishable') else 'NO (HELD)'}</strong></span>
                  </div>
                </div>
              </div>
            </article>
            """
        )

    wire_drawer_html = ""
    if wire_cards_html:
        wire_drawer_html = f"""
        <details class="wire-drawer">
          <summary>Unreviewed Wire Queue ({len(wire_cards_html)} captures in selection)</summary>
          <div class="wire-grid">
            {''.join(wire_cards_html)}
          </div>
        </details>
        """

    body_cards = ("\n".join(active_cards_html) + "\n" + wire_drawer_html) if (active_cards_html or wire_cards_html) else (
        "<div class='empty-state'>No sightings discovered yet. Run <code>discover_sightings</code> to check the wire.</div>"
    )

    counts = listed["counts_by_state"]
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>The Wildlife Desk — Local Newsroom Pressroom</title>
  <style>
    :root {{
      --bg: #0e1114;
      --panel: #161b20;
      --border: #2a323c;
      --text: #eef2f6;
      --muted: #9aa6b2;
      --accent: #e2e8f0;
      --ready: #1f7a4c;
      --held: #9a3412;
      --drafted: #1d4ed8;
      --published: #047857;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Georgia, serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    .masthead-bar {{
      border-bottom: 2px solid var(--text);
      padding: 24px 32px;
      background: #090b0d;
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      flex-wrap: wrap;
      gap: 16px;
    }}
    .masthead-title {{
      font-family: Impact, "Barlow Condensed", "Arial Narrow", sans-serif;
      font-size: 2.2rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin: 0;
    }}
    .masthead-sub {{
      color: var(--muted);
      font-size: 0.95rem;
      margin: 4px 0 0 0;
      font-style: italic;
    }}
    .status-pills {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.82rem;
    }}
    .pill {{
      padding: 6px 12px;
      border-radius: 4px;
      background: var(--panel);
      border: 1px solid var(--border);
    }}
    main {{
      max-width: 1360px;
      margin: 0 auto;
      padding: 28px 24px 64px;
    }}
    .dispatch-card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 32px;
      overflow: hidden;
    }}
    .card-header {{
      padding: 20px 24px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      background: #12161a;
    }}
    .kicker {{
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--muted);
      font-weight: 700;
    }}
    .card-header h2 {{
      margin: 4px 0 0 0;
      font-size: 1.55rem;
      letter-spacing: 0.02em;
    }}
    .badges {{
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .badge {{
      padding: 4px 10px;
      border-radius: 4px;
      font-size: 0.75rem;
      font-weight: 700;
      letter-spacing: 0.06em;
    }}
    .badge-ready {{ background: var(--ready); color: #fff; }}
    .badge-held {{ background: var(--held); color: #fff; }}
    .badge-drafted, .badge-observed, .badge-discovered {{ background: var(--drafted); color: #fff; }}
    .badge-published {{ background: var(--published); color: #fff; }}
    .badge-synth {{ background: #6b21a8; color: #fff; }}
    .badge-char {{ background: #334155; color: #e2e8f0; }}
    .event-id {{
      font-family: monospace;
      color: var(--muted);
      font-size: 0.82rem;
    }}
    .card-grid {{
      display: grid;
      grid-template-columns: minmax(340px, 500px) 1fr;
      gap: 28px;
      padding: 24px;
    }}
    @media (max-width: 960px) {{
      .card-grid {{ grid-template-columns: 1fr; }}
    }}
    .view-tabs {{
      display: flex;
      gap: 6px;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }}
    .tab-btn, .copy-btn {{
      background: #222931;
      color: var(--text);
      border: 1px solid var(--border);
      padding: 6px 11px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 0.8rem;
    }}
    .tab-btn.active {{
      background: var(--text);
      color: #090b0d;
      font-weight: 700;
    }}
    .frame-stage {{
      background: #07090b;
      border: 1px solid var(--border);
      border-radius: 6px;
      min-height: 420px;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 12px;
    }}
    .stage-img {{
      display: none;
      max-width: 100%;
      max-height: 620px;
      border-radius: 4px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    }}
    .stage-img.visible {{ display: block; }}
    .stage-img.thumb-size {{ width: 220px; height: auto; }}
    .meta-strip {{
      margin-top: 10px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      font-size: 0.78rem;
      color: var(--muted);
      font-family: monospace;
    }}
    .section-block {{
      margin-bottom: 18px;
    }}
    .block-title-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 6px;
    }}
    .section-block h3 {{
      font-size: 0.88rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin: 0 0 8px 0;
    }}
    .caption-box {{
      background: #0b0e11;
      border: 1px solid var(--border);
      padding: 16px;
      border-radius: 6px;
      white-space: pre-wrap;
      font-family: Georgia, "Times New Roman", serif;
      font-size: 1.04rem;
      line-height: 1.65;
      margin: 0;
    }}
    .hold-box {{
      background: rgba(154, 52, 18, 0.22);
      border: 1px solid #c2410c;
      padding: 12px 16px;
      border-radius: 6px;
      margin-bottom: 18px;
      font-size: 0.9rem;
    }}
    .pass-box {{
      background: rgba(31, 122, 76, 0.2);
      border: 1px solid #15803d;
      padding: 12px 16px;
      border-radius: 6px;
      margin-bottom: 18px;
      font-size: 0.9rem;
    }}
    .evidence-list, .uncertainty-list {{
      margin: 0;
      padding-left: 20px;
      font-size: 0.92rem;
    }}
    .asset-ref {{
      color: var(--muted);
      font-family: monospace;
      font-size: 0.82rem;
    }}
    .check-row {{
      display: flex;
      gap: 20px;
      flex-wrap: wrap;
      padding-top: 12px;
      border-top: 1px solid var(--border);
      font-size: 0.85rem;
      font-family: monospace;
    }}
    .wire-drawer {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px 24px;
      margin-top: 36px;
    }}
    .wire-drawer summary {{
      cursor: pointer;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .wire-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 16px;
      margin-top: 18px;
    }}
    .wire-item {{
      background: #0b0e11;
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      font-size: 0.78rem;
      font-family: monospace;
    }}
    .wire-item img {{
      width: 100%;
      height: 150px;
      object-fit: cover;
      display: block;
    }}
    .wire-meta {{
      padding: 8px 10px;
      display: flex;
      justify-content: space-between;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <header class="masthead-bar">
    <div>
      <h1 class="masthead-title">THE WILDLIFE DESK — PRESSROOM</h1>
      <p class="masthead-sub">A local newsroom covering an extremely small jurisdiction. Accurate observations. Absurdly serious coverage.</p>
    </div>
    <div class="status-pills">
      <span class="pill">Ready: {counts.get('ready', 0)}</span>
      <span class="pill">Drafted/Observed: {counts.get('drafted', 0) + counts.get('observed', 0) + counts.get('discovered', 0)}</span>
      <span class="pill">Held: {counts.get('held', 0)}</span>
      <span class="pill">Published: {counts.get('published', 0)}</span>
      <span class="pill">Publish Switch: {'ENABLED' if config.get('publish_enabled') else 'DISABLED (SAFE)'}</span>
    </div>
  </header>
  <main>
    {body_cards}
  </main>
  <script>
    function switchView(eid, mode) {{
      const modes = ['cover', 'square', 'thumb', 'raw'];
      modes.forEach(m => {{
        const el = document.getElementById('img-' + eid + '-' + m);
        if (el) {{
          if (m === mode) el.classList.add('visible');
          else el.classList.remove('visible');
        }}
      }});
      const card = document.getElementById('card-' + eid);
      if (card) {{
        const btns = card.querySelectorAll('.tab-btn');
        btns.forEach(b => {{
          if (b.getAttribute('onclick').includes("'" + mode + "'")) b.classList.add('active');
          else b.classList.remove('active');
        }});
      }}
    }}
    function copyCaption(eid) {{
      const pre = document.getElementById('caption-' + eid);
      if (pre && navigator.clipboard) {{
        navigator.clipboard.writeText(pre.innerText);
      }}
    }}
  </script>
</body>
</html>
"""
    pressroom_file = dirs["pressroom"] / "index.html"
    pressroom_file.write_text(html, encoding="utf-8")

    server_info = None
    if start_server:
        server_info = ensure_pressroom_server(
            dirs["pressroom"], port=config["local_server_port"]
        )

    return {
        "pressroom_html_path": str(pressroom_file),
        "dispatch_count": len(dispatches),
        "counts_by_state": counts,
        "server": server_info,
    }


def _get_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _get_tailscale_urls(port: int) -> Dict[str, Optional[str]]:
    ts_ip: Optional[str] = None
    ts_dns: Optional[str] = None
    try:
        out = subprocess.check_output(
            ["tailscale", "status", "--json"],
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            text=True,
        )
        data = json.loads(out)
        self_node = data.get("Self") or {}
        ips = self_node.get("TailscaleIPs") or []
        for ip in ips:
            if "." in str(ip):
                ts_ip = str(ip).strip()
                break
        dns_name = str(self_node.get("DNSName") or "").strip().rstrip(".")
        if dns_name:
            ts_dns = dns_name
    except Exception:
        pass
    return {
        "tailscale_ip_url": f"http://{ts_ip}:{port}/" if ts_ip else None,
        "tailscale_magicdns_url": f"http://{ts_dns}:{port}/" if ts_dns else None,
    }


def ensure_pressroom_server(serve_dir: Path, port: int = 8766) -> Dict[str, Any]:
    """Ensure a lightweight local HTTP server is serving `output/pressroom`."""
    lan_ip = _get_lan_ip()
    local_url = f"http://localhost:{port}/"
    lan_url = f"http://{lan_ip}:{port}/"
    ts_urls = _get_tailscale_urls(port)

    # Check if port is already listening
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            return {
                "running": True,
                "port": port,
                "local_url": local_url,
                "lan_url": lan_url,
                **ts_urls,
            }

    subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "0.0.0.0"],
        cwd=str(serve_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(0.25)
    return {
        "running": True,
        "port": port,
        "local_url": local_url,
        "lan_url": lan_url,
        **ts_urls,
    }


# ---------------------------------------------------------------------------
# Synthetic Sample Selection Generator (for local offline testing & onboarding)
# ---------------------------------------------------------------------------


def create_synthetic_test_selection(
    target_dir: Optional[Path] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Create a local gitignored folder (`captures/synthetic-test-selection`) with 3
    clearly labeled synthetic trail-camera test frames:
      1. Infrared night raccoon capture (valid sighting -> ready draft)
      2. Daytime person walking past camera (privacy hold boundary test)
      3. Empty wind-triggered leaf frame (no-story hold boundary test)
    """
    base = repo_dir or REPO_DIR
    sel_dir = target_dir or (base / "captures" / "synthetic-test-selection")
    sel_dir.mkdir(parents=True, exist_ok=True)

    # Frame 1: Infrared raccoon test image (or copy of hero base without text if synthetic)
    f1 = sel_dir / "SYNTH_CAM01_0001.jpg"
    img1 = Image.new("RGB", (1440, 1080), (26, 30, 32))
    d1 = ImageDraw.Draw(img1)
    # Draw stylized night forest + raccoon silhouette on right side (focal point ~ 0.68, 0.55)
    for y in range(1080):
        shade = int(20 + 25 * (y / 1080.0))
        d1.line([(0, y), (1440, y)], fill=(shade, shade + 3, shade + 5))
    # Trees
    for tx in (120, 340, 620, 1290):
        d1.rectangle([tx, 0, tx + 48, 820], fill=(16, 18, 20))
    # Raccoon body + glowing IR eyes on right
    d1.ellipse([820, 520, 1180, 760], fill=(95, 100, 105))
    d1.ellipse([760, 460, 920, 600], fill=(115, 120, 126))
    d1.ellipse([795, 515, 815, 535], fill=(245, 250, 255))
    d1.ellipse([855, 515, 875, 535], fill=(245, 250, 255))
    img1.save(f1, format="JPEG", quality=92)
    (f1.with_suffix(".jpg.meta.json")).write_text(
        json.dumps(
            {
                "capture_time": "2026-11-01T02:14:09",
                "capture_timezone_known": False,
                "capture_time_provenance": "exif_naive_clock",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Frame 2: Person / privacy hold test frame
    f2 = sel_dir / "SYNTH_CAM01_0002.jpg"
    img2 = Image.new("RGB", (1440, 1080), (65, 82, 68))
    d2 = ImageDraw.Draw(img2)
    d2.rectangle([660, 280, 780, 820], fill=(140, 150, 160))
    d2.ellipse([685, 190, 755, 270], fill=(170, 175, 180))
    img2.save(f2, format="JPEG", quality=90)
    (f2.with_suffix(".jpg.meta.json")).write_text(
        json.dumps(
            {
                "capture_time": "2026-11-01T09:30:00",
                "capture_timezone_known": False,
                "capture_time_provenance": "exif_naive_clock",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Frame 3: Empty wind-triggered leaf frame (no story test)
    f3 = sel_dir / "SYNTH_CAM01_0003.jpg"
    img3 = Image.new("RGB", (1440, 1080), (42, 54, 44))
    img3.save(f3, format="JPEG", quality=90)
    (f3.with_suffix(".jpg.meta.json")).write_text(
        json.dumps(
            {
                "capture_time": "2026-11-01T14:05:22",
                "capture_timezone_known": False,
                "capture_time_provenance": "exif_naive_clock",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "selection_dir": str(sel_dir),
        "files_created": [str(f1), str(f2), str(f3)],
    }


def get_newsroom_status(
    env_override: Optional[Dict[str, str]] = None,
    repo_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Return complete status of the Wildlife Desk newsroom, configuration boundary, and wire queue."""
    config = load_config(env_override=env_override, repo_dir=repo_dir)
    try:
        boundary = validate_selection_boundary(config)
    except SelectionBoundaryError as exc:
        boundary = {
            "valid": False,
            "mode": config.get("wildlife_source_mode"),
            "error": str(exc),
        }

    listed = list_dispatches(env_override=env_override, repo_dir=repo_dir)
    continuity = get_continuity_context(env_override=env_override, repo_dir=repo_dir)

    return {
        "newsroom": "The Wildlife Desk",
        "motto": "Accurate observations. Absurdly serious coverage.",
        "source_mode": config.get("wildlife_source_mode"),
        "selection_boundary": boundary,
        "publish_enabled": config.get("publish_enabled", False),
        "counts_by_state": listed["counts_by_state"],
        "total_dispatches": listed["total_count"],
        "species_archive": continuity["species_archive"],
        "available_sections": list(VALID_SECTIONS),
        "reporter_workflow": [
            "1. Call `discover_sightings()` to scan the restricted wildlife selection and cache EXIF-stripped frames.",
            "2. Visually inspect each sighting's `cached_previews` using your file/image viewer tool.",
            "3. Call `record_observations(...)` with asset-backed claims, uncertainties, and privacy screening.",
            "4. For empty frames or frames with people/property, call `hold_dispatch(...)`.",
            "5. For newsworthy wildlife sightings, check `get_continuity_context(species)` and call `draft_dispatch(...)` to write the deadpan headline + 60-120 word article and render the 1080x1350 cover.",
            "6. Call `build_pressroom(start_server=True)` so the Editor-in-Chief can review the front page, 1:1 grid crop, and evidence ledger.",
        ],
    }


# ---------------------------------------------------------------------------
# CLI Interface
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wildlife-desk",
        description="The Wildlife Desk — Local Newsroom Studio & Pipeline CLI",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show newsroom configuration, boundary check, and queue status")
    sub.add_parser("discover", help="Run incremental sighting discovery from configured selection")
    sub.add_parser("list", help="List all sighting dispatches in local state")

    p_press = sub.add_parser("pressroom", help="Build and optionally serve the Local Web Pressroom")
    p_press.add_argument("--serve", action="store_true", help="Start local HTTP server on LOCAL_SERVER_PORT")

    p_synth = sub.add_parser(
        "init-synthetic-demo",
        help="Create a 3-sighting synthetic test selection (1 ready story, 1 privacy hold, 1 empty-frame hold)",
    )
    p_synth.add_argument("--serve", action="store_true", help="Serve Pressroom after building demo")

    args = parser.parse_args(argv)

    if args.command == "status" or not args.command:
        print(json.dumps(get_newsroom_status(), indent=2))
        return 0
    if args.command == "discover":
        print(json.dumps(discover_sightings(), indent=2))
        return 0
    if args.command == "list":
        print(json.dumps(list_dispatches(), indent=2))
        return 0
    if args.command == "pressroom":
        print(json.dumps(build_pressroom(start_server=args.serve), indent=2))
        return 0
    if args.command == "init-synthetic-demo":
        synth = create_synthetic_test_selection()
        env_ov = {
            "WILDLIFE_SOURCE_MODE": "local",
            "LOCAL_WILDLIFE_DIR": synth["selection_dir"],
        }
        disc = discover_sightings(env_override=env_ov)
        eids = [s["event_id"] for s in disc["sightings"]]
        if len(eids) >= 3:
            # Sighting 1: Raccoon inspection (synthetic example flagged)
            s1 = load_dispatch_record(eids[0], env_override=env_ov)
            aid1 = s1["source"]["asset_ids"][0]
            record_observations(
                event_id=eids[0],
                observations=[
                    {
                        "claim": "One raccoon is visible on the right side of the frame facing toward the lens in infrared night mode.",
                        "source_asset_ids": [aid1],
                        "species": "raccoon",
                        "visible_behavior": "looking toward camera",
                        "certainty": "confirmed",
                        "focal_point": [0.68, 0.55],
                    }
                ],
                contains_people=False,
                contains_identifying_property=False,
                is_empty_frame=False,
                synthetic_example=True,
                env_override=env_ov,
            )
            draft_dispatch(
                event_id=eids[0],
                headline="RACCOON PAUSES FOR UNSCHEDULED OVERNIGHT CAMERA INSPECTION",
                caption=(
                    "A single raccoon was recorded in infrared night mode facing toward the camera from the right side of the frame. "
                    "Because a single still image was captured and the camera clock timezone is unverified, neither the exact hour nor the animal's direction of travel could be established.\n\n"
                    "The inspection concluded without further comment."
                ),
                section="The Night Shift",
                character_label="The Night Shift Regular (Identity Unconfirmed)",
                layout_position="bottom-left",
                env_override=env_ov,
            )

            # Sighting 2: Privacy hold (person in frame)
            s2 = load_dispatch_record(eids[1], env_override=env_ov)
            aid2 = s2["source"]["asset_ids"][0]
            record_observations(
                event_id=eids[1],
                observations=[
                    {
                        "claim": "A person is visible walking across the center of the camera frame during daylight.",
                        "source_asset_ids": [aid2],
                        "species": "human",
                        "certainty": "confirmed",
                    }
                ],
                contains_people=True,
                contains_identifying_property=False,
                is_empty_frame=False,
                privacy_notes="Human figure detected in frame; mandatory privacy hold.",
                synthetic_example=True,
                env_override=env_ov,
            )
            hold_dispatch(
                event_id=eids[1],
                reason="Mandatory Privacy Hold: Human figure visible in frame.",
                env_override=env_ov,
            )

            # Sighting 3: Empty frame (no story)
            s3 = load_dispatch_record(eids[2], env_override=env_ov)
            aid3 = s3["source"]["asset_ids"][0]
            record_observations(
                event_id=eids[2],
                observations=[
                    {
                        "claim": "No animal is visible in the frame; foliage movement likely triggered the sensor.",
                        "source_asset_ids": [aid3],
                        "species": None,
                        "certainty": "confirmed",
                    }
                ],
                contains_people=False,
                contains_identifying_property=False,
                is_empty_frame=True,
                synthetic_example=True,
                env_override=env_ov,
            )
            hold_dispatch(
                event_id=eids[2],
                reason="No Story: Empty frame with no visible wildlife.",
                is_empty_frame=True,
                env_override=env_ov,
            )

        press = build_pressroom(start_server=args.serve, env_override=env_ov)
        print(json.dumps({"demo_initialized": True, "pressroom": press}, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
