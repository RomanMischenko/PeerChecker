# Agent Directives & Architecture Guide for PeerChecker

## Project Overview
PeerChecker is an automated Python Telegram Bot (`pyTelegramBotAPI`) for School 21 (Nizhny Novgorod campus) that monitors, validates, and filters new peer accounts across target coalitions/tribes using the S21 EduPower Keycloak OpenAPI.

---

## Key Architecture & Core Rules

### 1. Peer Validation Logic (`PeerValidator`)
A peer is evaluated based on three strict criteria:

1. **Wave / Class Name Filter (`TARGET_CLASS_NAME`):**
   - Fetched via `GET /v1/participants/{login}` (`className` field, e.g. `26_08_NN`).
   - If `TARGET_CLASS_NAME` is configured and the peer's `className` does not match, validation short-circuits with status `SKIPPED_WAVE`. Project and feedback API calls are skipped to conserve API rate limits.
   - Skipped wave peers are saved to SQLite DB so they are remembered in `known_logins` and omitted from subsequent scans.

2. **Accepted Target Projects:**
   - Checked via `GET /v1/participants/{login}/projects/{projectId}` across `TARGET_PROJECT_IDS` (default list: `73187, 73188, 73189, 73328, 73190, 73191, 73192, 73193, 73194, 73195, 73196` representing Week 01 & Week 02 projects).
   - Peer must have at least `MIN_ACCEPTED_PROJECTS` (default: 3) in `ACCEPTED` status.

3. **Peer Feedback Scores:**
   - Checked via `GET /v1/participants/{login}/feedback`.
   - All 4 verifier feedback fields (`averageVerifierPunctuality`, `averageVerifierInterest`, `averageVerifierThoroughness`, `averageVerifierFriendliness`) must be strictly > 0.

- **Status Assignment:**
  - `VERIFIED`: Target wave matched, `ACCEPTED` projects >= 3, and all 4 feedback scores > 0.
  - `SUSPICIOUS`: Target wave matched, but either accepted projects < 3 or feedback scores are 0 (test/inactive accounts).
  - `SKIPPED_WAVE`: Wave `className` does not match `TARGET_CLASS_NAME`.

### 2. Storage & Database Concurrency (`Storage`)
- SQLite database connection uses WAL journal mode (`PRAGMA journal_mode=WAL;`) and a connection timeout of 30.0 seconds to support safe concurrent access between Telegram bot commands and background monitoring threads.
- Peer records are upserted via `ON CONFLICT(login) DO UPDATE`, preserving manual moderation flags (`is_manual=CASE WHEN excluded.is_manual = 1 THEN 1 ELSE is_manual END`).

### 3. API Client Stability (`S21ApiClient`)
- Reuses a persistent `requests.Session()` HTTP connection pool to avoid TCP socket exhaustion.
- Features exponential backoff retries for transient 5xx HTTP server errors and rate limits (429), plus automatic bearer token refresh on 401 Unauthorized responses.

---

## Important Constraints & Guidelines for AI Agents

1. **Git Commit Format:**
   - All git commit messages MUST strictly follow the format:
     `UPD: <one sentence description in English>`

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
