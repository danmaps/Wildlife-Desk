# Configuration

The repository is public. Configuration examples use neutral placeholders and empty credentials. `scripts/wildlife_desk.py` loads configuration from `.env` and environment variables with credential redaction across logs, errors, and state files.

Copy `.env.example` to an ignored local `.env` or supply equivalent settings through a deployment secret store. Never paste live values into an issue, screenshot, fixture, README, or commit. Keep logs free of tokens and secret-bearing URLs.

| Variable | Purpose |
| --- | --- |
| `WILDLIFE_SOURCE_MODE` | Source adapter mode: `immich` (restricted Immich album) or `local` (explicit local directory). |
| `IMMICH_BASE_URL` | Deployment-specific Immich URL. Example uses a reserved `.invalid` domain. |
| `IMMICH_API_KEY` | Credential for the selected source access method. |
| `IMMICH_WILDLIFE_ALBUM_ID` | Explicit selection boundary for `immich` mode; missing selection stops intake. |
| `LOCAL_WILDLIFE_DIR` | Explicit selection boundary for `local` mode; missing or top-level root path stops intake. |
| `BURST_WINDOW_SECONDS` | Conservative burst grouping window in seconds (default `180`) when capture timestamps are reliable. |
| `AI_BASE_URL` | Optional endpoint for a standalone model adapter (not required when using an interactive MCP agent). |
| `AI_API_KEY` | Model-provider credential, if required. |
| `AI_MODEL` | Configured vision-capable model; optional when operating via the MCP/Skill studio. |
| `INSTAGRAM_ACCOUNT_ID` | Target publishing account identifier (or `me` for direct Instagram Login tokens). |
| `INSTAGRAM_ACCESS_TOKEN` | Publishing credential for the chosen official integration. |
| `INSTAGRAM_STAGE_DIR` | Local web-root directory where rendered cover derivatives are staged ephemerally during container creation and immediately deleted. |
| `INSTAGRAM_STAGE_BASE_URL` | Public HTTPS base URL corresponding to `INSTAGRAM_STAGE_DIR` so Meta can fetch the temporary derivative. |
| `PUBLISH_ENABLED` | Default `false`; blocks all outbound publication attempts when absent or false. |
| `MAX_POSTS_PER_DAY` | Initial daily cap, adjustable after observing actual volume. |
| `MAX_MODEL_CALLS_PER_RUN` | Bound external inference work per run. |
| `MAX_MODEL_COST_PER_RUN_USD` | Proposed cost cap; enforce only with reliable provider usage accounting, otherwise stop rather than claim a cap is enforced. |
| `TIMEZONE` | Reporting timezone; neutral default UTC. Do not infer camera time from this alone. |
| `STATE_DIR` | Local persistent records, EXIF-stripped inspection cache, publication receipts, and lock state (`./state`). |
| `OUTPUT_DIR` | Local rendered covers (`1080x1350`, `1080x1080` square crop, `270x338` thumb) and Pressroom output (`./output`). |
| `LOCAL_SERVER_PORT` | Local HTTP port for serving the Wildlife Desk Pressroom (`8766` by default). |
| `LOG_LEVEL` | Logging verbosity, with credential redaction at every level. |

Add camera-specific addresses, account configuration, temporary media-hosting settings, and deployment paths as environment variables if an implementation needs them. Do not add real values to the template. Supply private context through local configuration and private runtime records.

The default `.gitignore` excludes local environment files, media, state, outputs, logs, and common credential file formats. Ignore rules are a convenience, not a secret detector. Check the staged diff before every commit; store private files outside the repository whenever practical.
