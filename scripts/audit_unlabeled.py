#!/usr/bin/env python3
"""
audit_unlabeled.py
──────────────────
Daily audit: across all linked_repos, find open issues created in the
last 30 days that lack ANY `project:*` label. Report them as a PMO repo issue.

Logic:
  - Issue must be OPEN
  - Issue must be from the last 30 days
  - Issue must NOT have any label starting with "project:"
  - Issue is NOT a pull request (PRs come back from /issues — filter those)

If 0 unlabeled: close the existing audit report issue.
If N > 0 unlabeled: open or update an issue listing each.

Env vars:
  GH_TOKEN     — built-in workflow token (writes audit issue in this repo)
  APP_TOKEN    — reads from linked code repos (preferred)
  REPO         — owner/repo of this PMO project
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

GH_TOKEN = os.environ["GH_TOKEN"]
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip() or GH_TOKEN
REPO = os.environ["REPO"]
GH_API = "https://api.github.com"

AUDIT_ISSUE_TITLE = "🔍 Daily Audit — Unlabeled Engineering Issues"


def rest(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{GH_API}{path}", data=data, method=method,
        headers={
            "Authorization": f"Bearer {token or GH_TOKEN}",
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


def load_config():
    import yaml
    return yaml.safe_load(Path("project-config.yml").read_text())


def get_unlabeled_issues(repo, since_iso):
    """Open issues in repo updated since `since_iso` with no project:* label."""
    out = []
    page = 1
    while page <= 5:
        result = rest("GET",
                      f"/repos/{repo}/issues?state=open&since={since_iso}&per_page=100&page={page}",
                      token=APP_TOKEN)
        if isinstance(result, dict) and "_error_code" in result:
            return None  # access denied
        if not result:
            break
        for i in result:
            # Skip pull requests (the issues endpoint returns both)
            if "pull_request" in i:
                continue
            # Check for any project:* label
            label_names = [l["name"] for l in i.get("labels", [])]
            if any(l.startswith("project:") for l in label_names):
                continue
            out.append({
                "number": i["number"],
                "title": i["title"],
                "url": i["html_url"],
                "created_at": i["created_at"],
                "author": (i.get("user") or {}).get("login", "?"),
            })
        page += 1
    return out


def find_audit_issue():
    issues = rest("GET", f"/repos/{REPO}/issues?state=all&per_page=100")
    if isinstance(issues, dict) and "_error_code" in issues:
        return None
    for i in issues:
        if i.get("title") == AUDIT_ISSUE_TITLE:
            return i
    return None


def render_audit_body(per_repo_results, project_slug):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = sum(len(items) for items in per_repo_results.values())

    body = f"# Unlabeled Engineering Issues — {total} total\n\n"
    body += f"_Last checked: {today}_\n\n"
    body += "These are open issues in linked code repos that don't have any "
    body += "`project:*` label, so they're not surfacing in PMO tracking.\n\n"
    body += f"**To resolve:** apply `project:{project_slug}` (or `project:none` "
    body += "if not project work) to each issue.\n\n---\n\n"

    for repo, items in per_repo_results.items():
        if not items:
            continue
        body += f"## `{repo}` — {len(items)} unlabeled\n\n"
        body += "| # | Title | Filed by | Created |\n|---|---|---|---|\n"
        for it in items[:25]:
            title = it["title"][:70].replace("|", "\\|")
            try:
                dt = datetime.fromisoformat(it["created_at"].replace("Z", "+00:00"))
                days = (datetime.now(timezone.utc) - dt).days
                age = f"{days}d ago"
            except Exception:
                age = "—"
            body += f"| [#{it['number']}]({it['url']}) | {title} | @{it['author']} | {age} |\n"
        if len(items) > 25:
            body += f"\n_…and {len(items) - 25} more not shown._\n"
        body += "\n"

    body += "---\n\n"
    body += "_This issue is auto-managed. It updates daily at 02:00 UTC and "
    body += "auto-closes when the unlabeled count reaches 0._\n"
    return body


def main():
    config = load_config()
    project = config.get("project") or {}
    slug = project.get("slug", "")
    if not slug or "REPLACE-ME" in slug.upper():
        print("Skipping audit — project.slug not set.")
        return

    linked = config.get("linked_repos") or []
    if not linked:
        print("Skipping audit — no linked_repos configured.")
        return

    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    per_repo = {}
    inaccessible = []
    for repo in linked:
        items = get_unlabeled_issues(repo, since)
        if items is None:
            inaccessible.append(repo)
            continue
        per_repo[repo] = items
        print(f"{repo}: {len(items)} unlabeled")

    total = sum(len(v) for v in per_repo.values())
    existing = find_audit_issue()

    if total == 0 and not inaccessible:
        print("✓ All engineering issues are labeled. 🎉")
        if existing and existing["state"] == "open":
            rest("PATCH", f"/repos/{REPO}/issues/{existing['number']}",
                 body={"state": "closed"})
            print(f"Closed audit issue #{existing['number']}.")
        return

    body = render_audit_body(per_repo, slug)
    if inaccessible:
        body += "\n## ⚠️ Inaccessible repos\n\n"
        body += "Could not read from these linked repos (check permissions):\n"
        for r in inaccessible:
            body += f"- `{r}`\n"
        body += "\n"

    # Determine assignees
    assignees = []
    notifs = config.get("notifications") or {}
    assignees = notifs.get("audit_assignees") or []

    if existing:
        update_payload = {"body": body, "state": "open"}
        rest("PATCH", f"/repos/{REPO}/issues/{existing['number']}", body=update_payload)
        print(f"Updated audit issue #{existing['number']} (total: {total}).")
    else:
        payload = {"title": AUDIT_ISSUE_TITLE, "body": body,
                   "labels": ["auto-managed"]}
        if assignees:
            payload["assignees"] = assignees
        result = rest("POST", f"/repos/{REPO}/issues", body=payload)
        if "_error_code" in result:
            print(f"ERROR creating audit issue: {result}")
            sys.exit(1)
        print(f"Opened audit issue #{result['number']} (total: {total}).")


if __name__ == "__main__":
    main()
