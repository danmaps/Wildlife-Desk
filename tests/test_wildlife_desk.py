from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import wildlife_desk
from wildlife_desk import (
    PublicationGateError,
    SelectionBoundaryError,
    ValidationHoldError,
    WildlifeDeskError,
)


def _make_test_env(tmp_path: Path, **overrides) -> dict:
    sel_dir = tmp_path / "wildlife_selection"
    sel_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "WILDLIFE_SOURCE_MODE": "local",
        "LOCAL_WILDLIFE_DIR": str(sel_dir),
        "STATE_DIR": str(tmp_path / "state"),
        "OUTPUT_DIR": str(tmp_path / "output"),
        "PUBLISH_ENABLED": "false",
        "BURST_WINDOW_SECONDS": "180",
    }
    env.update(overrides)
    return env


def _create_image_with_meta(
    folder: Path,
    name: str,
    color: tuple = (40, 50, 45),
    capture_time: str | None = "2026-11-01T03:15:00",
    tz_known: bool = False,
    has_gps: bool = False,
) -> Path:
    img_path = folder / name
    img = Image.new("RGB", (1200, 900), color)
    img.save(img_path, format="JPEG", quality=90)
    meta = {
        "capture_time": capture_time,
        "capture_timezone_known": tz_known,
        "capture_time_provenance": (
            "import_only_no_exif"
            if capture_time is None
            else ("exif_with_tz" if tz_known else "exif_naive_clock")
        ),
        "has_gps_metadata": has_gps,
    }
    (img_path.with_suffix(img_path.suffix + ".meta.json")).write_text(
        json.dumps(meta), encoding="utf-8"
    )
    return img_path


def test_selection_boundary_missing_or_broad_halts_intake(tmp_path: Path):
    # 1. Missing Immich album ID must halt
    env_no_album = {
        "WILDLIFE_SOURCE_MODE": "immich",
        "IMMICH_BASE_URL": "https://photos.example.org",
        "IMMICH_API_KEY": "secret-immich-key-12345",
        "IMMICH_WILDLIFE_ALBUM_ID": "",
        "STATE_DIR": str(tmp_path / "state"),
        "OUTPUT_DIR": str(tmp_path / "output"),
    }
    with pytest.raises(SelectionBoundaryError, match="IMMICH_WILDLIFE_ALBUM_ID"):
        wildlife_desk.discover_sightings(env_override=env_no_album)

    # 2. Placeholder .invalid URL must halt
    env_invalid_url = dict(env_no_album, IMMICH_WILDLIFE_ALBUM_ID="album-wildlife-001", IMMICH_BASE_URL="https://immich.example.invalid")
    with pytest.raises(SelectionBoundaryError, match="IMMICH_BASE_URL"):
        wildlife_desk.discover_sightings(env_override=env_invalid_url)

    # 3. Missing or root LOCAL_WILDLIFE_DIR must halt
    env_no_local = _make_test_env(tmp_path, LOCAL_WILDLIFE_DIR="")
    with pytest.raises(SelectionBoundaryError, match="LOCAL_WILDLIFE_DIR"):
        wildlife_desk.discover_sightings(env_override=env_no_local)

    env_broad_root = _make_test_env(tmp_path, LOCAL_WILDLIFE_DIR="/tmp")
    with pytest.raises(SelectionBoundaryError, match="top-level system or workspace root"):
        wildlife_desk.discover_sightings(env_override=env_broad_root)


def test_immich_album_id_mismatch_rejected(tmp_path: Path):
    env = {
        "WILDLIFE_SOURCE_MODE": "immich",
        "IMMICH_BASE_URL": "http://127.0.0.1:2283",
        "IMMICH_API_KEY": "super-secret-key-9999",
        "IMMICH_WILDLIFE_ALBUM_ID": "expected-album-1234",
        "STATE_DIR": str(tmp_path / "state"),
        "OUTPUT_DIR": str(tmp_path / "output"),
    }
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = json.dumps({"id": "wrong-album-9999", "assets": []}).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp

    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(SelectionBoundaryError, match="did not match configured"):
            wildlife_desk.discover_sightings(env_override=env)


def test_idempotent_discovery_burst_grouping_and_duplicates(tmp_path: Path):
    env = _make_test_env(tmp_path, BURST_WINDOW_SECONDS="120")
    sel_dir = Path(env["LOCAL_WILDLIFE_DIR"])

    # Two frames 45s apart -> grouped into 1 burst sighting
    _create_image_with_meta(sel_dir, "CAM_001.jpg", color=(30, 40, 50), capture_time="2026-11-01T02:00:00")
    _create_image_with_meta(sel_dir, "CAM_002.jpg", color=(35, 45, 55), capture_time="2026-11-01T02:00:45")
    # Duplicate image content of CAM_002 -> deduplicated
    _create_image_with_meta(sel_dir, "CAM_002_DUP.jpg", color=(35, 45, 55), capture_time="2026-11-01T02:00:45")
    # Frame 20 minutes later -> separate sighting
    _create_image_with_meta(sel_dir, "CAM_003.jpg", color=(60, 70, 80), capture_time="2026-11-01T02:20:00")
    # Frame with no EXIF timestamp -> never merged into burst
    _create_image_with_meta(sel_dir, "CAM_004_NO_EXIF.jpg", color=(90, 80, 70), capture_time=None)

    run1 = wildlife_desk.discover_sightings(env_override=env)
    assert run1["total_sightings"] == 3
    assert run1["new_sightings_created"] == 3

    # Re-running discovery over the same selection must be idempotent
    run2 = wildlife_desk.discover_sightings(env_override=env)
    assert run2["total_sightings"] == 3
    assert run2["new_sightings_created"] == 0
    assert run2["existing_sightings_preserved"] == 3
    assert run1["created_event_ids"] == run2["existing_event_ids"]


def test_clock_uncertainty_and_return_premise_validation(tmp_path: Path):
    env = _make_test_env(tmp_path)
    sel_dir = Path(env["LOCAL_WILDLIFE_DIR"])
    _create_image_with_meta(
        sel_dir,
        "FOX_001.jpg",
        color=(50, 55, 60),
        capture_time="2026-11-01T02:15:00",
        tz_known=False,
        has_gps=True,
    )

    disc = wildlife_desk.discover_sightings(env_override=env)
    eid = disc["created_event_ids"][0]
    rec = wildlife_desk.load_dispatch_record(eid, env_override=env)
    aid = rec["source"]["asset_ids"][0]
    assert rec["source"]["gps_metadata_stripped"] is True
    assert rec["source"]["public_location"] is None
    assert rec["source"]["capture_timezone_known"] is False

    # Reject observations referencing invalid asset IDs
    with pytest.raises(WildlifeDeskError, match="invalid source_asset_ids"):
        wildlife_desk.record_observations(
            event_id=eid,
            observations=[{"claim": "A fox crosses left to right.", "source_asset_ids": ["nonexistent-id"]}],
            env_override=env,
        )

    # Record valid observation
    wildlife_desk.record_observations(
        event_id=eid,
        observations=[
            {
                "claim": "One fox is visible near the center of the frame in infrared night mode.",
                "source_asset_ids": [aid],
                "species": "fox",
                "certainty": "confirmed",
            }
        ],
        contains_people=False,
        contains_identifying_property=False,
        is_empty_frame=False,
        env_override=env,
    )

    # 1. Claiming "FOX RETURNS" with zero prior fox sightings must fail factual_support_check and hold!
    draft_return = wildlife_desk.draft_dispatch(
        event_id=eid,
        headline="FOX RETURNS TO PERIMETER FOR OVERNIGHT INSPECTION",
        caption="A fox was recorded crossing the frame overnight. Its business remains unclear.",
        section="The Night Shift",
        env_override=env,
    )
    assert draft_return["state"] == "held"
    assert draft_return["eligibility"]["factual_support_check"] == "failed"
    assert any("without an earlier recorded sighting" in r for r in draft_return["eligibility"]["hold_reasons"])

    # 2. Claiming exact clock hour ("at 2 a.m.") when capture_timezone_known is False must hold!
    draft_clock = wildlife_desk.draft_dispatch(
        event_id=eid,
        headline="FOX CROSSES PERIMETER IN OVERNIGHT CAMERA FRAME",
        caption="A fox was recorded at 2 a.m. looking toward the camera. What happened next is unknown.",
        section="The Night Shift",
        env_override=env,
    )
    assert draft_clock["state"] == "held"
    assert any("timezone is unverified" in r for r in draft_clock["eligibility"]["hold_reasons"])

    # 3. Grounded headline & caption without unsupported return or exact hour transitions to "ready"!
    draft_ok = wildlife_desk.draft_dispatch(
        event_id=eid,
        headline="FOX CROSSES PERIMETER IN OVERNIGHT CAMERA FRAME",
        caption=(
            "A single fox was recorded in infrared night mode near the center of the camera frame. "
            "Because only one still image was captured and the camera timezone is unverified, "
            "neither duration nor exact wall-clock hour could be established.\n\n"
            "The fox declined to elaborate."
        ),
        section="The Night Shift",
        env_override=env,
    )
    assert draft_ok["state"] == "ready"
    assert draft_ok["eligibility"]["publishable"] is True
    cover_path = Path(draft_ok["production"]["cover_path"])
    assert cover_path.exists()
    with Image.open(cover_path) as rendered:
        assert rendered.size == (1080, 1350)
        assert not rendered.getexif()


def test_privacy_hold_and_empty_frame_no_story(tmp_path: Path):
    env = _make_test_env(tmp_path)
    sel_dir = Path(env["LOCAL_WILDLIFE_DIR"])
    _create_image_with_meta(sel_dir, "HUMAN_001.jpg", color=(80, 90, 100), capture_time="2026-11-01T08:00:00")
    _create_image_with_meta(sel_dir, "EMPTY_001.jpg", color=(20, 30, 20), capture_time="2026-11-01T11:00:00")

    disc = wildlife_desk.discover_sightings(env_override=env)
    eid_human, eid_empty = disc["created_event_ids"]

    rec_h = wildlife_desk.load_dispatch_record(eid_human, env_override=env)
    updated_h = wildlife_desk.record_observations(
        event_id=eid_human,
        observations=[
            {
                "claim": "A person is walking across the camera view.",
                "source_asset_ids": rec_h["source"]["asset_ids"],
                "species": "human",
                "certainty": "confirmed",
            }
        ],
        contains_people=True,
        contains_identifying_property=False,
        env_override=env,
    )
    assert updated_h["state"] == "held"
    assert updated_h["eligibility"]["privacy_check"] == "failed"
    assert updated_h["eligibility"]["publishable"] is False

    rec_e = wildlife_desk.load_dispatch_record(eid_empty, env_override=env)
    updated_e = wildlife_desk.record_observations(
        event_id=eid_empty,
        observations=[
            {
                "claim": "Empty frame; no wildlife visible.",
                "source_asset_ids": rec_e["source"]["asset_ids"],
                "species": None,
                "certainty": "confirmed",
            }
        ],
        contains_people=False,
        contains_identifying_property=False,
        is_empty_frame=True,
        env_override=env,
    )
    assert updated_e["state"] == "held"
    assert updated_e["eligibility"]["publishable"] is False


def test_headline_overflow_rejected_deterministically(tmp_path: Path):
    env = _make_test_env(tmp_path)
    sel_dir = Path(env["LOCAL_WILDLIFE_DIR"])
    _create_image_with_meta(sel_dir, "DEER_001.jpg", color=(45, 65, 55), capture_time="2026-11-01T05:00:00")

    disc = wildlife_desk.discover_sightings(env_override=env)
    eid = disc["created_event_ids"][0]
    rec = wildlife_desk.load_dispatch_record(eid, env_override=env)
    wildlife_desk.record_observations(
        event_id=eid,
        observations=[
            {
                "claim": "One deer is visible standing near the tree line.",
                "source_asset_ids": rec["source"]["asset_ids"],
                "species": "deer",
                "certainty": "confirmed",
            }
        ],
        contains_people=False,
        contains_identifying_property=False,
        env_override=env,
    )

    # Single unbroken 60-character token cannot wrap within max_text_width -> overflow rejected
    giant_word = "UNCHARACTERISTICALLYDISPROPORTIONATEJURISDICTIONALDEVELOPMENT"
    with pytest.raises(ValidationHoldError, match="Headline overflow rejected"):
        wildlife_desk.draft_dispatch(
            event_id=eid,
            headline=f"DEER OBSERVED DURING {giant_word} OVERNIGHT",
            caption="A deer paused near the tree line.",
            section="Local Affairs",
            env_override=env,
        )

    after = wildlife_desk.load_dispatch_record(eid, env_override=env)
    assert after["production"]["overflow_rejected"] is True
    assert after["state"] == "held"
    assert after["eligibility"]["publishable"] is False


def test_publishing_kill_switch_locks_and_timeout_reconciliation(tmp_path: Path):
    env = _make_test_env(tmp_path, PUBLISH_ENABLED="false")
    sel_dir = Path(env["LOCAL_WILDLIFE_DIR"])
    _create_image_with_meta(sel_dir, "OWL_001.jpg", color=(30, 35, 45), capture_time="2026-11-01T01:00:00")

    disc = wildlife_desk.discover_sightings(env_override=env)
    eid = disc["created_event_ids"][0]
    rec = wildlife_desk.load_dispatch_record(eid, env_override=env)
    wildlife_desk.record_observations(
        event_id=eid,
        observations=[
            {
                "claim": "An owl is perched on a low branch in infrared night mode.",
                "source_asset_ids": rec["source"]["asset_ids"],
                "species": "owl",
                "certainty": "confirmed",
            }
        ],
        contains_people=False,
        contains_identifying_property=False,
        env_override=env,
    )
    wildlife_desk.draft_dispatch(
        event_id=eid,
        headline="OWL OBSERVED ON LOW BRANCH DURING NIGHT SHIFT",
        caption="An owl was recorded perched on a branch in infrared mode. It remained still for the frame.",
        section="The Night Shift",
        env_override=env,
    )

    # 1. Disabled publishing switch blocks outbound attempts
    with pytest.raises(PublicationGateError, match="PUBLISH_ENABLED=false"):
        wildlife_desk.publish_dispatch(event_id=eid, env_override=env)

    # 2. Enable publishing with local test receipt mode, test simulated timeout -> reconcile required
    env_pub = dict(
        env,
        PUBLISH_ENABLED="true",
        INSTAGRAM_ACCOUNT_ID="local-test-receipt",
        INSTAGRAM_ACCESS_TOKEN="secret-ig-token-xyz987",
    )
    with pytest.raises(PublicationGateError, match="reconcile_required=True"):
        wildlife_desk.publish_dispatch(
            event_id=eid, simulate_remote_timeout=True, env_override=env_pub
        )

    # Retrying before reconciliation must be blocked to prevent duplicate posts
    with pytest.raises(PublicationGateError, match="reconcile_publication"):
        wildlife_desk.publish_dispatch(event_id=eid, env_override=env_pub)

    # Reconcile as NOT created on remote -> safe to retry
    reconciled = wildlife_desk.reconcile_publication(
        event_id=eid, remote_was_created=False, env_override=env_pub
    )
    assert reconciled["state"] == "ready"
    assert reconciled["publication"]["reconcile_required"] is False

    # Concurrent lock file blocks overlapping worker
    lock_file = Path(env_pub["STATE_DIR"]) / "locks" / f"{eid}.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("{}", encoding="utf-8")
    with pytest.raises(PublicationGateError, match="Concurrent publication lock active"):
        wildlife_desk.publish_dispatch(event_id=eid, env_override=env_pub)
    lock_file.unlink()

    # Successful publication records receipt and blocks subsequent duplicate runs
    published = wildlife_desk.publish_dispatch(event_id=eid, env_override=env_pub)
    assert published["state"] == "published"
    assert published["publication"]["remote_post_id"].startswith("ig-local-")

    with pytest.raises(PublicationGateError, match="already published"):
        wildlife_desk.publish_dispatch(event_id=eid, env_override=env_pub)

    # Verify secret redaction helper
    cfg = wildlife_desk.load_config(env_override=env_pub)
    redacted = wildlife_desk.redact_secrets("Token is secret-ig-token-xyz987!", cfg)
    assert "secret-ig-token-xyz987" not in redacted
    assert "[REDACTED_SECRET]" in redacted


def test_attach_video_and_pressroom_video_playback(tmp_path: Path):
    env = _make_test_env(tmp_path)
    sel_dir = Path(env["LOCAL_WILDLIFE_DIR"])
    _create_image_with_meta(
        sel_dir, "OPOSSUM_001.jpg", color=(38, 42, 46), capture_time="2026-11-01T03:10:00", tz_known=False
    )
    vid_file = sel_dir / "OPOSSUM_002.mp4"
    vid_file.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")
    (vid_file.with_suffix(".mp4.meta.json")).write_text(
        json.dumps(
            {
                "capture_time": "2026-11-01T03:10:05",
                "capture_timezone_known": True,
                "capture_time_provenance": "exif_tz",
            }
        ),
        encoding="utf-8",
    )

    disc = wildlife_desk.discover_sightings(env_override=env)
    assert disc["total_sightings"] == 2
    img_eid = [
        s["event_id"]
        for s in disc["sightings"]
        if wildlife_desk.load_dispatch_record(s["event_id"], env_override=env)["source"]["assets"][0]["media_type"]
        == "image"
    ][0]

    # Auto-detect and attach the companion video event
    paired = wildlife_desk.attach_video_to_dispatch(img_eid, env_override=env)
    assert paired["production"]["post_format"] == "carousel_video"
    assert paired["production"]["video_path"].endswith(".mp4")
    assert len(paired["source"]["asset_ids"]) == 2

    wildlife_desk.record_observations(
        event_id=img_eid,
        observations=[
            {
                "claim": "One opossum is visible crossing the clearing.",
                "source_asset_ids": paired["source"]["asset_ids"],
                "species": "opossum",
                "certainty": "confirmed",
            }
        ],
        contains_people=False,
        contains_identifying_property=False,
        env_override=env,
    )
    drafted = wildlife_desk.draft_dispatch(
        event_id=img_eid,
        headline="OPOSSUM CONDUCTS LOW-SPEED INSPECTION OF PERIMETER",
        caption="An opossum crossed the clearing in infrared mode and continued through the ten-second clip.",
        section="The Night Shift",
        camera_stamp="11/01/2026 03:10:00 • 14°C / 57°F",
        env_override=env,
    )
    assert drafted["production"]["post_format"] == "carousel_video"
    assert drafted["production"]["video_path"].endswith(".mp4")
    assert drafted["editorial"]["camera_stamp"] == "11/01/2026 03:10:00 • 14°C / 57°F"
    assert "Camera Stamp: 11/01/2026 03:10:00 • 14°C / 57°F" in drafted["editorial"]["caption"]

    press = wildlife_desk.build_pressroom(start_server=False, env_override=env)
    html_text = Path(press["pressroom_html_path"]).read_text(encoding="utf-8")
    assert "SLIDESHOW: COVER + 10S VIDEO" in html_text
    assert f"media/{img_eid}-video.mp4" in html_text
    assert "<video" in html_text
    assert "11/01/2026 03:10:00 • 14°C / 57°F" in html_text


def test_schedule_interleaving_and_drip_publish(tmp_path: Path):
    """
    Verify that `compute_publication_schedule` interleaves species across Tue/Thu/Sat
    slots and `drip_publish` respects schedule days and cooldown windows.
    """
    import datetime as dt

    env = _make_test_env(tmp_path)
    sel_dir = Path(env["LOCAL_WILDLIFE_DIR"])
    items = [
        ("C1.jpg", (30, 40, 50), "2026-11-01T01:00:00", "coyote", "COYOTE PATROLS RIDGE AT ONE IN THE MORNING"),
        ("C2.jpg", (45, 55, 65), "2026-11-01T02:00:00", "coyote", "COYOTE CROSSES HILLSIDE IN QUIET DARKNESS"),
        ("R1.jpg", (60, 70, 80), "2026-11-01T03:00:00", "raccoon", "RACCOON INSPECTS PERIMETER MARKER AFTER DARK"),
    ]
    for fname, col, cap_time, _, _ in items:
        _create_image_with_meta(
            sel_dir, fname, color=col, capture_time=cap_time, tz_known=False
        )

    disc = wildlife_desk.discover_sightings(env_override=env)
    assert disc["total_sightings"] == 3

    for s_info, (_, _, _, sp, headline) in zip(disc["sightings"], items):
        eid = s_info["event_id"]
        rec = wildlife_desk.load_dispatch_record(eid, env_override=env)
        wildlife_desk.record_observations(
            event_id=eid,
            observations=[
                {
                    "claim": f"One {sp} is visible in infrared mode.",
                    "source_asset_ids": rec["source"]["asset_ids"],
                    "species": sp,
                    "certainty": "confirmed",
                }
            ],
            contains_people=False,
            contains_identifying_property=False,
            env_override=env,
        )
        wildlife_desk.draft_dispatch(
            event_id=eid,
            headline=headline,
            caption=f"A {sp} crossed the clearing during overnight camera monitoring.",
            section="The Night Shift",
            env_override=env,
        )

    # Monday Nov 2, 2026 @ 10:00 local
    mon_dt = dt.datetime(2026, 11, 2, 10, 0, 0, tzinfo=dt.timezone.utc)
    sched = wildlife_desk.compute_publication_schedule(
        schedule_days="tue,thu,sat",
        schedule_hour_local=8,
        now_dt=mon_dt,
        env_override=env,
    )
    assert sched["ready_count"] == 3
    # Species should interleave: coyote -> raccoon -> coyote
    species_order = [s["species"] for s in sched["slots"]]
    assert species_order == ["coyote", "raccoon", "coyote"]

    # Calling drip_publish on Monday should skip (not a Tue/Thu/Sat schedule day)
    skip_res = wildlife_desk.drip_publish(
        now_dt=mon_dt,
        dry_run=True,
        env_override=env,
    )
    assert skip_res["action"] == "skipped_not_schedule_day"

    # Calling drip_publish on Tuesday Nov 3 @ 08:05 should dry-run publish slot #1
    tue_dt = dt.datetime(2026, 11, 3, 8, 5, 0, tzinfo=dt.timezone.utc)
    pub_res = wildlife_desk.drip_publish(
        now_dt=tue_dt,
        dry_run=True,
        env_override=env,
    )
    assert pub_res["action"] == "dry_run_validated"
    assert pub_res["event_id"] == sched["slots"][0]["event_id"]


