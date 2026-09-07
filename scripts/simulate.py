#!/usr/bin/env python3
"""End-to-end demo driver for the purple team platform.

Runs the full loop against the running compose stack without any clicking:

    inject vulnerability -> GitHub issue -> Devin session -> PR -> dashboard

Usage:
    python scripts/simulate.py                # inject 4 findings, watch to done
    python scripts/simulate.py --all          # inject the whole catalog
    python scripts/simulate.py --vuln tel-001-md5-subscriber-pii
    python scripts/simulate.py --budget-demo  # show the ACU ceiling refusing work

Environment (defaults match docker-compose):
    RED_TEAM_URL   default http://localhost:8001
    BLUE_TEAM_URL  default http://localhost:8002
    DASHBOARD_URL  default http://localhost:8003
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import urllib.error
import urllib.request
import json as jsonlib

RED = os.getenv("RED_TEAM_URL", "http://localhost:8001").rstrip("/")
BLUE = os.getenv("BLUE_TEAM_URL", "http://localhost:8002").rstrip("/")
DASH = os.getenv("DASHBOARD_URL", "http://localhost:8003").rstrip("/")
SIM_INTERVAL = float(os.getenv("SIMULATED_SESSION_SECONDS", "20"))

DEFAULT_SELECTION = [
    "tel-001-md5-subscriber-pii",
    "tel-003-clickhouse-query-injection",
    "tel-005-insecure-deserialization-cdr",
    "tel-006-jwt-auth-bypass",
]

BOLD, DIM, GREEN, YELLOW, RED_C, CYAN, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[36m", "\033[0m"
)


def call(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = jsonlib.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return jsonlib.loads(response.read() or b"{}")


def wait_for_services(timeout: float = 120.0) -> None:
    step(f"Waiting for services ({RED}, {BLUE}, {DASH})")
    deadline = time.time() + timeout
    for name, base in (("red-team", RED), ("blue-team", BLUE), ("dashboard", DASH)):
        while True:
            try:
                call(f"{base}/healthz")
                print(f"  {GREEN}up{RESET}   {name} {DIM}{base}{RESET}")
                break
            except (urllib.error.URLError, TimeoutError, OSError):
                if time.time() > deadline:
                    sys.exit(f"{RED_C}timed out waiting for {name} at {base}{RESET}")
                time.sleep(2)


def step(title: str) -> None:
    print(f"\n{BOLD}{CYAN}==>{RESET} {BOLD}{title}{RESET}")


def show_config() -> None:
    red_cfg = call(f"{RED}/api/config")
    blue_cfg = call(f"{BLUE}/api/config")
    mode = "DEMO (nothing is written to GitHub)" if red_cfg["demo_mode"] else "LIVE"
    print(f"  target repo : {red_cfg['target_repo']}")
    print(f"  mode        : {mode}")
    print(f"  guardrails  : {blue_cfg['guardrails']}")
    if not red_cfg["demo_mode"]:
        print(
            f"  {YELLOW}LIVE MODE: this will commit files and open issues in "
            f"{red_cfg['target_repo']} and spend real ACUs.{RESET}"
        )


def inject(vuln_ids: list[str] | None, everything: bool) -> list[dict[str, Any]]:
    step("RED TEAM - injecting synthetic vulnerabilities")
    body: dict[str, Any] = {} if everything else {"vuln_ids": vuln_ids}
    results = call(f"{RED}/api/inject-all", body)["results"]
    for result in results:
        if "error" in result:
            print(f"  {RED_C}fail{RESET} {result['vuln_id']}: {result['error']}")
            continue
        injection = result["injection"]
        marker = "new " if result.get("created") else "dupe"
        print(
            f"  {GREEN}{marker}{RESET} {injection['vuln_id']:<38} "
            f"run #{injection['instance']} {injection['severity']:<8} "
            f"issue #{injection['issue_number']} "
            f"{DIM}{injection['branch']}{RESET}"
        )
    return results


def show_guardrails() -> None:
    step("BLUE TEAM - guardrail decision for the next session")
    decision = call(f"{BLUE}/api/guardrails")
    colour = GREEN if decision["allowed"] else YELLOW
    print(f"  {colour}{decision['code']}{RESET}: {decision['reason']}")
    print(
        f"  committed {decision['acu_committed']} ACU of ceiling "
        f"{decision['acu_ceiling']}, {decision['active_sessions']} active"
    )


def watch(timeout: float) -> dict[str, Any]:
    step("BLUE TEAM - tracking Devin session lifecycle")
    deadline = time.time() + timeout
    last = ""
    overview: dict[str, Any] = {}
    while time.time() < deadline:
        call(f"{BLUE}/api/poll", {})
        overview = call(f"{DASH}/api/overview")
        m = overview["metrics"]
        line = (
            f"  sessions active={m['sessions_active']} "
            f"queued={m['sessions_queued']} "
            f"completed={m['sessions_completed']} failed={m['sessions_failed']} "
            f"refused={m['sessions_refused_budget']} "
            f"PRs={m['prs_opened']} merged={m['prs_merged']} "
            f"ACU={m['acu_spend']}/{m['acu_ceiling']}"
        )
        if line != last:
            print(line)
            last = line
        if (
            m["sessions_active"] == 0
            and m["sessions_queued"] == 0
            and m["sessions_total"] > 0
        ):
            break
        time.sleep(4)

    merge_deadline = min(deadline, time.time() + 3 * SIM_INTERVAL)
    while time.time() < merge_deadline:
        call(f"{BLUE}/api/poll", {})
        overview = call(f"{DASH}/api/overview")
        stages = [session.get("stage") for session in overview["sessions"]]
        if stages and all(stage == "merged" for stage in stages):
            break
        time.sleep(4)
    return overview


def report(overview: dict[str, Any]) -> None:
    step("PURPLE TEAM - dashboard state")
    m = overview["metrics"]
    summary = overview["summary"]
    print(f"  verdict     : {BOLD}{summary['verdict'].upper()}{RESET} - {summary['headline']}")
    print(f"  issues      : {m['issues_created']} raised, {m['remediated']} remediated")
    print(f"  sessions    : {m['sessions_total']} total, {m['success_rate_pct']}% success")
    print(f"  pull reqs   : {m['prs_opened']} ({m['prs_merged']} merged)")
    print(f"  ACU spend   : {m['acu_spend']} / {m['acu_ceiling']} ({m['budget_used_pct']}%)")
    print(f"  by category : {m['injections_by_category']}")
    for pr in m["pr_urls"]:
        print(f"    {DIM}{pr}{RESET}")
    print(f"\n  Open the SOC dashboard: {BOLD}{DASH}{RESET}")
    print(f"  Open the red team console: {BOLD}{RED}{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="inject the whole catalog")
    parser.add_argument(
        "--vuln", action="append", dest="vulns", help="inject a specific vuln_id"
    )
    parser.add_argument(
        "--budget-demo",
        action="store_true",
        help="inject the whole catalog to demonstrate the ACU ceiling refusing work",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--no-wait", action="store_true", help="inject and exit without watching"
    )
    args = parser.parse_args()

    wait_for_services()
    show_config()

    everything = args.all or args.budget_demo
    inject(args.vulns or DEFAULT_SELECTION, everything)
    show_guardrails()

    if args.no_wait:
        report(call(f"{DASH}/api/overview"))
        return 0

    overview = watch(args.timeout)
    overview = overview or call(f"{DASH}/api/overview")
    report(overview)

    if args.budget_demo:
        return 0

    # Non-zero exit so CI fails when the loop does not actually close.
    m = overview["metrics"]
    problems = []
    if m["sessions_failed"]:
        problems.append(f"{m['sessions_failed']} session(s) failed")
    if m["remediated"] < m["issues_created"]:
        problems.append(
            f"only {m['remediated']}/{m['issues_created']} finding(s) remediated"
        )
    if m["prs_merged"] < m["issues_created"]:
        problems.append(
            f"only {m['prs_merged']}/{m['issues_created']} pull request(s) merged"
        )
    unmerged = [
        session for session in overview["sessions"] if session.get("stage") != "merged"
    ]
    if unmerged:
        problems.append(f"{len(unmerged)} session(s) not at merged stage")
    if problems:
        print(f"\n  {RED_C}FAILED{RESET}: {'; '.join(problems)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
