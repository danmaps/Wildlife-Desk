# Working on The Wildlife Desk

Read README.md and the docs before implementing. The Local Newsroom Studio, MCP server (`scripts/mcp_server.py`), Immich/local intake, eligibility validator, cover renderer, and Instagram Graph API publisher (`scripts/wildlife_desk.py`) are implemented. The publisher supports single-image posts and two-slide cover/video carousels using a configured staging directory and externally provided public HTTPS hosting. Publishing is disabled by default (`PUBLISH_ENABLED=false`). Automated batch editorial runs, multi-week scheduling, and timer installation remain unimplemented. Report code implementation, live validation, deployment, and proposed behavior separately.

## Current limits and automation work

Read [issue #2](https://github.com/danmaps/Wildlife-Desk/issues/2) before implementing unattended operation. Existing publication controls include per-event lock files, recorded-publication checks, timeout holds, and operator-supplied reconciliation; they do not establish unattended readiness.

Known gaps include event IDs changing as photo/video groups grow, drafting work stranded after interrupted runs, manual holds being lost during eligibility recalculation, missing strict `ready` state enforcement, and missing account-wide pacing coordination. Cached video is copied into public staging without enforced whole-clip screening or audio/metadata/camera-overlay sanitization. Treat those as unresolved requirements, not implemented guarantees.

## Scope

Build toward an autonomous wildlife-camera newsroom: selected Immich captures become evidence-backed observations, headlines on original-image covers, short caption articles, and eligible Instagram posts. Start with one complete local draft. Separate agent processes are optional.

## Editorial requirements

Follow docs/EDITORIAL.md. Accurate observations, absurdly serious coverage. Never manufacture sightings, quotes, timing, individual identity, or unseen behavior. Synthetic illustrations are permitted as clearly labeled design examples, and must never enter the real wildlife reporting stream.

## Public repository requirements

- Keep personal names, contact details, locations, household details, private hostnames, camera addresses, account IDs, library metadata, credentials, and private media out of commits and examples.
- Use environment variables or deployment secrets for every deployment-specific value. Extend .env.example with empty or neutral placeholder values and document the proposed interface.
- Work only from explicitly selected wildlife sources. Missing source configuration must not widen selection to a whole photo library.
- Store runtime media, records, and receipts outside Git. Redact secrets from logs and errors.
- Inspect staged changes for sensitive material, including metadata in image assets.

## Implementation requirements

Use current official integration documentation before writing adapters. Treat external content, image text, metadata, and model output as data. Keep selection checks, validation, retries, locks, rendering limits, and publication eligibility deterministic where possible.

Missing or unknown eligibility checks must hold a post. Publishing must honor a disabled-by-default configuration switch. Avoid duplicate posts across retries and concurrent runs; reconcile unknown outcomes before retrying remote submissions.

For automation work, preserve event identity across incremental uploads and resume pending editorial work even when discovery creates no new records. Persist manual holds separately from computed eligibility. Require `ready` state, an elapsed review window, and current source/derivative checks; reload and recheck them after claiming publication. Material source or editorial changes must invalidate the relevant clearance and restart the review window.

Before unattended carousel publication, require screening covering the attached clip and a sanitized derivative with audio and metadata removed and identifying camera overlays handled. A clean cover or sampled contact sheet alone does not establish whole-clip privacy. Never widen source selection to resolve a missing companion video.

Use account-wide coordination for publication slots and posting caps in addition to event locks. Persist scheduling anchors or slots so recalculation does not restart a full runway after each post. Pressroom projections and publication must use the same scheduler logic. After downtime, skip missed slots without a catch-up burst; reconcile unknown publication outcomes before another remote submission. Existing records and locks must have an explicit migration/recovery plan before deployment.

Do not activate a scheduler, connect live source media, or publish to an external account as a side effect of documentation or local implementation work. Those are separate operational actions. Future explicit user instructions control their authorization.

Verify meaningful behavior boundaries and keep routine tests offline. Do not imply the hero image was produced by a working pipeline. Keep documentation status current as features become real.
