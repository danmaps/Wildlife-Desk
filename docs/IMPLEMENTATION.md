# Implementation plan

Status: **Local Newsroom Studio, MCP Server, Immich/Local Intake, Deterministic Cover Renderer, Instagram Graph API Publisher (Single-Image + 2-Slide Cover/Video Carousel), and Automated Batch Intake + Multi-Week Drip Scheduler (`scripts/cron_runner.sh`, `pair-companions`, `schedule`, `drip-publish`) implemented** (`scripts/wildlife_desk.py`, `scripts/mcp_server.py`, `SKILL.md`). Publishing remains gated behind `PUBLISH_ENABLED=false` by default until explicitly enabled.

## Intended flow

Wi-Fi download remains the upstream way captures reach Immich. Begin at Immich instead of building a camera-specific downloader.

1. Read new assets from a configured, explicitly selected wildlife album or equivalent restricted selection.
2. Group related captures into sightings and eliminate duplicates.
3. Extract structured observations and uncertainty from the source media.
4. Select a story and write a headline plus short caption article.
5. Validate factual support and privacy eligibility.
6. Render a cover using original camera media and deterministic typography.
7. Publish an eligible dispatch and retain a publication receipt.

Use one small application and persistent local state first. Intake, observation, editing, production, and publishing are logical roles, not a requirement for five agent processes. Python is a reasonable starting point; the runtime, model provider, and deployment target remain open choices.

## Source boundary

Require a configured wildlife selection. If it is absent or unavailable, stop intake rather than scanning a whole library. Check the installed Immich version and available permission scoping before choosing an access method. Use the narrowest available credentials and add an application-level selection boundary even where credentials are broader.

Do not commit originals, thumbnails derived from private media, private source URLs, coordinates, or album exports. Transfer only deliberately selected eligible material to any external model. Screen for people and identifying details before external model use; ambiguous material stays local and is held.

## A dispatch record

The fictional [example record](../examples/dispatch.json) shows the intended separation between source evidence, observations, editorial text, and publication state. It is illustrative, not a validated API schema.

At implementation time, define and validate a versioned record with:

- Stable event ID and source asset IDs or hashes.
- Capture times, their provenance, timezone certainty, and ordering confidence.
- Observation claims tied to specific source assets, with uncertainty.
- Separate headline, caption, and optional recurring-character labels.
- Privacy and factual-support decisions, including hold reasons.
- Renderer inputs, output hash, and template version.
- Publication attempts, platform result IDs, timestamps, and errors.

Keep source credentials and secret-bearing URLs out of records and logs. Runtime records stay private even though the software repository is public.

## State and retries

Proposed states: `discovered`, `observed`, `drafted`, `ready`, `publishing`, `published`, `held`, and `failed`.

Only publish `ready` records after all required checks pass. Treat missing or unknown checks as a hold. Create an atomic claim before sending a post so overlapping runs cannot publish the same event. Record a stable publication key based on the destination and event.

On a timeout after submission, reconcile the platform result before retrying. A failed local request does not prove the remote post was not created. Preserve attempt history and use bounded retries. Log partial failures clearly and support a publishing kill switch.

## Configuration and integrations

All deployment-specific settings belong in environment variables or a secret store; see [CONFIGURATION.md](CONFIGURATION.md). Keep a draft-only path that does not require Instagram credentials.

Before implementing Instagram publishing, verify the current official Meta documentation for the supported account type, authentication, permissions, media upload or hosting requirements, token lifecycle, and rate limits. These requirements are not assumed to be solved by this plan. If temporary public media hosting is required, expose only the eligible derivative, expire it promptly, and keep the original library private.

Verify the installed Immich API and model-provider interfaces against their official documentation when implementing each adapter. Never use invented endpoints or hardcoded personal service addresses.

## Build order

### 1. One local edition

Accept one deliberately selected image and an observation record. Produce a headline, a short article, a cover, and the source-linked dispatch record. Save locally without publishing.

Done when a person can inspect the source, factual claims, and final cover together. Include a privacy hold example and a case where the correct outcome is no story.

### 2. Immich intake

Add configured selection, incremental discovery, pagination, and persistent event IDs. Group bursts conservatively. Record capture-time uncertainty. Retrying discovery must not create duplicate events.

Done when several runs over a fixed selection produce stable event counts and unrelated assets are never selected. Keep publishing disabled.

### 3. Editorial automation and rendering

Connect a configurable vision-capable model, enforce structured output, and validate claims against recorded evidence. Render the approved headline with measured wrapping. Bound model calls and operating cost.

Done when a small varied collection produces useful drafts, uncertain species and identities remain uncertain, private material is held, and long headlines never overflow. Model-provided confidence alone does not satisfy an eligibility check.

### 4. Instagram publishing

Implement the supported official publishing route with explicit account configuration, kill switch, atomic claims, and receipts. Validate one controlled publication before scheduled operation.

Done when duplicate runs, overlapping workers, and an ambiguous timeout cannot silently create duplicate posts, and disabling publishing prevents every outbound publication attempt.

### 5. Autonomous operation

Choose cadence and cost limits using actual capture volume. Add one scheduler, health reporting, token-expiry handling, and bounded retries. Successful runs may publish nothing. Keep comments, direct messages, follows, and other social interactions outside the initial scope.

Done when the system runs unattended within its configured selection and posting limits, records reasons for skips and holds, and can be stopped with the kill switch.

## Verification priorities

Test meaningful boundaries: selection isolation, missing configuration, duplicate captures, clock uncertainty, unsupported claims, privacy holds, text overflow, disabled publishing, concurrent attempts, and reconciliation after a timeout. Use synthetic fixtures or explicitly distributable samples. Run no live publisher from routine tests.

## Decisions still open

- Wildlife selection and how its access can be restricted.
- Runtime and model provider, with actual cost and latency limits.
- Reliable privacy-screening approach and treatment of uncertain results.
- Instagram handle, account setup, and supported publishing route.
- Cadence, event-grouping threshold, and limits on repetitive sightings.
- Actual fonts, renderer stack, and storage retention.
- Whether individual animal recognition is feasible; species-level continuity is sufficient for the first release.
