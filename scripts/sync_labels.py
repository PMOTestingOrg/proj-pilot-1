#!/usr/bin/env python3
"""
sync_labels.py
──────────────
Creates the project-tracking labels in every linked code repo.
Idempotent — safe to re-run. Only the labels not already present get created.

Labels created in each linked repo:
  - project:<slug>      — primary aggregation label
  - project:none        — dismissal label (issue is non-project work)
  - needs-pm            — engineer flags issue for PM attention
  - severity:S1..S4     — bug severity (only if not already present)

Posts a summary as a workflow output and writes to GITHUB_STEP_SUMMARY.

Env vars:
  APP_TOKEN     — GitHub App installation token with `Issues: write` on linked repos
                  (or PAT with `repo` scope)
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

APP_TOKEN = os.environ.get("APP_TOKEN", "").strip() or os.environ.get("PROJECTS_TOKEN", "").strip()
if not APP_TOKEN:
    print("ERROR: APP_TOKEN or PROJECTS_TOKEN required for label sync.")
    sys.exit(1)

GH_API = "https://api.github.com"


def rest(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{GH_API}{path}", data=data, method=method,
        headers={
            "Authorization": f"Bearer {APP_TOKEN}",
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


def standard_labels(slug, display_name):
    """Labels created in every linked repo."""
    return [
        {"name": f"project:{slug}", "color": "0E8A16",
         "description": f"Tracks {display_name} (PMO project)"},
        {"name": "project:none", "color": "EEEEEE",
         "description": "Not associated with any tracked PMO project"},
        {"name": "needs-pm", "color": "FBCA04",
         "description": "Needs PM input/decision — surfaces in PMO dashboard"},
        {"name": "severity:S1", "color": "B60205", "description": "Blocker"},
        {"name": "severity:S2", "color": "D93F0B", "description": "Major"},
        {"name": "severity:S3", "color": "FBCA04", "description": "Minor"},
        {"name": "severity:S4", "color": "C2E0C6", "description": "Trivial"},
    ]


def get_existing_labels(repo):
    """Returns dict: label_name -> existing_label_dict."""
    out = {}
    page = 1
    while True:
        result = rest("GET", f"/repos/{repo}/labels?per_page=100&page={page}")
        if isinstance(result, dict) and "_error_code" in result:
            return None  # access denied or repo missing
        if not result:
            break
        for lab in result:
            out[lab["name"]] = lab
        page += 1
        if page > 10:
            break
    return out


def create_or_update_label(repo, label):
    """Returns 'created' | 'exists' | 'updated' | 'error:reason'."""
    existing = rest("GET", f"/repos/{repo}/labels/{urllib.parse.quote(label['name'])}")
    if "_error_code" not in existing:
        # Exists — check if description differs
        if (existing.get("description") or "") != (label.get("description") or ""):
            result = rest("PATCH", f"/repos/{repo}/labels/{urllib.parse.quote(label['name'])}",
                          body={"new_name": label["name"], "color": label["color"],
                                "description": label["description"]})
            return "updated" if "_error_code" not in result else f"error:{result['_error_code']}"
        return "exists"
    if existing.get("_error_code") != 404:
        return f"error:{existing['_error_code']}"
    # 404 = create
    result = rest("POST", f"/repos/{repo}/labels",
                  body={"name": label["name"], "color": label["color"],
                        "description": label["description"]})
    if "_error_code" in result:
        return f"error:{result['_error_code']}"
    return "created"


def main():
    config = load_config()
    project = config.get("project") or {}
    slug = project.get("slug", "")
    display = project.get("display_name", slug)
    linked = config.get("linked_repos") or []

    if not slug or "REPLACE-ME" in slug.upper():
        print("ERROR: project.slug not set. Edit project-config.yml first.")
        sys.exit(1)

    if not linked:
        print("WARNING: linked_repos is empty. No labels to sync.")
        return

    labels = standard_labels(slug, display)
    summary_lines = []
    summary_lines.append(f"## Label sync results\n")
    summary_lines.append(f"**Project:** `{slug}` ({display})\n")
    summary_lines.append(f"**Repos:** {len(linked)}\n\n")

    overall_ok = True

    for repo in linked:
        print(f"\n→ {repo}")
        summary_lines.append(f"### `{repo}`\n")

        # Quick access check
        existing_labels = get_existing_labels(repo)
        if existing_labels is None:
            msg = f"❌ Cannot access {repo} (404 or permission denied)"
            print(f"  {msg}")
            summary_lines.append(f"- {msg}\n")
            overall_ok = False
            continue

        per_repo = {"created": [], "exists": [], "updated": [], "error": []}
        for label in labels:
            status = create_or_update_label(repo, label)
            print(f"  {label['name']}: {status}")
            if status == "created":
                per_repo["created"].append(label["name"])
            elif status == "exists":
                per_repo["exists"].append(label["name"])
            elif status == "updated":
                per_repo["updated"].append(label["name"])
            else:
                per_repo["error"].append(f"{label['name']} ({status})")
                overall_ok = False

        if per_repo["created"]:
            summary_lines.append(f"- ✅ Created: {', '.join(f'`{n}`' for n in per_repo['created'])}\n")
        if per_repo["updated"]:
            summary_lines.append(f"- 🔄 Updated: {', '.join(f'`{n}`' for n in per_repo['updated'])}\n")
        if per_repo["exists"]:
            summary_lines.append(f"- ↻ Already present: {len(per_repo['exists'])} labels\n")
        if per_repo["error"]:
            summary_lines.append(f"- ❌ Errors: {', '.join(per_repo['error'])}\n")

    summary_lines.append("\n---\n")
    if overall_ok:
        summary_lines.append("✅ **All labels synced successfully.**\n")
    else:
        summary_lines.append("⚠️ **Some labels failed to sync.** See errors above.\n")

    summary = "".join(summary_lines)
    print("\n" + summary)

    # Write to workflow step summary
    step_summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary_file:
        Path(step_summary_file).write_text(summary)

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
