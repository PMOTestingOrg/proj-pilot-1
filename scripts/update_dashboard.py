#!/usr/bin/env python3
"""
update_dashboard.py
-------------------
Updates the Project Dashboard issue with:
  - Status Overview (totals by status)
  - Currently In: <active phase>
  - Phase Progress (from native sub-issues)
  - Invoice Milestones (from GitHub Milestones)
  - Open Risks
  - Delays

Also manages the "active phase pin" - pins the currently active phase parent issue
alongside the dashboard, and unpins others.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

GH_TOKEN = os.environ["GH_TOKEN"]
PROJECTS_TOKEN = os.environ.get("PROJECTS_TOKEN", "").strip()
REPO = os.environ["REPO"]
GH_API = "https://api.github.com"

# Exact title of the dashboard issue created by the seed script
DASHBOARD_TITLE = "Project Dashboard"

PHASES = [
    "1. Initiation & Planning",
    "2. Requirement Gathering",
    "3. Development",
    "4. UAT",
    "5. Production Deployment",
    "6. Closure",
]


def rest(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{GH_API}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {GH_TOKEN}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        text = resp.read()
        return json.loads(text) if text else {}


def graphql(query, variables=None):
    token = PROJECTS_TOKEN if PROJECTS_TOKEN else GH_TOKEN
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "GraphQL-Features": "sub_issues"},
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    if "errors" in data:
        raise RuntimeError(f"GraphQL: {data['errors']}")
    return data["data"]


def get_all_issues():
    out, page = [], 1
    while True:
        batch = rest("GET", f"/repos/{REPO}/issues?state=all&per_page=100&page={page}")
        if not batch:
            break
        out.extend([i for i in batch if "pull_request" not in i])
        page += 1
        if page > 20:
            break
    return out


def get_all_milestones():
    return rest("GET", f"/repos/{REPO}/milestones?state=all&per_page=100")


def has_label(issue, name):
    return any(l["name"] == name for l in issue.get("labels", []))


def find_dashboard(issues):
    """Find the dashboard issue by EXACT title + auto-managed label.
    Fall back to label-only match if title was edited.
    """
    # Primary: exact title match by github-actions bot
    for i in issues:
        if (i["title"] == DASHBOARD_TITLE
                and i.get("user", {}).get("login") == "github-actions[bot]"):
            return i
    # Fallback: title match with auto-managed label
    for i in issues:
        if i["title"] == DASHBOARD_TITLE and has_label(i, "auto-managed"):
            return i
    return None


def get_project_data():
    if not PROJECTS_TOKEN:
        return None
    owner, name = REPO.split("/")
    query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        projectsV2(first: 5) {
          nodes {
            id title url
            items(first: 100) {
              nodes {
                id
                content {
                  ... on Issue {
                    number title state
                    labels(first: 20) { nodes { name } }
                  }
                }
                fieldValues(first: 30) {
                  nodes {
                    ... on ProjectV2ItemFieldDateValue {
                      date field { ... on ProjectV2Field { name } }
                    }
                    ... on ProjectV2ItemFieldSingleSelectValue {
                      name field { ... on ProjectV2SingleSelectField { name } }
                    }
                    ... on ProjectV2ItemFieldNumberValue {
                      number field { ... on ProjectV2Field { name } }
                    }
                    ... on ProjectV2ItemFieldIterationValue {
                      title field { ... on ProjectV2IterationField { name } }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    result = graphql(query, {"owner": owner, "name": name})
    projects = result["repository"]["projectsV2"]["nodes"]
    for p in projects:
        if "Tracker" in (p.get("title") or ""):
            return p
    return projects[0] if projects else None


def extract_fields(item):
    out = {}
    for fv in item.get("fieldValues", {}).get("nodes", []) or []:
        if not fv:
            continue
        field_name = (fv.get("field") or {}).get("name")
        if not field_name:
            continue
        if "date" in fv:
            out[field_name] = fv["date"]
        elif "name" in fv:
            out[field_name] = fv["name"]
        elif "number" in fv:
            out[field_name] = fv["number"]
        elif "title" in fv:
            out[field_name] = fv["title"]
    return out


def get_phase_parents_with_subissues():
    """Use GraphQL to get phase parent issues with their native sub-issues.
    Returns dict: phase_name -> {parent_issue, sub_issues: [{number, state, title}]}

    Note: GraphQL Issue.state returns enum 'OPEN'/'CLOSED' (uppercase). The rest
    of the codebase uses lowercase 'open'/'closed' (REST convention). We normalize
    here so all downstream comparisons work consistently.
    """
    owner, name = REPO.split("/")
    query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        issues(first: 100, labels: ["type:phase"], states: [OPEN, CLOSED]) {
          nodes {
            number title state
            subIssues(first: 50) {
              nodes { number title state }
            }
          }
        }
      }
    }
    """
    try:
        result = graphql(query, {"owner": owner, "name": name})
        out = {}
        for parent in result["repository"]["issues"]["nodes"]:
            title = parent["title"]
            # Normalize parent state to lowercase
            parent["state"] = (parent.get("state") or "").lower()
            # Normalize sub-issue states to lowercase too
            for sub in parent["subIssues"]["nodes"]:
                sub["state"] = (sub.get("state") or "").lower()
            # Match the canonical phase name
            for phase in PHASES:
                if title.startswith(phase) or title == phase:
                    out[phase] = {
                        "parent": parent,
                        "subs": parent["subIssues"]["nodes"],
                    }
                    break
        return out
    except Exception as exc:
        print(f"Warning: could not query sub-issues: {exc}")
        return {}


def get_phase_statuses_from_board(project, phase_data):
    """Read each phase parent's Status and % Complete from the board.
    Returns dict: phase_name -> {status, pct_complete}.
    """
    if not project:
        return {}
    # Build issue_number -> phase_name map from phase_data parents
    parent_num_to_phase = {}
    for phase, data in phase_data.items():
        parent_num_to_phase[data["parent"]["number"]] = phase

    out = {}
    for item in project["items"]["nodes"]:
        content = item.get("content") or {}
        num = content.get("number")
        if num not in parent_num_to_phase:
            continue
        fields = extract_fields(item)
        phase = parent_num_to_phase[num]
        out[phase] = {
            "status": fields.get("Status", ""),
            "pct_complete": fields.get("% Complete"),
        }
    return out


def find_current_phases(phase_data, phase_statuses):
    """Find ALL phases currently In Progress (or all-but-Done open phases).
    Returns list of phase names in PHASES order.
    """
    in_progress = []
    for phase in PHASES:
        if phase not in phase_data:
            continue
        parent = phase_data[phase]["parent"]
        # Closed parent = phase done
        if parent["state"] == "closed":
            continue
        # Read board status — if explicitly marked "In Progress", include
        status = (phase_statuses.get(phase) or {}).get("status", "")
        if "In Progress" in status or "in progress" in status.lower():
            in_progress.append(phase)
    # Fallback: if board statuses aren't set, fall back to the earliest open phase
    if not in_progress:
        for phase in PHASES:
            if phase not in phase_data:
                continue
            if phase_data[phase]["parent"]["state"] != "closed":
                return [phase]
        return []
    return in_progress


def find_current_phase(phase_data):
    """Legacy single-phase API (kept for pin manager). Returns first in-progress phase."""
    for phase in PHASES:
        if phase not in phase_data:
            continue
        parent = phase_data[phase]["parent"]
        subs = phase_data[phase]["subs"]
        if parent["state"] == "closed":
            continue  # phase done
        # If parent is open, this is current
        return phase
    return None


# Section builders
def build_status_overview(phase_data, issues):
    """Single table showing totals across the project."""
    not_started = 0
    in_progress = 0
    blocked = 0
    done = 0

    # Count from sub-issues + parents
    all_tracked_issues = []
    for phase in phase_data.values():
        all_tracked_issues.append(phase["parent"])
        all_tracked_issues.extend(phase["subs"])

    for it in all_tracked_issues:
        if it["state"] == "closed":
            done += 1
        else:
            # Open issue - check if it has a status hint from labels
            # For now, treat all open as "not started" unless we can detect otherwise
            not_started += 1

    # Check status labels (board status is more reliable, but we need a fallback)
    # We rely on board data for in_progress/blocked

    section = "## Status Overview\n\n"
    section += "| Status | Count |\n|---|---|\n"
    section += f"| Not Started | {not_started} |\n"
    section += f"| In Progress | {in_progress} |\n"
    section += f"| Blocked | {blocked} |\n"
    section += f"| Done | {done} |\n"
    total = not_started + in_progress + blocked + done
    section += f"| **Total** | **{total}** |\n"
    return section


def build_status_overview_v2(project, phase_data):
    """Better version using board Status field."""
    counts = {"Not Started": 0, "In Progress": 0, "Blocked": 0, "Done": 0, "Other": 0}

    # Get all parent + sub issue numbers we care about
    tracked_numbers = set()
    for phase in phase_data.values():
        tracked_numbers.add(phase["parent"]["number"])
        for sub in phase["subs"]:
            tracked_numbers.add(sub["number"])

    if project:
        for item in project["items"]["nodes"]:
            content = item.get("content")
            if not content or content.get("number") not in tracked_numbers:
                continue
            fields = extract_fields(item)
            status = fields.get("Status", "")
            if content["state"] == "closed" or status == "Done":
                counts["Done"] += 1
            elif "Blocked" in status:
                counts["Blocked"] += 1
            elif "In Progress" in status or "In progress" in status:
                counts["In Progress"] += 1
            elif "Todo" in status or "" == status:
                counts["Not Started"] += 1
            else:
                counts["Other"] += 1
    else:
        # No board data - count from issue state only
        for num in tracked_numbers:
            # We don't have state info without project - rough fallback
            pass

    total = sum(counts.values())
    section = "## Status Overview\n\n"
    section += "| Status | Count |\n|---|---|\n"
    section += f"| ⏳ Not Started | {counts['Not Started']} |\n"
    section += f"| 🔄 In Progress | {counts['In Progress']} |\n"
    section += f"| 🚫 Blocked | {counts['Blocked']} |\n"
    section += f"| ✅ Done | {counts['Done']} |\n"
    if counts['Other']:
        section += f"| Other | {counts['Other']} |\n"
    section += f"| **Total** | **{total}** |\n"
    return section


def build_currently_in_section(phase_data, current_phases, phase_statuses):
    """Show all in-progress phases. current_phases is a list."""
    section = "## 📍 Currently In\n\n"
    if not current_phases:
        section += "_All phases complete — project ready for closeout._\n"
        return section

    for phase in current_phases:
        parent = phase_data[phase]["parent"]
        subs = phase_data[phase]["subs"]
        done = sum(1 for s in subs if s["state"] == "closed")
        total = len(subs)

        # Pull % Complete from board if available
        board_pct = (phase_statuses.get(phase) or {}).get("pct_complete")
        board_status = (phase_statuses.get(phase) or {}).get("status", "")

        section += (f"**{phase}** — [#{parent['number']}]"
                    f"(https://github.com/{REPO}/issues/{parent['number']})\n")
        if board_status:
            section += f"- Status: {board_status}\n"
        if board_pct is not None:
            section += f"- % Complete (from board): {board_pct}%\n"
        if total > 0:
            sub_pct = round(100 * done / total)
            section += f"- Sub-tasks: {done}/{total} complete ({sub_pct}%)\n"
        else:
            section += "- No sub-tasks (work tracked via board fields)\n"
        section += "\n"
    return section


def build_phase_progress_section(phase_data, project, phase_statuses):
    section = "## 📊 Phase Progress\n\n"
    section += "| Phase | Status | Progress | Sub-issues | Earliest Planned End |\n"
    section += "|---|---|---|---|---|\n"
    today = datetime.now(timezone.utc).date()

    # Get earliest planned-end per phase from board (open work only)
    phase_earliest_end = {}
    if project:
        for item in project["items"]["nodes"]:
            content = item.get("content")
            if not content:
                continue
            fields = extract_fields(item)
            phase_name = fields.get("Phase")
            planned_end = fields.get("Planned End")
            actual_end = fields.get("Actual End")
            if not phase_name or not planned_end or actual_end:
                continue
            if content["state"] == "closed":
                continue
            if phase_name not in phase_earliest_end or planned_end < phase_earliest_end[phase_name]:
                phase_earliest_end[phase_name] = planned_end

    for phase in PHASES:
        if phase not in phase_data:
            section += f"| {phase} | _missing_ | — | — | — |\n"
            continue
        parent = phase_data[phase]["parent"]
        subs = phase_data[phase]["subs"]
        total = len(subs)
        done = sum(1 for s in subs if s["state"] == "closed")

        # Get board fields
        board_data = phase_statuses.get(phase) or {}
        board_status = board_data.get("status", "")
        board_pct = board_data.get("pct_complete")

        # Status: prefer board status when set, else infer from issue state
        if parent["state"] == "closed":
            status_icon = "✅ Done"
        elif board_status:
            # Use board status when set
            if "Done" in board_status:
                status_icon = "✅ " + board_status
            elif "In Progress" in board_status:
                status_icon = "🔄 " + board_status
            elif "Blocked" in board_status:
                status_icon = "🚫 " + board_status
            elif "Not Started" in board_status:
                status_icon = "⏳ " + board_status
            else:
                status_icon = board_status
        elif total == 0:
            status_icon = "⏳ Empty"
        elif done == total:
            status_icon = "✅ Ready to close"
        elif done > 0:
            status_icon = "🔄 In Progress"
        else:
            status_icon = "⏳ Not Started"

        # Progress: prefer board % Complete when set, else sub-issue ratio
        if board_pct is not None:
            try:
                progress_str = f"{int(board_pct)}%"
            except (ValueError, TypeError):
                progress_str = str(board_pct)
        elif total > 0:
            pct = round(100 * done / total)
            progress_str = f"{pct}%"
        else:
            progress_str = "—"

        # Sub-issues column
        if total > 0:
            sub_str = f"{done}/{total}"
        else:
            sub_str = "—"

        end_str = phase_earliest_end.get(phase, "—")
        variance = ""
        if end_str != "—":
            try:
                pe = datetime.fromisoformat(end_str).date()
                if today > pe and parent["state"] != "closed":
                    variance = f" ⚠️ +{(today - pe).days}d"
            except Exception:
                pass

        section += f"| {phase} | {status_icon} | {progress_str} | {sub_str} | {end_str}{variance} |\n"
    return section


def build_invoice_section(milestones):
    section = "## 💰 Invoice Milestones\n\n"
    if not milestones:
        return section + "_No milestones yet. Create them on the Milestones tab._\n"
    today = datetime.now(timezone.utc).date()
    section += "| Milestone | Due | Progress | Status |\n|---|---|---|---|\n"
    for m in sorted(milestones, key=lambda x: (x.get("due_on") or "9999")):
        title = m["title"]
        open_c = m.get("open_issues", 0)
        closed_c = m.get("closed_issues", 0)
        total = open_c + closed_c
        pct = round(100 * closed_c / total) if total else 0
        progress = f"{closed_c}/{total} ({pct}%)" if total else "_no items_"
        due_str = "—"
        status = "⏳ Pending"
        if m.get("due_on"):
            try:
                due = datetime.fromisoformat(m["due_on"].replace("Z", "+00:00")).date()
                due_str = due.isoformat()
                days = (due - today).days
                if m["state"] == "closed":
                    status = "✅ Invoiced"
                elif pct == 100 and total > 0:
                    status = "✅ Ready to invoice"
                elif days < 0:
                    status = f"🔴 Overdue ({-days}d)"
                elif days < 14:
                    status = f"🟡 Due in {days}d"
                else:
                    status = f"⏳ {days}d remaining"
            except Exception:
                pass
        elif m["state"] == "closed":
            status = "✅ Invoiced"
        section += f"| [{title}]({m['html_url']}) | {due_str} | {progress} | {status} |\n"
    return section


def build_risks_section(issues):
    risks = [i for i in issues if has_label(i, "type:risk") and i["state"] == "open"]
    if not risks:
        return "## ⚠️ Open Risks\n\nNone. 🎉\n"
    section = "## ⚠️ Open Risks\n\n| # | Risk | Owner |\n|---|---|---|\n"
    for r in risks[:10]:
        owner = ", ".join(a["login"] for a in r.get("assignees", [])) or "_unassigned_"
        title = r["title"].replace("[RISK] ", "")
        section += f"| #{r['number']} | {title} | {owner} |\n"
    return section


def build_delays_section(project):
    section = "## 📅 Items Past Planned End\n\n"
    if not project:
        return section + "_Project board required._\n"
    today = datetime.now(timezone.utc).date()
    delays = []
    for item in project["items"]["nodes"]:
        content = item.get("content")
        if not content or content["state"] == "closed":
            continue
        fields = extract_fields(item)
        planned = fields.get("Planned End")
        if not planned or fields.get("Actual End"):
            continue
        try:
            pe = datetime.fromisoformat(planned).date()
            if today > pe:
                delays.append(((today - pe).days, content, planned))
        except Exception:
            continue
    if not delays:
        return section + "Nothing past planned end. 🎉\n"
    delays.sort(reverse=True)
    section += "| # | Item | Days Late | Planned End |\n|---|---|---|---|\n"
    for days, content, planned in delays[:15]:
        section += f"| #{content['number']} | {content['title']} | ⚠️ {days}d | {planned} |\n"
    return section


def build_health_section(phase_data, milestones, project, issues):
    """Compute overall health from delays, blockers, overdue invoices."""
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

    health = "🟢 Green — on track"
    if blocked > 0 or delays > 3 or overdue_invoices > 0:
        health = "🔴 Red — off track"
    elif delays > 0 or risks_open > 0:
        health = "🟡 Yellow — at risk"

    section = "## 🚦 Project Health\n\n"
    section += "| | |\n|---|---|\n"
    section += f"| **Overall** | {health} |\n"
    section += f"| **Open Risks** | {risks_open} |\n"
    section += f"| **Blocked Items** | {blocked} |\n"
    section += f"| **Items past planned end** | {delays} |\n"
    section += f"| **Overdue invoice milestones** | {overdue_invoices} |\n"
    return section


def manage_pins(dashboard_issue, phase_data, current_phase):
    """Pin the Dashboard + the currently active phase parent.
    Unpin any other pinned issues we created. Uses GraphQL.
    """
    try:
        # Get currently pinned issues
        owner, name = REPO.split("/")
        result = graphql(
            """
            query($owner: String!, $name: String!) {
              repository(owner: $owner, name: $name) {
                pinnedIssues(first: 10) {
                  nodes { issue { id number title } }
                }
              }
            }
            """,
            {"owner": owner, "name": name},
        )
        currently_pinned = {
            n["issue"]["number"]: n["issue"]["id"]
            for n in (result["repository"]["pinnedIssues"]["nodes"] or [])
        }

        # Desired pins
        desired = {dashboard_issue["number"]: dashboard_issue["node_id"]}
        if current_phase and current_phase in phase_data:
            parent_num = phase_data[current_phase]["parent"]["number"]
            # Need node_id of the parent
            parent_data = rest("GET", f"/repos/{REPO}/issues/{parent_num}")
            desired[parent_num] = parent_data["node_id"]

        # Pin missing
        for num, node_id in desired.items():
            if num not in currently_pinned:
                try:
                    graphql(
                        "mutation($id: ID!) { pinIssue(input: {issueId: $id}) { issue { id } } }",
                        {"id": node_id},
                    )
                    print(f"  Pinned #{num}")
                except Exception as exc:
                    print(f"  Could not pin #{num}: {exc}")

        # Unpin issues not in desired set
        for num, node_id in currently_pinned.items():
            if num not in desired:
                try:
                    graphql(
                        "mutation($id: ID!) { unpinIssue(input: {issueId: $id}) { issue { id } } }",
                        {"id": node_id},
                    )
                    print(f"  Unpinned #{num}")
                except Exception as exc:
                    print(f"  Could not unpin #{num}: {exc}")
    except Exception as exc:
        print(f"Pin management failed: {exc}")


def main():
    issues = get_all_issues()
    milestones = get_all_milestones()
    dashboard = find_dashboard(issues)

    if not dashboard:
        print(f"ERROR: No dashboard issue with title '{DASHBOARD_TITLE}' from github-actions[bot].")
        print("Run setup workflow first.")
        sys.exit(1)
    print(f"Found dashboard: #{dashboard['number']} '{dashboard['title']}'")

    try:
        project = get_project_data()
    except Exception as exc:
        print(f"Could not read project board: {exc}")
        project = None

    phase_data = get_phase_parents_with_subissues()
    # NEW: read board status + % Complete per phase parent
    phase_statuses = get_phase_statuses_from_board(project, phase_data)
    current_phases = find_current_phases(phase_data, phase_statuses)
    # Legacy single-phase value for pin manager
    current_phase = current_phases[0] if current_phases else None

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = "# 📈 Project Dashboard\n\n"
    body += f"> **Last refreshed:** {now}\n"
    body += "> _Auto-updates on every issue change. Don't edit manually._\n"
    if project:
        body += f"> 🗂️ [Open the Project Board]({project.get('url', '#')})\n"
    body += "\n"

    base = f"https://github.com/{REPO}/issues"
    parents_only_query = "is%3Aissue+is%3Aopen+-label%3A%22is%3Asub-task%22"
    body += "## 📂 Quick Views\n\n"
    body += f"- 🎯 [**Parents only** (cleanest Issues view — bookmark this!)]({base}?q={parents_only_query})\n"
    body += f"- 💰 [Invoice Milestones](https://github.com/{REPO}/milestones)\n"
    body += f"- ⚠️ [Open Risks]({base}?q=is%3Aissue+is%3Aopen+label%3Atype%3Arisk)\n"
    body += f"- 🐞 [Open Bugs]({base}?q=is%3Aissue+is%3Aopen+label%3Atype%3Abug)\n"
    body += f"- 🔄 [Change Requests]({base}?q=is%3Aissue+label%3Atype%3Achange-request)\n"
    body += f"- 📊 [Weekly Status Reports]({base}?q=is%3Aissue+label%3Atype%3Astatus-report)\n\n"

    body += build_health_section(phase_data, milestones, project, issues) + "\n"
    body += build_currently_in_section(phase_data, current_phases, phase_statuses) + "\n"
    body += build_status_overview_v2(project, phase_data) + "\n"
    body += build_phase_progress_section(phase_data, project, phase_statuses) + "\n"
    body += build_invoice_section(milestones) + "\n"

    # ─── Cross-repo sections (if project-config.yml is set up) ───
    # These pull from linked engineering code repos via the GitHub App token.
    # If config is absent or APP_TOKEN missing, these silently skip.
    try:
        from refresh_cross_repo import build_cross_repo_sections
        cross_sections = build_cross_repo_sections()
        # Preferred display order
        order = [
            "engineering_rollup",
            "needs_pm",
            "high_severity_bugs",
            "sprint_status",
            "recent_closures",
            "invoice_triggers",
            "error",
        ]
        for key in order:
            if key in cross_sections and cross_sections[key].strip():
                body += cross_sections[key] + "\n"
    except Exception as exc:
        print(f"Cross-repo aggregation skipped: {exc}")

    body += build_risks_section(issues) + "\n"
    body += build_delays_section(project) + "\n"

    # Update dashboard issue body
    rest("PATCH", f"/repos/{REPO}/issues/{dashboard['number']}", {"body": body})
    print(f"Dashboard #{dashboard['number']} updated.")

    # Manage pins
    print("\nManaging pins...")
    manage_pins(dashboard, phase_data, current_phase)


if __name__ == "__main__":
    main()
