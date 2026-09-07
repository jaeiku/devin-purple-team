# Devin Purple Team

**Autonomous security remediation for a telecom operator's Apache Superset
monitoring stack.** A red team injects deterministic synthetic vulnerabilities
into a fork of Apache Superset and files GitHub issues; the GitHub issue event
triggers a blue team that opens a budget-capped Devin session, which
investigates and opens a remediation pull request; a Blue Team SOC dashboard
makes the whole loop observable for engineers and engineering leaders. The
project as a whole is a **purple-team exercise**: red and blue running against
the same target so the defence can be measured.

| | |
| --- | --- |
| Solution repo | <https://github.com/jaeiku/devin-purple-team> (this repo) |
| Target fork | <https://github.com/jaeiku/superset> |
| Red-team issues (remediated + in progress) | <https://github.com/jaeiku/superset/issues?q=label%3Ared-team> |
| Devin fix PRs | <https://github.com/jaeiku/superset/pulls?q=is%3Apr+fix%28security%29> |
| Demo video (Loom) | _coming soon_ |
| Demo script | [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) (English + 한국어) |

Everything runs with `docker compose up`, and a **demo mode** (default) runs the
complete lifecycle with **no credentials and no writes to GitHub** — ideal for
recording a walkthrough. **Live mode** performs real GitHub writes on the fork
and real Devin sessions.

## Background: why a telco, why now

2025 was the worst year on record for Korean telecom security: all three major
carriers suffered subscriber data breaches, drawing record regulatory fines and
lasting reputational damage. Korea's three operators serve ~50 million people,
so a single breach can expose 10M+ subscribers, and because identity is
centrally keyed on the resident registration number plus phone number, a PII
leak cascades into credit and payment fraud far more readily than elsewhere.

This project imagines an operator that runs Apache Superset as its
security-monitoring dashboard and asks: **when a vulnerability is found in that
stack, can an autonomous agent fix it in minutes instead of waiting for a human
on-call rotation?** Borrowing the red/blue team split from operator security
practice:

- **Red team** — scripted adversary. Plants a realistic telco vulnerability
  (MD5-hashed subscriber PII, CDR deserialization, OSS/BSS credentials, ...)
  and files a GitHub issue, exactly as a scanner or pentester would.
- **Blue team** — automated defender. The GitHub issue is the trigger; it
  spins up a Devin session under hard ACU/concurrency guardrails, tracks it
  until the fix PR is merged, and runs the **Blue Team SOC** dashboard that
  answers "is this working?" for leadership.
- **Purple team** — the exercise itself: red and blue against one target with
  shared telemetry. The red↔blue feedback loop (fix outcomes feeding back into
  detection scenarios) is the natural next step — see *Next steps* in the demo
  script.

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
                        │  branch red-team/<vuln-id>/run-<n> + issue │
                        └───────────────┬──────────────────────────┘
                     3. issue trigger:  │  pull watcher (default),
                        poll / Actions  │  Actions workflow or webhook
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
                        │    BLUE TEAM SOC  (dashboard/ :8003)     │
                        │  SOC feed · live tiles · ACU vs ceiling  │
                        │  leader "is this working?" summary       │
                        └──────────────────────────────────────────┘
```

In live mode, the GitHub issue is the trigger for blue-team remediation.
By default, the blue team polls `GET /issues?labels=red-team&state=open`
every `POLL_INTERVAL_SECONDS` (15 seconds by default), so no inbound network
connection is required. GitHub Actions
(`devin-remediation.yml` with `BLUE_TEAM_URL`) and the raw webhook endpoint
(`/api/webhooks/github`) are push-based alternatives. All three paths collapse
on the per-issue idempotency key. In demo mode, no GitHub issue exists, so the
red team posts the simulated event directly to the blue team.

| Service | Path | Port | Responsibility |
| --- | --- | --- | --- |
| Red team | `red-team/` | 8001 | Vulnerability catalogue UI, numbered injection instances, issue creation |
| Blue team | `blue-team/` | 8002 | Issue intake (GitHub poller / Actions / webhook), Devin sessions, guardrails, lifecycle polling, issue comments |
| Blue team SOC | `dashboard/` | 8003 | SOC dashboard, metrics, event feed, leadership summary |
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
  repositories -> the fork*, then *Contents: Read and write*, *Issues: Read and
  write* and *Pull requests: Read and write* (the last one is what lets the
  blue team comment on issues and read PR merge state). Add *Workflows* and
  *Actions* if you want the optional GitHub Actions trigger installed for you.
- The database schema includes numbered injection instances and PR state
  tracking. If an existing Postgres volume is present after a schema change,
  reset it with `docker compose down -v`.

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

- Blue team SOC dashboard — <http://localhost:8003>
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
==> BLUE TEAM SOC - dashboard state
  verdict     : HEALTHY - Autonomous remediation is working: 8 fix PR(s) opened,
                8 merged (8/8 findings remediated).
```

In demo mode the GitHub commit/issue, the Devin session and the resulting PR
(including its merge, after a short delay) are simulated deterministically
(stable pseudo-SHAs and per-instance issue numbers) and recorded in the shared
store, so every state transition, guardrail decision and dashboard tile behaves
exactly as it does in live mode. Because no GitHub issue exists in demo mode,
the red team posts the simulated issue event straight to the blue team; in live
mode that hand-off does not exist and GitHub is the only trigger.

Every Inject click creates a new numbered instance, branch and issue. The blue
team remains idempotent per issue number, so retries for one issue do not create
duplicate Devin sessions.

---

## Going live

1. Fill in `.env`:

   ```dotenv
   DEMO_MODE=false
   DEVIN_API_KEY=...
   GITHUB_TOKEN=...            # repo scope on the fork only
   GITHUB_ISSUE_ASSIGNEE=...   # GitHub login assigned to red-team issues
   SUPERSET_FORK_REPO=jaeiku/superset
   MAX_ACU_PER_SESSION=10
   MAX_CONCURRENT_SESSIONS=2
   GLOBAL_BUDGET_ACU_CEILING=100
   ```

2. `docker compose up --build`.
3. Pick a vulnerability type in the red team console. Each Inject click commits
   the synthetic file to a new `red-team/<vuln_id>/run-<n>` branch in the fork
   and opens a labelled issue titled with `(run #n)`, assigned to
   `GITHUB_ISSUE_ASSIGNEE` (defaults to the fork owner). The red team's job
   ends here — it never calls the blue team in live mode.
4. Within `POLL_INTERVAL_SECONDS` the blue team sees the new open `red-team`
   issue on GitHub, runs the guardrails, and creates a Devin session with
   `max_acu_limit` set from `MAX_ACU_PER_SESSION`. It comments the session link
   on the issue, polls the session, and comments again with the PR when Devin
   finishes. A Devin session that reports `blocked` while a PR exists is
   treated as a completed session (Devin is waiting on human review).
5. The blue team then watches the PR itself. The finding counts as
   **remediated** only when the PR is merged; a closed-unmerged PR surfaces as
   `pr_closed`.

Observed timings from live runs: Inject → issue in a few seconds; issue →
Devin session ≤ 15 s (poll interval); session → fix PR roughly 2–5 minutes
depending on the vulnerability.

ACU accounting: the blue team asks `GET /v1/enterprise/consumption` for real
usage. That endpoint is only available on plans where it has been enabled; when
it is not, a finished session is charged a **random 1–4 ACU estimate** (never
above its cap) and the dashboard labels spend as *estimated*. The per-session
`max_acu_limit` is still enforced by Devin regardless.

Every secret is read from the environment; nothing is hardcoded.

### GitHub issue triggers

Copy `blue-team/github-actions/devin-remediation.yml` into the fork as
`.github/workflows/devin-remediation.yml` for the Actions path. It fires on
`issues: opened` / `labeled`, filters to the `red-team` / `security` labels,
and POSTs the issue to the blue-team backend:

`BLUE_TEAM_URL` must be reachable from GitHub (public host or a tunnel such as
`ngrok`/Cloudflare Tunnel). The raw webhook alternative sends the GitHub
`issues` payload to `/api/webhooks/github`.

Repository secrets to add in the fork (Settings → Secrets and variables →
Actions):

| Secret | Purpose |
| --- | --- |
| `BLUE_TEAM_URL` | Public URL of the blue team backend for Actions |
| `DEVIN_API_KEY` | Devin API key used by the blue team backend |
| `MAX_ACU_PER_SESSION` | Optional per-session ACU cap |

The polling watcher, Actions workflow and webhook all use the same per-issue
idempotency key, so duplicate delivery (e.g. one `opened` plus three `labeled`
events for a single issue) collapses onto one Devin session; the extra
deliveries are logged as `session_deduplicated`. For a local demo without a
public URL, the polling watcher alone is sufficient.

---

## Vulnerability catalogue

Each entry is a **vulnerability type**, represented by a self-contained
synthetic file added to the fork (never an edit to real Superset internals).
Every injection creates a new numbered instance with a
`red-team/<vuln_id>/run-<n>` branch and an issue titled
`... (run #n)`. Telco-flavoured, mirroring an operator's Superset deployment.

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
- **Injection instances** — repeated Inject clicks intentionally create new
  branches and issues; blue-team idempotency remains scoped to each issue
  number.
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
- **Leadership** — a plain-language "is this working?" verdict
  (`idle / in_progress / healthy / attention / degraded / budget_blocked`) with
  detection→PR coverage, median time to PR, mean ACU per remediation and budget
  consumed.

Each issue row shows a unified **remediation stage** derived from two raw
states that are also displayed: the Devin session status and the PR state.
The lifecycle is `queued → investigating → pr_open → merged`; `failed`,
`refused` and `pr_closed` are terminal alternatives. A finding is
**remediated** only after its fix PR is merged. Timestamps are stored in UTC
and rendered in the viewer's local time zone.

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
| POST | `/api/events/issue-opened` | Issue intake used by Actions or demo mode |
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

After a schema change, an existing Postgres volume must be reset with
`docker compose down -v` before restarting the stack.
