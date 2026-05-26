#!/usr/bin/env python3
"""
daily_snapshot.py
-----------------
Captures today's dashboard state into status-history/YYYY-MM-DD.md.
Runs nightly at 02:00 UTC.

Each file is a frozen-in-time snapshot you can scroll back through to see
how the project looked on any given day.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse logic from update_dashboard
sys.path.insert(0, os.path.dirname(__file__))
from update_dashboard import (
    get_all_issues, get_all_milestones, get_project_data,
    get_phase_parents_with_subissues, find_current_phase,
    build_health_section, build_currently_in_section,
    build_status_overview_v2, build_phase_progress_section,
    build_invoice_section, build_risks_section, build_delays_section,
)


def main():
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    issues = get_all_issues()
    milestones = get_all_milestones()

    try:
        project = get_project_data()
    except Exception as exc:
        print(f"Could not read project board: {exc}")
        project = None

    phase_data = get_phase_parents_with_subissues()
    current_phase = find_current_phase(phase_data)

    body = f"# Daily Snapshot — {date_str}\n\n"
    body += f"_Captured at {now.strftime('%Y-%m-%d %H:%M UTC')}_\n\n"
    body += build_health_section(phase_data, milestones, project, issues) + "\n"
    body += build_currently_in_section(phase_data, current_phase) + "\n"
    body += build_status_overview_v2(project, phase_data) + "\n"
    body += build_phase_progress_section(phase_data, project) + "\n"
    body += build_invoice_section(milestones) + "\n"
    body += build_risks_section(issues) + "\n"
    body += build_delays_section(project) + "\n"

    out_dir = Path("status-history")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{date_str}.md"
    out_path.write_text(body)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
