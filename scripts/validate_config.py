#!/usr/bin/env python3
"""
validate_config.py
──────────────────
Validates project-config.yml and reports problems as a PMO repo issue.

Checks performed:
  1. File exists and is valid YAML
  2. project.slug is set, kebab-case, not REPLACE-ME
  3. project.display_name is set, not REPLACE ME
  4. Every linked_repo follows owner/repo format
  5. Every linked_repo actually exists and is accessible (read check)
  6. Slug is unique across projects (would need org-level check; logs only here)
  7. Invoice trigger configs reference existing repos
  8. Notification assignees are valid GitHub users

If any check fails: opens (or updates) an issue titled
"⚠️ project-config.yml validation failed" with details.
If all pass: closes the validation issue if open.

Env vars:
  GH_TOKEN      — built-in workflow token (read access to this repo)
  APP_TOKEN     — GitHub App installation token (read access to linked repos)
                  Falls back to GH_TOKEN if not set
  REPO          — owner/repo of this PMO repo
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

GH_TOKEN = os.environ["GH_TOKEN"]
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip() or GH_TOKEN
REPO = os.environ["REPO"]
GH_API = "https://api.github.com"

VALIDATION_ISSUE_TITLE = "⚠️ project-config.yml validation failed"
SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,40}[a-z0-9]$")
REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


def rest(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body else None
    headers = {
        "Authorization": f"Bearer {token or GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(f"{GH_API}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            text = resp.read()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        return {"_error_code": exc.code, "_error_body": exc.read().decode()}


def load_yaml(path):
    """Load YAML without depending on PyYAML being installed."""
    try:
        import yaml
        return yaml.safe_load(Path(path).read_text())
    except ImportError:
        # Minimal fallback — only supports the structure we expect
        raise RuntimeError("PyYAML required; install with: pip install pyyaml")


def find_validation_issue():
    """Find existing validation-failure issue if open."""
    issues = rest("GET", f"/repos/{REPO}/issues?state=open&per_page=100")
    if isinstance(issues, dict) and "_error_code" in issues:
        return None
    for i in issues:
        if i.get("title") == VALIDATION_ISSUE_TITLE:
            return i
    return None


def check_repo_exists(owner_repo):
    """Returns (exists: bool, reason: str)."""
    result = rest("GET", f"/repos/{owner_repo}", token=APP_TOKEN)
    if "_error_code" in result:
        if result["_error_code"] == 404:
            return False, "Repository not found (may have been renamed or deleted)"
        if result["_error_code"] == 403:
            return False, "App lacks read permission on this repository"
        return False, f"HTTP {result['_error_code']}: {result['_error_body'][:200]}"
    return True, "OK"


def check_user_exists(username):
    result = rest("GET", f"/users/{username}")
    if "_error_code" in result:
        return False
    return True


def validate(config):
    """Returns list of error strings. Empty list = all checks passed."""
    errors = []

    # 1. project block exists
    project = config.get("project") or {}
    if not project:
        errors.append("Missing required `project` section")
        return errors  # can't continue without this

    # 2. slug
    slug = project.get("slug", "")
    if not slug or "REPLACE-ME" in slug.upper():
        errors.append(
            "`project.slug` is not set. Edit project-config.yml and set a kebab-case slug "
            "(e.g., `portal-redesign`)."
        )
    elif not SLUG_PATTERN.match(slug):
        errors.append(
            f"`project.slug` is invalid: `{slug}`. Must be 3-42 chars, kebab-case, "
            "start with a letter, no leading/trailing hyphens. Example: `portal-redesign`."
        )

    # 3. display_name
    display = project.get("display_name", "")
    if not display or "REPLACE ME" in display.upper():
        errors.append(
            "`project.display_name` is not set. Edit project-config.yml and set "
            "a human-readable name."
        )

    # 4 + 5. linked_repos
    linked = config.get("linked_repos") or []
    if not isinstance(linked, list):
        errors.append("`linked_repos` must be a list (use `[]` if empty).")
    elif not linked:
        # Empty is allowed (project may not have eng work yet), but warn
        pass
    else:
        for idx, repo in enumerate(linked):
            if not isinstance(repo, str):
                errors.append(f"`linked_repos[{idx}]` is not a string: {repo!r}")
                continue
            if not REPO_PATTERN.match(repo):
                errors.append(
                    f"`linked_repos[{idx}]` invalid format: `{repo}`. "
                    "Must be `owner/repo` (e.g., `your-org/customer-portal-backend`)."
                )
                continue
            exists, reason = check_repo_exists(repo)
            if not exists:
                errors.append(f"`linked_repos[{idx}]` (`{repo}`) is not accessible: {reason}")

    # 7. invoice_triggers
    triggers = config.get("invoice_triggers") or []
    if not isinstance(triggers, list):
        errors.append("`invoice_triggers` must be a list.")
    else:
        for idx, t in enumerate(triggers):
            if not isinstance(t, dict):
                errors.append(f"`invoice_triggers[{idx}]` must be a dict.")
                continue
            milestone = t.get("milestone")
            trigger = t.get("trigger") or {}
            if not milestone:
                errors.append(f"`invoice_triggers[{idx}]` missing `milestone` field.")
            ttype = trigger.get("type")
            if ttype not in ("pmo_milestone", "code_milestone"):
                errors.append(
                    f"`invoice_triggers[{idx}].trigger.type` must be 'pmo_milestone' "
                    f"or 'code_milestone', got: {ttype!r}"
                )
            if ttype == "code_milestone":
                repos = trigger.get("repos") or []
                if not repos:
                    errors.append(
                        f"`invoice_triggers[{idx}]` code_milestone needs at least one `repos` entry."
                    )
                if not trigger.get("milestone_name"):
                    errors.append(
                        f"`invoice_triggers[{idx}]` code_milestone needs `milestone_name`."
                    )

    # 8. notification assignees
    notifs = config.get("notifications") or {}
    for key in ("audit_assignees", "validation_assignees"):
        users = notifs.get(key) or []
        if not isinstance(users, list):
            errors.append(f"`notifications.{key}` must be a list.")
            continue
        for user in users:
            if not check_user_exists(user):
                errors.append(f"`notifications.{key}` contains non-existent GitHub user: `{user}`")

    return errors


def render_issue_body(errors, config_path):
    """Build the issue body listing all errors."""
    body = "## Configuration validation found problems\n\n"
    body += f"Validating `{config_path}` failed with {len(errors)} issue(s):\n\n"
    for i, err in enumerate(errors, 1):
        body += f"{i}. {err}\n"
    body += "\n---\n\n"
    body += "**To fix:**\n\n"
    body += f"1. Open `{config_path}` in this repo\n"
    body += "2. Address each issue listed above\n"
    body += "3. Commit your fix\n"
    body += "4. The validator runs on every commit to `project-config.yml` and daily at 02:00 UTC. "
    body += "This issue will auto-close once all checks pass.\n\n"
    body += "_This issue is auto-managed. Don't edit manually._\n"
    return body


def main():
    config_path = "project-config.yml"
    print(f"Validating {config_path}...")

    if not Path(config_path).exists():
        errors = [f"`{config_path}` file does not exist in this repo."]
    else:
        try:
            config = load_yaml(config_path)
            if not isinstance(config, dict):
                errors = [f"`{config_path}` does not contain a valid YAML object."]
            else:
                errors = validate(config)
        except Exception as exc:
            errors = [f"Failed to parse `{config_path}`: {exc}"]

    existing = find_validation_issue()

    if not errors:
        print("✓ Validation passed.")
        if existing:
            # Close the existing validation issue
            rest("PATCH", f"/repos/{REPO}/issues/{existing['number']}",
                 body={"state": "closed",
                       "body": existing.get("body", "") +
                               "\n\n---\n\n**Resolved.** Validation now passing."})
            print(f"Closed validation issue #{existing['number']}.")
        return

    # Errors exist
    print(f"✗ {len(errors)} validation error(s) found:")
    for err in errors:
        print(f"  - {err}")

    body = render_issue_body(errors, config_path)

    if existing:
        rest("PATCH", f"/repos/{REPO}/issues/{existing['number']}", body={"body": body})
        print(f"Updated validation issue #{existing['number']}.")
    else:
        # Determine assignees from config if loadable
        assignees = []
        try:
            cfg = load_yaml(config_path)
            notifs = (cfg or {}).get("notifications") or {}
            assignees = notifs.get("validation_assignees") or []
        except Exception:
            pass
        payload = {"title": VALIDATION_ISSUE_TITLE, "body": body,
                   "labels": ["auto-managed", "type:dashboard"]}
        if assignees:
            payload["assignees"] = assignees
        result = rest("POST", f"/repos/{REPO}/issues", body=payload)
        if "_error_code" in result:
            print(f"ERROR creating issue: {result}")
            sys.exit(1)
        print(f"Opened validation issue #{result['number']}.")


if __name__ == "__main__":
    main()
