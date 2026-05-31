#!/usr/bin/env python3
"""
seed_test_scenario.py
─────────────────────
Populates a linked ENGINEERING code repo (e.g., sandbox-backend) with a
realistic mix of issues, labels, severities, milestones, and closures so the
PMO dashboard's cross-repo sections all light up with verifiable data.

After running this, trigger the Dashboard update workflow on your PMO repo and
every cross-repo section will have content you can verify against the
"EXPECTED DASHBOARD STATE" printed at the end of this script's run.

This is SAFE and IDEMPOTENT-ish:
  - It tags everything it creates with a marker label `seed:test-data`
  - Re-running creates a fresh batch (it does not delete prior data)
  - To clean up, filter by `seed:test-data` label and bulk-close/delete manually

Env vars:
  SEED_TOKEN     — PAT with `repo` scope (or APP_TOKEN). Needs WRITE to the eng repo.
  ENG_REPO       — the engineering repo to seed, e.g., "PMOTestingOrg/sandbox-backend"
  PROJECT_SLUG   — the project label slug, e.g., "pilot-1" (creates project:pilot-1)

Usage (locally):
  SEED_TOKEN=ghp_xxx ENG_REPO=PMOTestingOrg/sandbox-backend PROJECT_SLUG=pilot-1 \
    python3 seed_test_scenario.py

Usage (GitHub Actions): see seed-test-scenario.yml workflow.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

TOKEN = (os.environ.get("SEED_TOKEN", "").strip()
         or os.environ.get("APP_TOKEN", "").strip()
         or os.environ.get("PROJECTS_TOKEN", "").strip())
ENG_REPO = os.environ.get("ENG_REPO", "").strip()
PROJECT_SLUG = os.environ.get("PROJECT_SLUG", "").strip()

GH_API = "https://api.github.com"

if not TOKEN:
    print("ERROR: SEED_TOKEN (or APP_TOKEN/PROJECTS_TOKEN) required.")
    sys.exit(1)
if not ENG_REPO or "/" not in ENG_REPO:
    print("ERROR: ENG_REPO must be set as owner/repo (e.g., PMOTestingOrg/sandbox-backend).")
    sys.exit(1)
if not PROJECT_SLUG:
    print("ERROR: PROJECT_SLUG must be set (e.g., pilot-1).")
    sys.exit(1)

PROJECT_LABEL = f"project:{PROJECT_SLUG}"
MARKER_LABEL = "seed:test-data"


def rest(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{GH_API}{path}", data=data, method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            text = resp.read()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        return {"_error_code": exc.code, "_error_body": exc.read().decode()}


def ensure_label(name, color, description):
    """Create a label if it doesn't exist."""
    existing = rest("GET", f"/repos/{ENG_REPO}/labels/{urllib.parse.quote(name)}")
    if "_error_code" not in existing:
        return  # exists
    result = rest("POST", f"/repos/{ENG_REPO}/labels",
                  body={"name": name, "color": color, "description": description})
    if "_error_code" in result and result["_error_code"] != 422:
        print(f"  Warning: could not create label {name}: {result}")


def create_milestone(title, due_days_from_now, description=""):
    """Create a milestone. Returns milestone number or None."""
    due = None
    if due_days_from_now is not None:
        due_dt = datetime.now(timezone.utc) + timedelta(days=due_days_from_now)
        due = due_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {"title": title, "description": description}
    if due:
        body["due_on"] = due
    result = rest("POST", f"/repos/{ENG_REPO}/milestones", body=body)
    if "_error_code" in result:
        # Might already exist — try to find it
        existing = rest("GET", f"/repos/{ENG_REPO}/milestones?state=all&per_page=100")
        if isinstance(existing, list):
            for m in existing:
                if m["title"] == title:
                    return m["number"]
        print(f"  Warning: could not create milestone {title}: {result}")
        return None
    return result["number"]


def close_milestone(number):
    rest("PATCH", f"/repos/{ENG_REPO}/milestones/{number}", body={"state": "closed"})


def create_issue(title, body, labels, milestone=None):
    """Create an issue. Returns issue number or None."""
    payload = {"title": title, "body": body, "labels": labels}
    if milestone:
        payload["milestone"] = milestone
    result = rest("POST", f"/repos/{ENG_REPO}/issues", body=payload)
    if "_error_code" in result:
        print(f"  ERROR creating issue '{title}': {result}")
        return None
    return result["number"]


def close_issue(number):
    rest("PATCH", f"/repos/{ENG_REPO}/issues/{number}", body={"state": "closed"})


def main():
    import urllib.parse
    globals()["urllib"].parse = urllib.parse

    print(f"Seeding test scenario into {ENG_REPO}")
    print(f"Project label: {PROJECT_LABEL}\n")

    # ── Step 1: Ensure all labels exist ──
    print("Step 1: Ensuring labels exist...")
    ensure_label(PROJECT_LABEL, "0E8A16", f"Tracks {PROJECT_SLUG} (PMO project)")
    ensure_label("project:none", "EEEEEE", "Not associated with any tracked PMO project")
    ensure_label("project:other-demo", "5319E7", "A different project (for filter testing)")
    ensure_label("needs-pm", "FBCA04", "Needs PM input/decision")
    ensure_label("severity:S1", "B60205", "Blocker")
    ensure_label("severity:S2", "D93F0B", "Major")
    ensure_label("severity:S3", "FBCA04", "Minor")
    ensure_label("severity:S4", "C2E0C6", "Trivial")
    ensure_label("type:bug", "D73A4A", "Defect")
    ensure_label(MARKER_LABEL, "BFD4F2", "Auto-generated test data — safe to delete")
    print("  Done.\n")

    # ── Step 2: Create milestones ──
    print("Step 2: Creating milestones...")
    # Sprint 5: due in 10 days, will have mixed open/closed (sprint in progress)
    sprint5 = create_milestone("Sprint 5", 10, "Current sprint - in progress")
    print(f"  Sprint 5 → milestone #{sprint5} (due in 10 days)")
    # Sprint 4: due 3 days ago, overdue, for testing overdue indicator
    sprint4 = create_milestone("Sprint 4", -3, "Previous sprint - overdue")
    print(f"  Sprint 4 → milestone #{sprint4} (overdue by 3 days)")
    # Sprint 6: due in 25 days, empty (planned ahead)
    sprint6 = create_milestone("Sprint 6", 25, "Future sprint - planned")
    print(f"  Sprint 6 → milestone #{sprint6} (due in 25 days, empty)")
    print("  Done.\n")

    created = {
        "tracked_open": [],
        "tracked_closed": [],
        "needs_pm_tracked": [],
        "needs_pm_untagged": [],
        "s1_tracked": [],
        "s2_tracked": [],
        "s1_untracked": [],
        "other_project": [],
        "unlabeled": [],
    }

    # ── Step 3: Tracked issues (project:pilot-1) ──
    print("Step 3: Creating tracked issues (with project label)...")

    # 3 open tracked issues in Sprint 5
    for i in range(1, 4):
        num = create_issue(
            f"[TEST] Implement feature module {i}",
            f"Tracked work item {i} for {PROJECT_SLUG}. Part of Sprint 5.",
            [PROJECT_LABEL, MARKER_LABEL],
            milestone=sprint5,
        )
        if num:
            created["tracked_open"].append(num)
            print(f"  #{num} open tracked (Sprint 5)")

    # 2 closed tracked issues in Sprint 5 (so Sprint 5 shows partial progress)
    for i in range(1, 3):
        num = create_issue(
            f"[TEST] Completed task {i}",
            f"Finished work item {i} for {PROJECT_SLUG}.",
            [PROJECT_LABEL, MARKER_LABEL],
            milestone=sprint5,
        )
        if num:
            close_issue(num)
            created["tracked_closed"].append(num)
            print(f"  #{num} closed tracked (Sprint 5) — recent closure")

    # 1 issue in overdue Sprint 4
    num = create_issue(
        "[TEST] Carryover task from Sprint 4",
        "This was supposed to be done in Sprint 4 (now overdue).",
        [PROJECT_LABEL, MARKER_LABEL],
        milestone=sprint4,
    )
    if num:
        created["tracked_open"].append(num)
        print(f"  #{num} open tracked (Sprint 4 - overdue)")
    print("  Done.\n")

    # ── Step 4: needs-pm issues ──
    print("Step 4: Creating needs-pm issues...")

    # needs-pm WITH project label (correct case)
    num = create_issue(
        "[TEST] Question: BRD interpretation for module 2",
        "Engineer needs PM clarification on requirements. Has project label.",
        [PROJECT_LABEL, "needs-pm", MARKER_LABEL],
    )
    if num:
        created["needs_pm_tracked"].append(num)
        print(f"  #{num} needs-pm WITH project label")

    # needs-pm WITHOUT any project label (the lenient-surfacing case)
    num = create_issue(
        "[TEST] Scope question — no project label yet",
        "Engineer flagged for PM but forgot to apply project label. "
        "Should appear in the 'missing project label' subsection.",
        ["needs-pm", MARKER_LABEL],
    )
    if num:
        created["needs_pm_untagged"].append(num)
        print(f"  #{num} needs-pm WITHOUT project label")
    print("  Done.\n")

    # ── Step 5: Severity bugs ──
    print("Step 5: Creating severity-labeled bugs...")

    # S1 + project label (should appear in High-Severity)
    num = create_issue(
        "[TEST] S1: Login completely broken",
        "Blocker bug with project label. Should appear in High-Severity S1.",
        [PROJECT_LABEL, "severity:S1", "type:bug", MARKER_LABEL],
    )
    if num:
        created["s1_tracked"].append(num)
        print(f"  #{num} S1 WITH project label")

    # S2 + project label
    num = create_issue(
        "[TEST] S2: Dashboard slow to load",
        "Major bug with project label. Should appear in High-Severity S2.",
        [PROJECT_LABEL, "severity:S2", "type:bug", MARKER_LABEL],
    )
    if num:
        created["s2_tracked"].append(num)
        print(f"  #{num} S2 WITH project label")

    # S1 WITHOUT project label (should NOT appear — tests the AND filter)
    num = create_issue(
        "[TEST] S1: Unrelated blocker (no project label)",
        "S1 bug but NOT tagged to this project. Should NOT appear in dashboard "
        "(tests that the AND filter works correctly).",
        ["severity:S1", "type:bug", MARKER_LABEL],
    )
    if num:
        created["s1_untracked"].append(num)
        print(f"  #{num} S1 WITHOUT project label (should be filtered out)")
    print("  Done.\n")

    # ── Step 6: Other-project issue (filter isolation test) ──
    print("Step 6: Creating other-project issue...")
    num = create_issue(
        "[TEST] Work for a different project",
        "Tagged project:other-demo + needs-pm. Should NOT appear in pilot-1 "
        "dashboard (belongs to another project).",
        ["project:other-demo", "needs-pm", MARKER_LABEL],
    )
    if num:
        created["other_project"].append(num)
        print(f"  #{num} project:other-demo + needs-pm (should be isolated)")
    print("  Done.\n")

    # ── Step 7: Unlabeled issue (for audit + nudge testing) ──
    print("Step 7: Creating unlabeled issue...")
    num = create_issue(
        "[TEST] Brand new unlabeled issue",
        "No project label. Should be caught by daily audit + nudged by the App.",
        [MARKER_LABEL],  # only the marker, no project label
    )
    if num:
        created["unlabeled"].append(num)
        print(f"  #{num} unlabeled (audit + nudge target)")
    print("  Done.\n")

    # ── Print expected dashboard state ──
    print("=" * 70)
    print("SEEDING COMPLETE — EXPECTED DASHBOARD STATE")
    print("=" * 70)
    print(f"""
After running the Dashboard update workflow on your PMO repo, verify:

🔧 ENGINEERING ACTIVITY (cross-repo rollup)
  - {ENG_REPO}: Open w/ {PROJECT_LABEL} = {len(created['tracked_open']) + len(created['needs_pm_tracked']) + len(created['s1_tracked']) + len(created['s2_tracked'])}
    (3 features + 1 carryover + 1 needs-pm + 1 S1 + 1 S2 = 7 open tracked)
  - Closed last 7d w/ {PROJECT_LABEL} = {len(created['tracked_closed'])} (2 completed tasks)

⚠️ NEEDS PM ATTENTION
  - "Tagged with {PROJECT_LABEL}" subsection: 1 issue (#{created['needs_pm_tracked'][0] if created['needs_pm_tracked'] else '?'})
  - "Has needs-pm but missing project label" subsection: 1 issue (#{created['needs_pm_untagged'][0] if created['needs_pm_untagged'] else '?'})
  - Should NOT show #{created['other_project'][0] if created['other_project'] else '?'} (belongs to other project)

🐛 HIGH-SEVERITY BUGS
  - S1: 1 issue (#{created['s1_tracked'][0] if created['s1_tracked'] else '?'}) — login broken
  - S2: 1 issue (#{created['s2_tracked'][0] if created['s2_tracked'] else '?'}) — dashboard slow
  - Should NOT show #{created['s1_untracked'][0] if created['s1_untracked'] else '?'} (S1 but no project label)

🏃 ENGINEERING SPRINT STATUS
  - Sprint 5: ~2/5 closed (40%), due in 10 days
  - Sprint 4: 0/1 or with carryover, due 3 days ago ⚠️ overdue
  - Sprint 6: empty, due in 25 days

✅ RECENTLY CLOSED (last 7 days)
  - 2 issues (#{', #'.join(str(n) for n in created['tracked_closed']) if created['tracked_closed'] else '?'})

🔍 DAILY AUDIT (run the audit workflow separately)
  - Should list #{created['unlabeled'][0] if created['unlabeled'] else '?'} (unlabeled)
  - May also list #{created['needs_pm_untagged'][0] if created['needs_pm_untagged'] else '?'} (needs-pm but no project label)

🤖 GITHUB APP NUDGE (run polling separately)
  - Should nudge #{created['unlabeled'][0] if created['unlabeled'] else '?'} (unlabeled, gets a comment + needs-project-label)

CLEANUP:
  To remove all test data, go to {ENG_REPO} issues, filter by label
  `{MARKER_LABEL}`, and bulk-close or delete them. Also delete the 3 milestones
  (Sprint 4, 5, 6) from the Milestones tab.
""")


if __name__ == "__main__":
    main()
