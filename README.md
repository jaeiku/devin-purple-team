# Devin Purple Team

An event-driven, Dockerized demonstration of **Devin autonomously remediating
security vulnerabilities**. A red team injects deterministic synthetic
vulnerabilities into a fork of Apache Superset and files GitHub issues; a blue
team turns each issue into a budget-capped Devin session that opens a
remediation pull request; a purple-team SOC dashboard makes the whole loop
observable.

Everything runs with `docker compose up`, and a **demo mode** (default) runs the
complete lifecycle with **no credentials and no writes to GitHub** — ideal for
recording a walkthrough.

---

## Architecture

```text
                        ┌──────────────────────────────────────────┐
                        │        RED TEAM  (red-team/ :8001)       │
                        │  catalogue UI · deterministic injector   │
                        └───────────────┬──────────────────────────┘
                    1. commit synthetic │ 2. open labelled issue
                       vulnerable file  │    (security, red-team,
                                        │     severity:*, category:*)
                                        ▼
                        ┌──────────────────────────────────────────┐
                        │      GitHub: jaeiku/superset (fork)      │
                        │  branch red-team/<vuln-id> + issue       │
                        └───────────────┬──────────────────────────┘
                     3. issues: opened  │  (.github/workflows/
                        GitHub Actions  │   devin-remediation.yml)
                                        ▼
                        ┌──────────────────────────────────────────┐
                        │      BLUE TEAM  (blue-team/ :8002)       │
                        │  guardrails: idempotency · concurrency   │
                        │             · ACU budget ceiling         │
                        │  POST /v1/sessions ──► Devin  (4.)       │
                        │  poller: status · ACU · PR   (5.)        │
                        └───────────────┬──────────────────────────┘
                     6. PR + status     │
                        comment on the  ▼
                        issue      ┌──────────────────────────────┐
                                   │  Devin opens the fix PR      │
                                   │  "Closes #<issue>"           │
                                   └──────────────────────────────┘

     all three services read/write ──►  ┌────────────────────────────┐
     injections · issues · sessions ·   │  SHARED STORE (Postgres,   │
     ACU spend · structured events      │  or SQLite) — source of    │
                                        │  truth for budget + UI     │
                                        └─────────────┬──────────────┘
                                                      ▼
                        ┌──────────────────────────────────────────┐
                        │      PURPLE TEAM  (dashboard/ :8003)     │
                        │  SOC feed · live tiles · ACU vs ceiling  │
                        │  leader "is this working?" summary       │
                        └──────────────────────────────────────────┘
```

| Service | Path | Port | Responsibility |
| --- | --- | --- | --- |
| Red team | `red-team/` | 8001 | Vulnerability catalogue UI, deterministic + idempotent injection, issue creation |
| Blue team | `blue-team/` | 8002 | Issue intake (Actions workflow / webhook), Devin sessions, guardrails, lifecycle polling, issue comments |
| Purple team | `dashboard/` | 8003 | SOC dashboard, metrics, event feed, leadership summary |
| Shared | `shared/pt_shared` | – | Models, datastore, config, structured logging, GitHub client |
| Store | `postgres` | 5432 | Shared source of truth (SQLite also supported via `DATABASE_URL`) |

---

## Prerequisites

- Docker with the Compose plugin (`docker compose version`).
- Python 3.10+ on the host (only to run `scripts/simulate.py`).
- **Manual prerequisite for live mode:** the fork **`jaeiku/superset` must
  already exist**. Nothing in this repo creates it, and the GitHub client
  hard-refuses to write to any repository other than `SUPERSET_FORK_REPO`.
  GitHub disables **Issues** on forks by default - enable it under
  *Settings -> General -> Features* or issue creation fails with HTTP 410.
- Live mode also needs a Devin API key and a GitHub token with write access to
  that fork. For a fine-grained PAT: *Repository access -> Only select
  repositories -> the fork*, then *Contents: Read and write* and
  *Issues: Read and write*.

---

## Quick start (demo mode, no credentials)

```bash
git clone https://github.com/jaeiku/devin-purple-team.git
cd devin-purple-team
cp .env.example .env          # defaults are demo mode
docker compose up --build
```

Then, in a second terminal, drive the whole flow end to end:

```bash
python3 scripts/simulate.py           # 4 representative findings
python3 scripts/simulate.py --all     # the entire 8-vulnerability catalogue
```

Or do both in one command:

```bash
./scripts/demo.sh --all
```

Open:

- Purple team SOC dashboard — <http://localhost:8003>
- Red team console — <http://localhost:8001>
- Blue team API — <http://localhost:8002/api/sessions>

### What the demo shows

```text
==> RED TEAM - injecting synthetic vulnerabilities
  new  tel-001-md5-subscriber-pii            high      issue #9799
  new  tel-003-clickhouse-query-injection    critical  issue #9796
  ...
==> BLUE TEAM - guardrail decision for the next session
  concurrency_limit: 2 session(s) already in flight, limit is 2; queued for retry
==> BLUE TEAM - tracking Devin session lifecycle
  sessions active=2 queued=6 completed=0 failed=0 refused=0 PRs=0 ACU=0.0/100.0
  sessions active=2 queued=4 completed=2 failed=0 refused=0 PRs=2 ACU=12.1/100.0
  sessions active=0 queued=0 completed=8 failed=0 refused=0 PRs=8 ACU=44.0/100.0
==> PURPLE TEAM - dashboard state
  verdict     : HEALTHY - 8/8 injected vulnerabilities closed by a Devin PR.
```

In demo mode the GitHub commit/issue, the Devin session and the resulting PR
are simulated deterministically (stable issue numbers, stable pseudo-SHAs) and
recorded in the shared store, so every state transition, guardrail decision and
dashboard tile behaves exactly as it does in live mode.

Re-running the simulation is safe: injections and sessions are idempotent, so
findings show as `dupe` instead of duplicating issues or Devin sessions.

---

## Going live

1. Fill in `.env`:

   ```dotenv
   DEMO_MODE=false
   DEVIN_API_KEY=...
   GITHUB_TOKEN=...            # repo scope on the fork only
   SUPERSET_FORK_REPO=jaeiku/superset
   MAX_ACU_PER_SESSION=10
   MAX_CONCURRENT_SESSIONS=2
   GLOBAL_BUDGET_ACU_CEILING=100
   ```

2. `docker compose up --build`.
3. Pick a vulnerability in the red team console. It commits the synthetic file
   to `red-team/<vuln-id>` in the fork and opens a labelled issue.
4. The blue team creates a Devin session with `max_acu_limit` set from
   `MAX_ACU_PER_SESSION`, comments the session link on the issue, polls the
   session, and comments again with the PR when Devin finishes.

Every secret is read from the environment; nothing is hardcoded.

### GitHub Actions trigger in the fork

Copy `blue-team/github-actions/devin-remediation.yml` into the fork as
`.github/workflows/devin-remediation.yml`. It fires on `issues: opened` /
`labeled`, filters to the `red-team` / `security` labels, and then:

- **Preferred:** POSTs the issue to the blue team backend
  (`BLUE_TEAM_URL`), so guardrails, budget accounting and the dashboard stay
  authoritative. The backend must be reachable from GitHub (public host or a
  tunnel such as `ngrok`/Cloudflare Tunnel).
- **Fallback:** if `BLUE_TEAM_URL` is not set, the workflow calls
  `POST /v1/sessions` directly with `DEVIN_API_KEY` and comments the session
  link on the issue. Simpler to demo, but only the backend path records state
  in the shared store.

Repository secrets to add in the fork (Settings → Secrets and variables →
Actions):

| Secret | Purpose |
| --- | --- |
| `BLUE_TEAM_URL` | Public URL of the blue team backend (preferred path) |
| `DEVIN_API_KEY` | Devin API key (direct-invocation fallback) |
| `MAX_ACU_PER_SESSION` | Optional per-session ACU cap for the fallback path |

For a local demo without a public URL, the red team also hands issues straight
to the blue team over the internal network — the blue team is idempotent per
issue number, so both trigger paths can run simultaneously without creating
duplicate sessions.

---

## Vulnerability catalogue

Each entry is a **self-contained synthetic file** added to the fork (never an
edit to real Superset internals), with a deterministic branch, issue title and
remediation guidance. Telco-flavoured, mirroring an operator's Superset
deployment.

| ID | Category | Severity | Injected file |
| --- | --- | --- | --- |
| `tel-001-md5-subscriber-pii` | PII exposure | high | `telco_security/pii/subscriber_masking.py` |
| `tel-002-vulnerable-dependency` | Vulnerable dependency / RCE | critical | `telco_security/requirements.txt` |
| `tel-003-clickhouse-query-injection` | Query injection | critical | `telco_security/analytics/clickhouse_query_parser.py` |
| `tel-004-hardcoded-credentials` | Secrets exposure | critical | `telco_security/config/ossbss_client_config.py` |
| `tel-005-insecure-deserialization-cdr` | Insecure deserialization | critical | `telco_security/ingestion/cdr_ingest.py` |
| `tel-006-jwt-auth-bypass` | Auth bypass | critical | `telco_security/api/subscriber_auth.py` |
| `tel-007-ssrf-network-element` | SSRF | high | `telco_security/integrations/network_element_probe.py` |
| `tel-008-path-traversal-billing` | Path traversal | high | `telco_security/billing/report_download.py` |

---

## Guardrails

Budget protection is enforced in the blue team before any session is created,
using the shared store as the source of truth:

- **Idempotency** — one Devin session per issue, ever. The key is
  `<repo>#issue-<number>`, backed by a unique constraint and by the Devin API's
  own `idempotent` flag, so workflow re-runs and duplicate webhooks collapse
  onto the same session.
- **Per-session cap** — `MAX_ACU_PER_SESSION` is passed as `max_acu_limit` on
  `POST /v1/sessions`.
- **Concurrency** — at most `MAX_CONCURRENT_SESSIONS` sessions run at once.
  Excess issues are **queued**, not dropped, and the poller admits them as slots
  free up.
- **Global ceiling** — a session is admitted only if
  `spend so far + caps reserved by in-flight sessions + this session's cap ≤ GLOBAL_BUDGET_ACU_CEILING`.
  Reserving the cap up front is what makes the ceiling hard rather than
  observational. Refused sessions are recorded and surfaced on the dashboard.
- **Blast radius** — the GitHub client refuses to write to any repository other
  than `SUPERSET_FORK_REPO`, and the blue team ignores issues from any other
  repo.

---

## Observability

The dashboard has two views:

- **Operations** — live tiles (issues raised, active/queued/completed/failed
  sessions, PRs, success rate), an ACU-spend-vs-ceiling meter, category and
  severity breakdowns, a severity-coloured issue feed with per-issue progress,
  and a live structured-event log from all three services.
- **Leadership** — a plain-language "is this working?" verdict with
  detection→PR coverage, median time to PR, mean ACU per remediation and budget
  consumed.

Every service logs single-line JSON to stdout (SIEM-friendly) and mirrors the
same records into the `events` table, which is what the dashboard renders.

---

## API reference

Red team (`:8001`)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/catalog` | Full vulnerability catalogue |
| GET | `/api/injections` | Injection state |
| POST | `/api/inject` | Inject one vulnerability (`{"vuln_id": ...}`) |
| POST | `/api/inject-all` | Inject the catalogue or a subset |

Blue team (`:8002`)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/events/issue-opened` | Issue intake used by the Actions workflow |
| POST | `/api/webhooks/github` | Raw GitHub `issues` webhook intake |
| GET | `/api/guardrails` | Current guardrail decision and budget state |
| GET | `/api/sessions` | Devin session records |
| POST | `/api/poll` | Force a reconciliation pass |

Dashboard (`:8003`)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/overview` | Metrics, injections, sessions, summary, config |
| GET | `/api/summary` | Leadership verdict only |
| GET | `/api/events` | Recent structured events |

---

## Local development without Docker

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ./shared -r red-team/requirements.txt
export DATABASE_URL=sqlite:////tmp/purple/purple_team.db DEMO_MODE=true
(cd red-team  && uvicorn app.main:app --port 8001) &
(cd blue-team && uvicorn app.main:app --port 8002) &
(cd dashboard && uvicorn app.main:app --port 8003) &
python3 scripts/simulate.py
```

## Teardown

```bash
docker compose down       # stop
docker compose down -v    # stop and wipe the datastore
```
