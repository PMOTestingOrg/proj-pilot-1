#!/usr/bin/env python3
"""
refresh_cross_repo.py
─────────────────────
Polls every linked code repo for issues tagged with this project's label,
plus surrounding metadata, and produces dashboard sections.

The actual dashboard issue update is done by update_dashboard.py which
calls this module to get the cross-repo content.

Sections produced (each conditional on `surfacing.*` flags in config):
  - Engineering Activity Rollup
  - Needs PM Attention queue
  - High-Severity Bugs (S1/S2)
  - Sprint Status (open milestones per repo)
  - Recent Closures (last 7 days)
  - Invoice Trigger Status (cross-repo milestone state)

Designed to be imported by update_dashboard.py — main() also runs standalone
for debugging.

Env vars:
  APP_TOKEN     — GitHub App installation token (preferred; 15K/hr limit)
                  Falls back to PROJECTS_TOKEN if not set (5K/hr limit)
  REPO          — owner/repo of this PMO project repo
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_TOKEN = (os.environ.get("APP_TOKEN", "").strip()
             or os.environ.get("PROJECTS_TOKEN", "").strip())
REPO = os.environ.get("REPO", "")
GH_API = "https://api.github.com"


def graphql(query, variables=None):
    if not APP_TOKEN:
        raise RuntimeError("APP_TOKEN or PROJECTS_TOKEN required for cross-repo polling.")
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body, method="POST",
        headers={"Authorization": f"Bearer {APP_TOKEN}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode()
        raise RuntimeError(f"GraphQL HTTP {exc.code}: {body_text[:500]}")
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data.get("data", {})


def rest(method, path):
    req = urllib.request.Request(
        f"{GH_API}{path}", method=method,
        headers={"Authorization": f"Bearer {APP_TOKEN}",
                 "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return {"_error_code": exc.code, "_error_body": exc.read().decode()}


def load_config():
    try:
        import yaml
        return yaml.safe_load(Path("project-config.yml").read_text())
    except FileNotFoundError:
        return None


def get_project_label(config):
    slug = (config.get("project") or {}).get("slug", "")
    if not slug or "REPLACE-ME" in slug.upper():
        return None
    return f"project:{slug}"


def query_repo_issues(owner_repo, project_label, additional_labels=None,
                      states=("OPEN",), limit=50):
    """Query issues in a repo by label(s) + state. Returns list of issues.

    Important: GitHub's GraphQL `labels:` filter has OR semantics, not AND.
    To require ALL labels, we query only by project_label and filter
    additional_labels client-side.
    """
    owner, name = owner_repo.split("/", 1)
    state_filter = list(states)

    # Fetch enough to allow client-side filtering for additional labels
    fetch_limit = limit if not additional_labels else max(100, limit * 5)

    query = """
    query($owner: String!, $name: String!, $labels: [String!]!, $states: [IssueState!]!, $limit: Int!) {
      repository(owner: $owner, name: $name) {
        issues(first: $limit, labels: $labels, states: $states,
               orderBy: {field: UPDATED_AT, direction: DESC}) {
          totalCount
          nodes {
            number title state url
            createdAt updatedAt closedAt
            author { login }
            assignees(first: 5) { nodes { login } }
            labels(first: 20) { nodes { name color } }
          }
        }
      }
    }
    """
    try:
        result = graphql(query, {
            "owner": owner, "name": name,
            "labels": [project_label],  # query by project label only
            "states": state_filter,
            "limit": fetch_limit,
        })
        repo = result.get("repository") or {}
        issues_data = repo.get("issues") or {}
        items = issues_data.get("nodes") or []

        # Client-side AND filter for additional required labels
        if additional_labels:
            required = set(additional_labels)
            filtered = []
            for issue in items:
                issue_labels = {l["name"] for l in (issue.get("labels") or {}).get("nodes", [])}
                if required.issubset(issue_labels):
                    filtered.append(issue)
            items = filtered[:limit]
            total = len(items)
        else:
            total = issues_data.get("totalCount", 0)
            items = items[:limit]

        return {
            "total": total,
            "items": items,
            "error": None,
        }
    except Exception as exc:
        return {"total": 0, "items": [], "error": str(exc)}


def query_repo_milestones(owner_repo):
    """Open milestones in a repo with progress."""
    result = rest("GET", f"/repos/{owner_repo}/milestones?state=open&per_page=20")
    if isinstance(result, dict) and "_error_code" in result:
        return {"items": [], "error": f"HTTP {result['_error_code']}"}
    return {"items": result or [], "error": None}


def query_milestone_by_name(owner_repo, milestone_name):
    """Find a specific milestone by name across open and closed."""
    for state in ("open", "closed"):
        page = 1
        while page <= 5:
            result = rest("GET", f"/repos/{owner_repo}/milestones?state={state}&per_page=100&page={page}")
            if isinstance(result, dict) and "_error_code" in result:
                return None
            if not result:
                break
            for m in result:
                if m["title"] == milestone_name:
                    return m
            page += 1
    return None


# ─── Section builders ───────────────────────────────────────────
def time_ago(iso_str):
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt
        if delta.days > 0:
            return f"{delta.days}d ago"
        hours = delta.seconds // 3600
        if hours > 0:
            return f"{hours}h ago"
        minutes = (delta.seconds % 3600) // 60
        return f"{max(1, minutes)}m ago"
    except Exception:
        return iso_str[:10]


def build_engineering_rollup_section(config, project_label):
    """Per-repo summary table: open issues, closed last 7 days, last activity."""
    linked = config.get("linked_repos") or []
    if not linked:
        return ""

    section = "## 🔧 Engineering Activity (cross-repo rollup)\n\n"
    section += f"_Counts include only issues labeled `{project_label}` — issues without the label aren't tracked._\n\n"
    section += f"| Repo | Open w/ `{project_label}` | Closed last 7d w/ `{project_label}` | Last activity |\n"
    section += "|---|---|---|---|\n"

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    for repo in linked:
        open_data = query_repo_issues(repo, project_label, states=("OPEN",), limit=100)
        closed_data = query_repo_issues(repo, project_label, states=("CLOSED",), limit=100)

        if open_data["error"]:
            section += f"| `{repo}` | _error: {open_data['error'][:40]}_ | — | — |\n"
            continue

        recent_closed = 0
        last_activity = None
        for issue in open_data["items"] + closed_data["items"]:
            updated = issue.get("updatedAt")
            if updated:
                if last_activity is None or updated > last_activity:
                    last_activity = updated
        for issue in closed_data["items"]:
            closed_at = issue.get("closedAt")
            if closed_at:
                try:
                    if datetime.fromisoformat(closed_at.replace("Z", "+00:00")) >= week_ago:
                        recent_closed += 1
                except Exception:
                    pass

        # Deep link to filtered view
        link = f"https://github.com/{repo}/issues?q=is%3Aissue+label%3A%22{project_label}%22"
        section += (f"| [`{repo}`]({link}) | {open_data['total']} | "
                    f"{recent_closed} | {time_ago(last_activity)} |\n")

    return section


def build_needs_pm_section(config, project_label):
    """Issues tagged needs-pm in linked repos.
    Surfaces ALL needs-pm issues, marking those missing the project label
    so PMs notice them even if engineering forgot the project tag.
    """
    linked = config.get("linked_repos") or []
    if not linked:
        return ""

    project_items = []   # has both needs-pm AND project:slug
    untagged_items = []  # has needs-pm but NOT project:slug

    for repo in linked:
        # Get ALL open issues with needs-pm in this repo (regardless of project label)
        owner, name = repo.split("/", 1)
        query = """
        query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            issues(first: 50, labels: ["needs-pm"], states: [OPEN],
                   orderBy: {field: UPDATED_AT, direction: DESC}) {
              nodes {
                number title state url createdAt updatedAt
                author { login }
                labels(first: 20) { nodes { name } }
              }
            }
          }
        }
        """
        try:
            result = graphql(query, {"owner": owner, "name": name})
            issues = (result.get("repository") or {}).get("issues", {}).get("nodes", [])
        except Exception as exc:
            print(f"  Error querying needs-pm in {repo}: {exc}")
            continue

        for issue in issues:
            issue_labels = {l["name"] for l in (issue.get("labels") or {}).get("nodes", [])}
            if project_label in issue_labels:
                project_items.append((repo, issue))
            else:
                # has needs-pm but not this project's label
                # only flag if no OTHER project label either (otherwise it's another project's concern)
                has_any_project_label = any(l.startswith("project:") and l != "project:none"
                                            for l in issue_labels)
                if not has_any_project_label:
                    untagged_items.append((repo, issue))

    section = "## ⚠️ Needs PM Attention\n\n"

    if not project_items and not untagged_items:
        section += f"_No engineering issues currently flagged `needs-pm` in linked repos. 🎉_\n"
        return section

    if project_items:
        section += f"### Tagged with `{project_label}` ({len(project_items)})\n\n"
        section += "| Repo | # | Title | Filed by | Age |\n|---|---|---|---|---|\n"
        for repo, issue in project_items[:20]:
            title = issue["title"].replace("|", "\\|")
            if len(title) > 60:
                title = title[:57] + "..."
            author = (issue.get("author") or {}).get("login", "?")
            section += (f"| `{repo}` | [#{issue['number']}]({issue['url']}) | "
                        f"{title} | @{author} | {time_ago(issue.get('createdAt'))} |\n")
        section += "\n"

    if untagged_items:
        section += f"### ⚠️ Has `needs-pm` but missing project label ({len(untagged_items)})\n\n"
        section += f"_These may belong to this project but engineering didn't apply a `project:*` label. Please review and label appropriately._\n\n"
        section += "| Repo | # | Title | Filed by | Age |\n|---|---|---|---|---|\n"
        for repo, issue in untagged_items[:20]:
            title = issue["title"].replace("|", "\\|")
            if len(title) > 60:
                title = title[:57] + "..."
            author = (issue.get("author") or {}).get("login", "?")
            section += (f"| `{repo}` | [#{issue['number']}]({issue['url']}) | "
                        f"{title} | @{author} | {time_ago(issue.get('createdAt'))} |\n")

    return section


def build_high_severity_bugs_section(config, project_label):
    """S1/S2 bugs across linked repos with project label."""
    linked = config.get("linked_repos") or []
    if not linked:
        return ""

    s1_items = []
    s2_items = []
    for repo in linked:
        for sev_label, bucket in (("severity:S1", s1_items), ("severity:S2", s2_items)):
            data = query_repo_issues(repo, project_label,
                                     additional_labels=[sev_label],
                                     states=("OPEN",), limit=10)
            for issue in data["items"]:
                bucket.append((repo, issue))

    section = "## 🐛 High-Severity Bugs\n\n"
    if not s1_items and not s2_items:
        section += "_No S1 or S2 bugs open. 🎉_\n"
        return section

    if s1_items:
        section += f"### 🔴 S1 — Blocker ({len(s1_items)})\n\n"
        section += "| Repo | # | Title | Age |\n|---|---|---|---|\n"
        for repo, issue in s1_items[:10]:
            title = issue["title"][:60].replace("|", "\\|")
            section += (f"| `{repo}` | [#{issue['number']}]({issue['url']}) | "
                        f"{title} | {time_ago(issue.get('createdAt'))} |\n")
        section += "\n"

    if s2_items:
        section += f"### 🟠 S2 — Major ({len(s2_items)})\n\n"
        section += "| Repo | # | Title | Age |\n|---|---|---|---|\n"
        for repo, issue in s2_items[:10]:
            title = issue["title"][:60].replace("|", "\\|")
            section += (f"| `{repo}` | [#{issue['number']}]({issue['url']}) | "
                        f"{title} | {time_ago(issue.get('createdAt'))} |\n")

    return section


def build_sprint_status_section(config):
    """Open milestones per linked repo (engineering's own milestones)."""
    linked = config.get("linked_repos") or []
    if not linked:
        return ""

    section = "## 🏃 Engineering Sprint Status\n\n"
    section += "_Open milestones in linked repos with progress:_\n\n"
    section += "| Repo | Milestone | Progress | Due |\n"
    section += "|---|---|---|---|\n"

    any_found = False
    today = datetime.now(timezone.utc).date()
    for repo in linked:
        ms_data = query_repo_milestones(repo)
        for m in ms_data["items"][:5]:
            any_found = True
            title = m["title"]
            open_c = m.get("open_issues", 0)
            closed_c = m.get("closed_issues", 0)
            total = open_c + closed_c
            if total:
                pct = round(100 * closed_c / total)
                progress = f"{closed_c}/{total} ({pct}%)"
            else:
                progress = "_empty_"
            due = m.get("due_on")
            if due:
                try:
                    due_d = datetime.fromisoformat(due.replace("Z", "+00:00")).date()
                    days = (due_d - today).days
                    due_str = due_d.isoformat()
                    if days < 0:
                        due_str = f"{due_str} ⚠️ {-days}d overdue"
                    elif days < 7:
                        due_str = f"{due_str} 🟡 in {days}d"
                except Exception:
                    due_str = due[:10]
            else:
                due_str = "—"
            section += f"| `{repo}` | [{title}]({m['html_url']}) | {progress} | {due_str} |\n"

    if not any_found:
        section = "## 🏃 Engineering Sprint Status\n\n_No open milestones in any linked repo._\n"
    return section


def build_recent_closures_section(config, project_label):
    """Issues closed in last 7 days with project label."""
    linked = config.get("linked_repos") or []
    if not linked:
        return ""

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    items = []
    for repo in linked:
        data = query_repo_issues(repo, project_label, states=("CLOSED",), limit=30)
        for issue in data["items"]:
            closed_at = issue.get("closedAt")
            if not closed_at:
                continue
            try:
                if datetime.fromisoformat(closed_at.replace("Z", "+00:00")) >= week_ago:
                    items.append((repo, issue, closed_at))
            except Exception:
                continue

    section = "## ✅ Recently Closed (last 7 days)\n\n"
    if not items:
        section += "_No engineering work closed in the past week._\n"
        return section

    items.sort(key=lambda x: x[2], reverse=True)
    section += f"_{len(items)} closure(s) in linked code repos:_\n\n"
    section += "| Repo | # | Title | Closed |\n|---|---|---|---|\n"
    for repo, issue, closed_at in items[:15]:
        title = issue["title"][:60].replace("|", "\\|")
        section += (f"| `{repo}` | [#{issue['number']}]({issue['url']}) | "
                    f"{title} | {time_ago(closed_at)} |\n")
    return section


def build_invoice_triggers_section(config):
    """Status of invoice triggers — pmo_milestone vs code_milestone."""
    triggers = config.get("invoice_triggers") or []
    if not triggers:
        return ""

    section = "## 💰 Invoice Trigger Status\n\n"
    section += "| Invoice | Trigger Type | Status |\n|---|---|---|\n"

    for t in triggers:
        milestone_name = t.get("milestone", "_unnamed_")
        trigger = t.get("trigger") or {}
        ttype = trigger.get("type", "?")

        if ttype == "pmo_milestone":
            status = "Tracked by PMO milestone (see Invoice section above)"
            type_label = "PMO milestone"
        elif ttype == "code_milestone":
            repos = trigger.get("repos") or []
            target_name = trigger.get("milestone_name", "")
            type_label = f"Code milestone: `{target_name}`"
            # Check each repo
            results = []
            all_closed = bool(repos)
            for repo in repos:
                m = query_milestone_by_name(repo, target_name)
                if m is None:
                    results.append(f"`{repo}`: missing")
                    all_closed = False
                elif m["state"] == "closed":
                    results.append(f"`{repo}`: ✅")
                else:
                    open_c = m.get("open_issues", 0)
                    closed_c = m.get("closed_issues", 0)
                    total = open_c + closed_c
                    pct = round(100 * closed_c / total) if total else 0
                    results.append(f"`{repo}`: {closed_c}/{total} ({pct}%)")
                    all_closed = False
            status_prefix = "✅ Ready to invoice — " if all_closed else "⏳ "
            status = status_prefix + " · ".join(results)
        else:
            type_label = f"Unknown: {ttype}"
            status = "(invalid trigger type)"

        section += f"| {milestone_name} | {type_label} | {status} |\n"

    return section


# ─── Top-level function called by update_dashboard.py ──────────
def build_cross_repo_sections():
    """Returns dict: section_name -> markdown_string. Empty dict if no config."""
    config = load_config()
    if not config:
        return {}

    project_label = get_project_label(config)
    if not project_label:
        return {}

    surfacing = config.get("surfacing") or {}
    sections = {}

    try:
        if surfacing.get("show_engineering_rollup", True):
            sections["engineering_rollup"] = build_engineering_rollup_section(config, project_label)
        if surfacing.get("show_needs_pm_queue", True):
            sections["needs_pm"] = build_needs_pm_section(config, project_label)
        if surfacing.get("show_high_severity_bugs", True):
            sections["high_severity_bugs"] = build_high_severity_bugs_section(config, project_label)
        if surfacing.get("show_sprint_status", True):
            sections["sprint_status"] = build_sprint_status_section(config)
        if surfacing.get("show_recent_closures", True):
            sections["recent_closures"] = build_recent_closures_section(config, project_label)
        # Invoice triggers always shown if defined
        if config.get("invoice_triggers"):
            sections["invoice_triggers"] = build_invoice_triggers_section(config)
    except Exception as exc:
        sections["error"] = (f"## ⚠️ Cross-repo aggregation error\n\n"
                             f"```\n{exc}\n```\n"
                             f"_Check that APP_TOKEN is valid and linked_repos exist._\n")

    return sections


def main():
    """Standalone debugging — prints all sections to stdout."""
    sections = build_cross_repo_sections()
    if not sections:
        print("No cross-repo sections generated (no config or no project label).")
        return
    for name, content in sections.items():
        print(f"\n{'=' * 70}\n# Section: {name}\n{'=' * 70}\n")
        print(content)


if __name__ == "__main__":
    main()
