# Configuration

The repository is public. Configuration examples use neutral placeholders and empty credentials. There is no configuration loader yet; these names are a proposed interface for implementation.

Copy `.env.example` to an ignored local `.env` or supply equivalent settings through a deployment secret store. Never paste live values into an issue, screenshot, fixture, README, or commit. Keep logs free of tokens and secret-bearing URLs.

| Variable | Purpose |
| --- | --- |
| `IMMICH_BASE_URL` | Deployment-specific Immich URL. Example uses a reserved `.invalid` domain. |
| `IMMICH_API_KEY` | Credential for the selected source access method. |
| `IMMICH_WILDLIFE_ALBUM_ID` | Explicit selection boundary; missing selection must stop intake. |
| `AI_BASE_URL` | Optional endpoint for a compatible model adapter. |
| `AI_API_KEY` | Model-provider credential, if required. |
| `AI_MODEL` | Configured vision-capable model; choose when implementing the adapter. |
| `INSTAGRAM_ACCOUNT_ID` | Target publishing account identifier. |
| `INSTAGRAM_ACCESS_TOKEN` | Publishing credential for the chosen official integration. |
| `PUBLISH_ENABLED` | Proposed default `false`; no publishing when absent or false. |
| `MAX_POSTS_PER_DAY` | Proposed initial cap, adjustable after observing actual volume. |
| `MAX_MODEL_CALLS_PER_RUN` | Bound external inference work per run. |
| `MAX_MODEL_COST_PER_RUN_USD` | Proposed cost cap; enforce only with reliable provider usage accounting, otherwise stop rather than claim a cap is enforced. |
| `TIMEZONE` | Reporting timezone; neutral default UTC. Do not infer camera time from this alone. |
| `STATE_DIR` | Local persistent records, publication receipts, and lock state. |
| `OUTPUT_DIR` | Local rendered covers and draft outputs. |
| `LOG_LEVEL` | Logging verbosity, with credential redaction at every level. |

Add camera-specific addresses, account configuration, temporary media-hosting settings, and deployment paths as environment variables if an implementation needs them. Do not add real values to the template. Supply private context through local configuration and private runtime records.

The default `.gitignore` excludes local environment files, media, state, outputs, logs, and common credential file formats. Ignore rules are a convenience, not a secret detector. Check the staged diff before every commit; store private files outside the repository whenever practical.
