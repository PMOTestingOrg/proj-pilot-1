#!/usr/bin/env python3
"""
weekly_summary.py
-----------------
Appends one row per week to status-history/timeline.md.
Each row: date, overall health, phase progress %, open risks, blocked count, delays.

The file becomes a project EKG you can scroll through week-by-week.

Runs every Monday at 09:00 UTC.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from update_dashboard import (
    get_all_issues, get_all_milestones, get_project_data,
    get_phase_parents_with_subissues, find_current_phase,
    has_label, extract_fields, PHASES,
)


def compute_phase_progress(phase_data):
    """Return list of (phase_name, done, total) for compact display."""
    out = []
    for phase in PHASES:
        if phase not in phase_data:
            out.append((phase, 0, 0))
            continue
        subs = phase_data[phase]["subs"]
        done = sum(1 for s in subs if s["state"] == "closed")
        total = len(subs)
        out.append((phase, done, total))
    return out


def compute_overall_health(project, milestones, issues):
    risks_open = sum(1 for i in issues if has_label(i, "type:risk") and i["state"] == "open")
    today = datetime.now(timezone.utc).date()
    delays = 0
    blocked = 0
    if project:
        for item in project["items"]["nodes"]:
            if not item.get("content"):
                continue
            fields = extract_fields(item)
            if "Blocked" in fields.get("Status", ""):
                blocked += 1
            planned_end = fields.get("Planned End")
            if planned_end and not fields.get("Actual End"):
                try:
                    pe = datetime.fromisoformat(planned_end).date()
                    if today > pe and item["content"]["state"] != "closed":
                        delays += 1
                except Exception:
                    pass
    overdue_invoices = 0
    for m in milestones:
        if m["state"] == "open" and m.get("due_on"):
            try:
                due = datetime.fromisoformat(m["due_on"].replace("Z", "+00:00")).date()
                if today > due:
                    overdue_invoices += 1
            except Exception:
                pass
    if blocked > 0 or delays > 3 or overdue_invoices > 0:
        return "Red", risks_open, blocked, delays, overdue_invoices
    if delays > 0 or risks_open > 0:
        return "Yellow", risks_open, blocked, delays, overdue_invoices
    return "Green", risks_open, blocked, delays, overdue_invoices


def main():
    now = datetime.now(timezone.utc)
    week_str = now.strftime("%Y-W%U")  # e.g., 2026-W23
    date_str = now.strftime("%Y-%m-%d")

    issues = get_all_issues()
    milestones = get_all_milestones()
    try:
        project = get_project_data()
    except Exception:
        project = None
    phase_data = get_phase_parents_with_subissues()
    current = find_current_phase(phase_data) or "—"

    health, risks, blocked, delays, overdue = compute_overall_health(project, milestones, issues)
    phase_progress = compute_phase_progress(phase_data)

    # Compact progress string: P1:2/4 P2:0/5 ...
    pp_compact = " ".join(
        f"P{i+1}:{d}/{t}" for i, (_, d, t) in enumerate(phase_progress)
    )

    out_dir = Path("status-history")
    out_dir.mkdir(exist_ok=True)
    timeline = out_dir / "timeline.md"

    # Build header if file doesn't exist
    if not timeline.exists():
        header = "# Project Timeline\n\n"
        header += "Weekly snapshot of overall project health. New row appended every Monday.\n\n"
        header += "| Week | Date | Health | Currently In | Phase Progress | Risks | Blocked | Delays | Overdue Inv |\n"
        header += "|---|---|---|---|---|---|---|---|---|\n"
        timeline.write_text(header)

    # Determine emoji for health
    health_emoji = {"Green": "🟢", "Yellow": "🟡", "Red": "🔴"}[health]

    # Append the new row
    row = f"| {week_str} | {date_str} | {health_emoji} {health} | {current} | {pp_compact} | {risks} | {blocked} | {delays} | {overdue} |\n"
    with timeline.open("a") as f:
        f.write(row)

    print(f"Appended row to {timeline}: {row.strip()}")


if __name__ == "__main__":
    main()
