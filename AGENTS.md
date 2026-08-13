# Agent Directives & Architecture Guide for PeerChecker

## Project Overview
PeerChecker is an automated Python Telegram Bot (`pyTelegramBotAPI`) for School 21 (Nizhny Novgorod campus) that monitors, validates, and filters new peer accounts across target coalitions/tribes using the S21 EduPower Keycloak OpenAPI.

---

## Key Architecture & Core Rules

### 1. Peer Validation Logic (`PeerValidator`)
A peer is evaluated based on three strict criteria:

1. **Wave Project Mapping (`TARGET_PROJECT_IDS_<WAVE_NAME>`):**
   - Fetched via `GET /v1/participants/{login}` (`className` field, e.g. `26_08_NN`).
   - Project IDs to check are dynamically determined from `TARGET_PROJECT_IDS_<WAVE_NAME>` environment variables (e.g. `TARGET_PROJECT_IDS_26_04_NN`).
   - If the peer's wave is not configured in environment variables or an API error (non-2xx response status code) occurs during project status requests, validation short-circuits with status `SKIPPED_PEERS` and records the explicit skip reason.
   - Skipped peers are saved to SQLite DB so they are remembered in `known_logins` and omitted from subsequent scans. They are included in `/export` and monitoring scan report attachments (`{tribe}_skipped.txt`). Logins of approved peers can be exported via `/export_verified_logins`.
   - `total_xp` is retrieved directly from the `expValue` profile attribute returned by `GET /v1/participants/{login}` without redundant experience-history API calls.

2. **Accepted Target Projects:**
   - Checked via `GET /v1/participants/{login}/projects/{projectId}` across the project IDs mapped to the peer's wave.
   - Peer must have at least `MIN_ACCEPTED_PROJECTS` (default: 3) in `ACCEPTED` status.

3. **Peer Feedback Scores:**
   - Checked via `GET /v1/participants/{login}/feedback`.
   - All 4 verifier feedback fields (`averageVerifierPunctuality`, `averageVerifierInterest`, `averageVerifierThoroughness`, `averageVerifierFriendliness`) must be strictly > 0.

- **Status Assignment & Lifecycle:**
  - `VERIFIED`: Wave projects configured, `ACCEPTED` projects >= 3, and all 4 feedback scores > 0.
  - `SUSPICIOUS`: Wave projects configured, but either accepted projects < 3 or feedback scores are 0 (test/inactive accounts).
  - `SKIPPED_PEERS`: Wave unconfigured in environment variables or API error encountered during project status checks.
  - `EXPELLED`: Peer was previously saved in SQLite DB (in any active status), but is missing from target coalition API responses during subsequent scans.
  - **Restoration Transition:** If a peer with status `EXPELLED` appears in target coalition API responses again, they are re-validated via `validate_peer(...)` and automatically moved back to their active status (`VERIFIED`, `SUSPICIOUS`, or `SKIPPED_PEERS`). A peer belongs to exactly one status at any given time.
  - **Telegram Card Rendering:** Status strings sent in peer cards (`_build_peer_card_content`) escape Telegram Markdown v1 special characters (e.g. `SKIPPED\_PEERS`) to avoid entity parsing errors.
  - **Notification Suppression:** Automatic periodic background scans (`is_background=True`) suppress Telegram admin notifications when no changes occur (0 new/restored peers, 0 expelled peers, 0 new skipped peers). Notifications are sent strictly upon changes. Manual scans (`/check_now`) always send output reports.

### 2. Storage & Database Concurrency (`Storage`)
- SQLite database connections use WAL journal mode (`PRAGMA journal_mode=WAL;`), a 30.0s connection timeout, exponential backoff retries on `sqlite3.OperationalError` (database is locked), and auto-closing `connection_scope()` context managers to prevent database connection leaks across concurrent Telegram command handlers and background threads.
- Aggregated database statistics (`get_stats()`) accurately separate `VERIFIED`, `SUSPICIOUS`, and `SKIPPED_PEERS` metrics. Automatic SQL migration (`UPDATE peers SET status = 'SKIPPED_PEERS' WHERE status = 'SKIPPED_WAVE'`) maintains backward compatibility with legacy database files.
- Peer records are upserted via `ON CONFLICT(login) DO UPDATE`, preserving manual moderation flags unless forced during recheck (`force=True` in `save_peer`).
- Bot state persistence (`bot_state` table) saves flags such as `monitoring_active`. Upon restart, `PeerCheckerBot` automatically restores active states and resumes background monitoring seamless loop if enabled prior to shutdown or crash.

### 3. API Client Stability (`S21ApiClient`)
- Reuses a persistent `requests.Session()` HTTP connection pool with context manager support (`__enter__`/`__exit__` and `close()`) to avoid TCP socket leaks.
- Features exponential backoff retries for transient 5xx HTTP server errors and rate limits (429), fast-failing on non-retryable 4xx HTTP errors (400, 403, 404), plus automatic bearer token refresh on 401 Unauthorized responses. Safe parsing for null/missing `expires_in` values from Keycloak.

### 4. Logging System (`setup_logging`)
- Configures dual handlers: `StreamHandler` (stdout) and `TimedRotatingFileHandler` writing to `config.log_file` (`data/app.log` by default).
- File logs rotate daily at midnight (`when='midnight'`, `interval=1`) keeping up to 30 days of archives (`backupCount=30`).
- Formats log entries with timestamps (`%Y-%m-%d %H:%M:%S`), log levels, logger module names, filenames, and function names.
- Filters out verbose info noise from third-party libraries (e.g. `telebot` log level set to `WARNING`).

---

## Important Constraints & Guidelines for AI Agents

1. **Git Commit Format:**
   - All git commit messages MUST strictly follow the format:
     `<PREFIX>: <one sentence description in English>`
   - Supported semantic prefixes:
     - `ADD:` — Adding new features, commands, files, or tests.
     - `UPD:` — Updating, improving, or refactoring existing code/logic.
     - `FIX:` — Fixing bugs, errors, or defects.
     - `RM:` — Removing deprecated code, files, or features.
     - `DOC:` — Documentation updates (`README.md`, `AGENTS.md`, docstrings) without logic changes.

2. **README Formatting Rule:**
   - Do NOT use emojis in `README.md`. Keep documentation in plain, clean markdown.

3. **PROMT.md Privacy:**
   - `PROMT.md` must remain in local directory `/home/lenyldes/PeerChecker/PROMT.md` but MUST NOT be committed to git remote history (ensure it remains in `.gitignore`).

4. **Instant Database Persistence:**
   - To prevent progress loss if scans are interrupted, every validated peer MUST be written to SQLite database immediately after validation via `self.storage.save_peer(val_res)`.

5. **Verbose Request Logging:**
   - Detailed step-by-step logging must be maintained during peer validation (wave match check, project GET statuses, feedback GET scores, and final verdict).

6. **Automatic Documentation Maintenance:**
   - After making any functional, architectural, or configuration changes, AI agents MUST automatically update both `README.md` and `AGENTS.md` so documentation remains 100% accurate and up-to-date without needing explicit user reminders.


---

## Local Verification & Testing Commands

To run unit tests:
```bash
python3 -m pytest tests/ -v
```
