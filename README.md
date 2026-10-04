# Aangan — Parcel Handover

A Flask reception desk for a fictional Ahmedabad apartment community. Record a parcel, share a private pickup code and hand it over exactly once—with an audit trail that survives retries and competing requests.

The demo uses a teal-and-sand lobby register with parcel tickets, resident views and clear expiry states. It works entirely on your machine; it sends no SMS, emails or delivery notifications.

## Working screenshots

![Aangan desktop parcel register](docs/screenshots/desktop.png)

![Aangan mobile parcel register](docs/screenshots/mobile.png)

## What you can try

- **Reception:** record arrivals for a resident, see all parcels, reissue expired or locked codes and verify collection.
- **Resident:** see only your own parcels and submit the private code for a handover. Signing in does not grant access to another flat’s parcels.
- **Single-use collection:** code verification, parcel state, handover receipt and audit event commit together in one transaction.
- **Safe retries:** the same authenticated actor can retry an identical handover command without creating a second receipt. Reusing a key for another actor, parcel or code fails.
- **Code safety:** HMAC-SHA256 digests in SQLite; 24-hour expiry; five incorrect attempts lock the code; reissuing invalidates the old digest.
- **Race protection:** changing a parcel requires its current revision; independent SQLite connections serialize conflicting handovers and reissues.

## Assumptions and boundaries

Prerna Residency, every person and every sender are fictional. The examples are hand-written synthetic fixtures; no resident data was scraped. Reception gives a code to the intended resident privately, outside this application. A code is shown once in the arrival/reissue response, never in parcel lists or event history. A resident may complete their own demo handover; a real reception process would require staff to confirm the physical exchange.

This is a local portfolio application, not a production housing-management service. There is no self-registration, password reset, SMS identity verification, courier integration, parcel photograph or multi-building tenancy. Public demo passwords and fixture codes are intentional; replace this account model before any real deployment.

## Stack

Python 3.12, Flask 3.1, SQLite, Werkzeug scrypt password hashing, server-side input validation, cookie sessions with CSRF tokens, and plain HTML/CSS/JavaScript. No Node build step or paid service is required.

## Local setup

Use Python **3.11 or newer**. Commands below assume a shell opened in the repository directory.

```bash
git clone https://github.com/Siddh-sys-rgb/aangan-parcel-handover.git
cd aangan-parcel-handover
```

A downloaded source ZIP works too: extract it, then open a shell in its root.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python app.py --port 8112
```

### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe app.py --port 8112
```

Open **http://127.0.0.1:8112**. Stop the server with `Ctrl+C`. For running without test tools, install `requirements.txt` instead. `requirements-tested.txt` records the exact packages used for the checked local environment.

### Launch from any directory

Paths for templates, assets and the default database resolve from `app.py`, so your shell does not need to remain inside the source directory.

```bash
/full/path/aangan-parcel-handover/.venv/bin/python /full/path/aangan-parcel-handover/app.py --port 8112 --data-dir /full/path/aangan-demo-data
```

Windows equivalent:

```powershell
& "C:\full\path\aangan-parcel-handover\.venv\Scripts\python.exe" "C:\full\path\aangan-parcel-handover\app.py" --port 8112 --data-dir "C:\full\path\aangan-demo-data"
```

The server binds **127.0.0.1**, never all network interfaces, and debug mode is off. `--no-demo` initializes an empty database for inspection/testing; it does **not** create accounts. Provisioning non-demo accounts and production deployment are deliberately outside the current scope.

## Demo accounts

| Persona | Email | Password | Access |
|---|---|---|---|
| Kavita Shah | `reception@aangan.demo` | `Lobby@2026` | Reception, every parcel |
| Aarav Patel | `aarav@aangan.demo` | `Home@2026` | Flat A-302 only |
| Nisha Desai | `nisha@aangan.demo` | `Home@2026` | Flat B-104 only |
| Rohan Mehta | `rohan@aangan.demo` | `Home@2026` | Flat A-205 only |

The login form contains account-selection shortcuts. They fill credentials; the backend still verifies hashed passwords and the selected account’s permissions.

### Five-minute walkthrough

1. Sign in as Kavita. Four synthetic arrivals are visible; Rohan’s code has already expired.
2. Choose **Record arrival**, select Aarav, enter a sender and description. Save it and note the six-digit code shown in the green panel. Dismissing or reloading hides that plaintext code.
3. Open the new parcel and enter an incorrect code. The error is visible and no handover is recorded.
4. Enter the issued code, choose **Verify & hand over**, then inspect the collected card and event trail.
5. Sign out, sign in as Nisha and verify that Aarav’s parcels are absent.
6. Return to reception, open Rohan’s parcel and **Issue a new pickup code**. The old code cannot collect the parcel.

Fresh fixture pickup codes are parcel 1 `246810` (Aarav), parcel 2 `135790` (Nisha), parcel 3 `112233` (Rohan, expired), and parcel 4 `445566` (Aarav). Fixture expiry is relative to the first startup; after a day, issue fresh codes. These predictable fixture codes demonstrate behavior and are not secret production credentials.

## Data and reset

Default runtime data lives in ignored `instance/`:

- `app.sqlite3`: users, parcels, handover receipts and events.
- `.session-secret`: persistent randomly generated signing/HMAC secret, created with owner-only permissions.
- SQLite WAL/SHM files may exist while running.

Restarting preserves records and the secret. Seeding runs only when there are no users, so a restart does not duplicate arrivals. Stop the server and delete the **entire demo data directory** to start afresh; on the next launch synthetic accounts and relative expiry times are recreated. Do not delete only the secret while keeping a database: its digests and sessions depend on the original secret. Do not commit runtime data or real credentials.

## API

First call `GET /api/session` to obtain `csrf_token` and a session cookie. Send `X-CSRF-Token` with **every** modifying request, including sign-in and sign-out. The token rotates after sign-in/sign-out. JSON requests need `Content-Type: application/json`; use one cookie jar across calls. Responses are JSON and are marked `Cache-Control: no-store`.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | Local storage health; no authentication required |
| GET | `/api/session` | Current user, CSRF token and demo flag |
| POST | `/api/login` | `{email, password}`; hashed-password verification |
| POST | `/api/logout` | Clear authenticated session |
| GET | `/api/residents` | Reception-only resident directory |
| GET | `/api/parcels` | Role/ownership-scoped parcel list; no code digests |
| POST | `/api/parcels` | Reception arrival: `{resident_id, sender, description}` |
| GET | `/api/parcels/{id}/events` | Ownership-scoped, retained audit trail |
| POST | `/api/parcels/{id}/reissue` | Reception only: `{revision}`; fresh plaintext code returned once |
| POST | `/api/parcels/{id}/handover` | `{revision, pickup_code, retry_key}`; atomic collection |

Example collection body:

```json
{"revision":1,"pickup_code":"246810","retry_key":"pickup_demo_command_001"}
```

Keep the same `retry_key` and payload after a network failure. A confirmed identical retry returns `replayed: true`; a new command against a collected parcel fails. Validation errors return `400`, unauthenticated access `401`, forbidden role/CSRF/origin `403`, inaccessible records `404`, revision/state conflicts `409`, code lock `423`, failed sign-in throttle `429`, and unavailable storage `503`.

## Architecture and schema

```text
HTML/CSS/JS → app.py (HTTP, sessions, CSRF, role-scoped routes)
                    → domain.py (state transitions, revision checks)
                    → core.py (SQLite, transactions, validation)
                    → SQLite (WAL, foreign keys, immutable audit triggers)
```

| Table | Responsibility |
|---|---|
| `users` | Identity, role, flat and scrypt password hash |
| `parcels` | Owner, description, status, revision, code digest/expiry/attempt count |
| `handovers` | One receipt per parcel; unique retry key, actor and payload fingerprint |
| `events` | Append-only parcel audit events; no submitted or generated code |
| `login_attempts` | Fifteen-minute failed-sign-in throttle per hashed email identity |

`BEGIN IMMEDIATE` acquires a writer lock before validation. A successful pickup commits the parcel update, unique receipt and audit event together. A failed audit insert rolls back collection. Read APIs omit secret hashes, failed guesses and retry fingerprints. Per-app cookie name `aangan_session` avoids accidental collisions with other localhost demos.

SQLite suits a single local reception desk; its writer serialization is a deliberate tradeoff. There is no distributed worker, external database, message queue or production throughput claim. Clock comparisons use server UTC Unix timestamps; the browser formats display dates for Indian locale.

## Verification

```bash
python -m pytest --cov=app --cov=core --cov=domain --cov-report=term-missing --cov-fail-under=90
python -m pip check
node --check static/app.js
```

Node is optional for the app and used only to check JavaScript syntax. Tests use temporary databases and never mutate your demo records. The local suite has **63 passing tests**, covering ownership, authentication/CSRF, validation limits, expiry and lockout, old-code invalidation, identical retries, conflicting commands, independent-connection races, audit rollback and immutable receipts/events. Combined statement-and-branch coverage is recorded in [`docs/verification.json`](docs/verification.json); it excludes browser rendering and is not a security certification.

The repository includes a Windows/Linux CI matrix. Real working desktop/mobile screenshots are under `docs/screenshots/`. Detailed implementation reasoning and review exercises are maintained privately outside this repository.

## Repository layout

```text
app.py                 Flask factory + CLI
core.py                Transaction/auth/validation infrastructure
domain.py              Schema + parcel business rules
templates/index.html   Accessible forms, native dialogs and register
static/                Responsive theme and browser interactions
tests/                 Isolated API/domain/concurrency tests
docs/                  Verification evidence and real demo screenshots
requirements*.txt      Runtime, development and tested dependencies
.github/workflows/     Automated checks
```

## Engineering discussion points

Why commit a receipt and event in the same transaction? Why bind a retry key to the actor and code fingerprint? Why does code reissue invalidate an old code immediately? What changes for multiple buildings or SMS delivery? The implementation gives concrete examples for these interview conversations.
