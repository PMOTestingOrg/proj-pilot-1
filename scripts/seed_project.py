#!/usr/bin/env python3
"""
seed_project.py
---------------
Scaffolds a new project repo:
  1. 8 top-level issues + native sub-issues
  2. 3 starter GitHub Milestones for invoicing
  3. Clones the golden Project board
  4. Pins ONLY the Dashboard (workflow updates the second pin to the active phase)

Env vars:
  GH_TOKEN              built-in workflow token
  PROJECTS_TOKEN        PAT with 'project' + 'repo' scopes
  GOLDEN_PROJECT_OWNER  owner of the golden project
  GOLDEN_PROJECT_NUMBER project number from URL
  REPO                  owner/repo
  OWNER                 repo owner
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

GH_TOKEN = os.environ["GH_TOKEN"]
PROJECTS_TOKEN = os.environ.get("PROJECTS_TOKEN", "").strip()
GOLDEN_OWNER = os.environ.get("GOLDEN_PROJECT_OWNER", "").strip()
GOLDEN_NUMBER = os.environ.get("GOLDEN_PROJECT_NUMBER", "").strip()
REPO = os.environ["REPO"]
OWNER = os.environ.get("OWNER") or REPO.split("/")[0]
GH_API = "https://api.github.com"


def rest(method, path, body=None):
    url = f"{GH_API}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            text = resp.read()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        err = exc.read().decode()
        print(f"REST {method} {path}: HTTP {exc.code} - {err}", file=sys.stderr)
        raise


def graphql(query, variables=None, use_pat=True):
    token = PROJECTS_TOKEN if (use_pat and PROJECTS_TOKEN) else GH_TOKEN
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "GraphQL-Features": "sub_issues"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        err = exc.read().decode()
        raise RuntimeError(f"GraphQL HTTP {exc.code}: {err}")
    if "errors" in data:
        raise RuntimeError(f"GraphQL: {data['errors']}")
    return data.get("data", {})


# Issue / milestone helpers
def create_issue(title, body, labels):
    issue = rest("POST", f"/repos/{REPO}/issues", {"title": title, "body": body, "labels": labels})
    print(f"  Issue #{issue['number']}: {title}")
    return issue


def add_sub_issue(parent_node_id, child_node_id):
    graphql(
        """
        mutation($issueId: ID!, $subIssueId: ID!) {
          addSubIssue(input: { issueId: $issueId, subIssueId: $subIssueId }) {
            issue { number }
          }
        }
        """,
        {"issueId": parent_node_id, "subIssueId": child_node_id},
    )


def create_milestone(title, description, due_days_offset):
    existing = rest("GET", f"/repos/{REPO}/milestones?state=all&per_page=100")
    for m in existing:
        if m["title"] == title:
            return m["number"]
    due = (datetime.now(timezone.utc) + timedelta(days=due_days_offset)).strftime("%Y-%m-%dT00:00:00Z")
    result = rest("POST", f"/repos/{REPO}/milestones",
                  {"title": title, "description": description, "due_on": due, "state": "open"})
    print(f"  Milestone: {title}")
    return result["number"]


def project_already_seeded():
    """Check by exact title, not by label (more robust)."""
    issues = rest("GET", f"/repos/{REPO}/issues?state=all&per_page=100")
    return any(i["title"] == "Project Dashboard" for i in issues)


def pin_issue(issue_node_id):
    graphql(
        "mutation($id: ID!) { pinIssue(input: {issueId: $id}) { issue { id } } }",
        {"id": issue_node_id}, use_pat=False,
    )


# Body builders - lean, no charter form
def phase_parent_body(name, goal):
    return f"""# {name}

**Goal:** {goal}

This is a parent issue. Sub-tasks are listed in the **Sub-issues** section below (provided by GitHub natively).

Dates, status, and progress live on the **Project board** - not in this issue body.

Close this issue when all sub-issues are done.
"""


def lightweight_doc_body(doc_name, where):
    return f"""## What to do

1. Author **{doc_name}** in your team's external system ({where}).
2. Review and (if required) get sign-off.
3. Paste the link below.
4. Set **Actual End** on the Project board.
5. Close this issue.

## Deliverable link
_(paste link to the document here)_

## Acceptance
- [ ] Document authored and reviewed
- [ ] (If required) signed off
- [ ] Link added above
- [ ] Issue closed
"""


def session_body(n):
    return f"""## What to do

Conduct **Requirement Session {n}** with the client.

1. Prepare agenda (share 24h ahead).
2. Run the session.
3. Document minutes externally; paste link below.
4. Set **Actual End** on the board, close this issue.

## Minutes link
_(paste here)_

## Acceptance
- [ ] Session conducted
- [ ] Minutes documented and shared
- [ ] Open questions logged
"""


def operational_body(desc, steps):
    checklist = "\n".join(f"- [ ] {s}" for s in steps)
    return f"""## What to do

{desc}.

## Acceptance
{checklist}

Set dates/status on the **Project board**, then close this issue.
"""


def charter_body():
    return """## Project Charter

The charter document is authored externally (SharePoint / Drive / Confluence).

## What to do

1. Author the charter externally with: project name, sponsor, dates, budget, scope, stakeholders.
2. Get sponsor sign-off.
3. Paste link below.
4. Close this issue.

## Charter link
_(paste link to the signed charter)_

## Acceptance
- [ ] Charter authored
- [ ] Sponsor sign-off obtained
- [ ] Link added above
- [ ] Issue closed
"""


def dashboard_body_initial():
    return """# Project Dashboard

This dashboard will populate within ~30 seconds via the **Dashboard update** workflow.

If empty, trigger manually: **Actions tab -> Dashboard update -> Run workflow**.
"""


# Project board cloning
def get_owner_node_id(login):
    """Get the GraphQL node ID for an owner (org or user).
    Uses a single combined query so a NOT_FOUND on one path doesn't
    blow up the other. The repositoryOwner interface covers both.
    """
    query = """
    query($login: String!) {
      repositoryOwner(login: $login) { id __typename }
    }
    """
    try:
        result = graphql(query, {"login": login})
        owner = result.get("repositoryOwner")
        if owner and owner.get("id"):
            print(f"  Owner '{login}' resolved as {owner.get('__typename', '?')}")
            return owner["id"]
    except Exception as exc:
        # Fall through to explicit lookups
        print(f"  repositoryOwner query failed for '{login}': {exc}")

    # Fallback 1: try as org explicitly
    try:
        result = graphql(
            "query($login: String!) { organization(login: $login) { id } }",
            {"login": login}
        )
        if result.get("organization") and result["organization"].get("id"):
            return result["organization"]["id"]
    except Exception:
        pass

    # Fallback 2: try as user
    try:
        result = graphql(
            "query($login: String!) { user(login: $login) { id } }",
            {"login": login}
        )
        if result.get("user") and result["user"].get("id"):
            return result["user"]["id"]
    except Exception:
        pass

    raise RuntimeError(
        f"Could not resolve owner '{login}' as either an organization or user. "
        f"Check that the name is exact (case-sensitive) and that PROJECTS_TOKEN "
        f"has been authorized for the org."
    )


def get_repo_node_id():
    owner, name = REPO.split("/")
    result = graphql(
        "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { id } }",
        {"owner": owner, "name": name},
    )
    return result["repository"]["id"]


def get_golden_project_node_id():
    """Find the golden project by owner+number, regardless of whether owner is org or user."""
    # repositoryOwner doesn't have projectV2 directly, so we use a union query
    query = """
    query($login: String!, $number: Int!) {
      organization(login: $login) {
        projectV2(number: $number) { id title }
      }
      user(login: $login) {
        projectV2(number: $number) { id title }
      }
    }
    """
    # Use raw POST so we can inspect both data AND errors
    body = json.dumps({"query": query,
                       "variables": {"login": GOLDEN_OWNER, "number": int(GOLDEN_NUMBER)}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body, method="POST",
        headers={"Authorization": f"Bearer {PROJECTS_TOKEN}",
                 "Content-Type": "application/json",
                 "GraphQL-Features": "sub_issues"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Golden project lookup HTTP {exc.code}: {exc.read().decode()}")

    result = data.get("data") or {}

    # Try org path first
    org = result.get("organization")
    if org and org.get("projectV2"):
        proj = org["projectV2"]
        return proj["id"], proj["title"]
    # Then user path
    user = result.get("user")
    if user and user.get("projectV2"):
        proj = user["projectV2"]
        return proj["id"], proj["title"]

    # Neither resolved - surface the most useful error
    errors = data.get("errors") or []
    err_msgs = [e.get("message", str(e)) for e in errors]
    raise RuntimeError(
        f"Golden project not found at {GOLDEN_OWNER}/projects/{GOLDEN_NUMBER}. "
        f"Errors: {err_msgs}"
    )


def copy_project(source_project_id, target_owner_id, title):
    result = graphql(
        """
        mutation($sourceId: ID!, $ownerId: ID!, $title: String!) {
          copyProjectV2(input: {
            projectId: $sourceId, ownerId: $ownerId, title: $title, includeDraftIssues: false
          }) {
            projectV2 { id number url title }
          }
        }
        """,
        {"sourceId": source_project_id, "ownerId": target_owner_id, "title": title},
    )
    return result["copyProjectV2"]["projectV2"]


def link_repo_to_project(project_id, repo_id):
    graphql(
        """
        mutation($projectId: ID!, $repoId: ID!) {
          linkProjectV2ToRepository(input: { projectId: $projectId, repositoryId: $repoId }) {
            repository { name }
          }
        }
        """,
        {"projectId": project_id, "repoId": repo_id},
    )


def list_project_fields(project_id):
    result = graphql(
        """
        query($projectId: ID!) {
          node(id: $projectId) {
            ... on ProjectV2 {
              fields(first: 50) {
                nodes {
                  ... on ProjectV2Field { id name dataType }
                  ... on ProjectV2SingleSelectField {
                    id name dataType
                    options { id name }
                  }
                  ... on ProjectV2IterationField { id name dataType }
                }
              }
            }
          }
        }
        """,
        {"projectId": project_id},
    )
    out = {}
    for f in result["node"]["fields"]["nodes"]:
        if not f or not f.get("name"):
            continue
        entry = {"id": f["id"]}
        if "options" in f:
            entry["options"] = {o["name"]: o["id"] for o in f["options"]}
        out[f["name"]] = entry
    return out


def add_issue_to_project(project_id, issue_node_id):
    try:
        result = graphql(
            """
            mutation($projectId: ID!, $contentId: ID!) {
              addProjectV2ItemById(input: { projectId: $projectId, contentId: $contentId }) {
                item { id }
              }
            }
            """,
            {"projectId": project_id, "contentId": issue_node_id},
        )
        return result["addProjectV2ItemById"]["item"]["id"]
    except Exception as exc:
        print(f"    Could not add to board: {exc}")
        return None


def set_field_date(project_id, item_id, field_id, iso_date):
    graphql(
        """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: Date!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId, itemId: $itemId, fieldId: $fieldId, value: { date: $value }
          }) { projectV2Item { id } }
        }
        """,
        {"projectId": project_id, "itemId": item_id, "fieldId": field_id, "value": iso_date},
    )


def set_field_single_select(project_id, item_id, field_id, option_id):
    graphql(
        """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId, itemId: $itemId, fieldId: $fieldId, value: { singleSelectOptionId: $optionId }
          }) { projectV2Item { id } }
        }
        """,
        {"projectId": project_id, "itemId": item_id, "fieldId": field_id, "optionId": option_id},
    )


def main():
    today = datetime.now(timezone.utc).date()

    if project_already_seeded():
        print("\nProject already seeded - skipping to avoid duplicates.")
        return

    print("\nCreating invoice milestones...\n")
    create_milestone("Invoice 1 - TO RENAME (e.g. BRD Sign-Off)",
                     "Rename and set due date based on contract.", 45)
    create_milestone("Invoice 2 - TO RENAME (e.g. UAT Sign-Off)",
                     "Rename per contract.", 150)
    create_milestone("Invoice 3 - TO RENAME (e.g. Project Closure)",
                     "Rename per contract.", 215)

    print("\nCreating top-level issues...\n")
    # NOTE: Plain titles (no emoji) to make string-matching robust
    charter = create_issue("Project Charter", charter_body(), ["type:charter"])
    dashboard = create_issue("Project Dashboard", dashboard_body_initial(),
                              ["type:dashboard", "auto-managed"])

    print("\nCreating phase parents and sub-issues...\n")

    structure = [
        {
            "parent_title": "1. Initiation & Planning",
            "parent_goal": "Team staffed, kickoff held.",
            "planned_start_offset": 0, "planned_end_offset": 15,
            "phase_name": "1. Initiation & Planning",
            "subs": [
                ("Project Staffing",
                 operational_body("Identify and onboard the project team",
                                  ["Tech Lead identified", "Developers assigned",
                                   "QA resource assigned", "Tools access confirmed"]),
                 0, 10),
                ("Kick-Off Meeting",
                 operational_body("Conduct formal kickoff with all stakeholders",
                                  ["Agenda prepared", "Meeting held",
                                   "Minutes documented", "Action items logged"]),
                 10, 15),
            ],
        },
        {
            "parent_title": "2. Requirement Gathering",
            "parent_goal": "Gather requirements through sessions, BRD signed off.",
            "planned_start_offset": 15, "planned_end_offset": 45,
            "phase_name": "2. Requirement Gathering",
            "subs": [
                ("Requirement Session 1", session_body(1), 15, 18),
                ("Requirement Session 2", session_body(2), 20, 23),
                ("Requirement Session 3", session_body(3), 25, 28),
                ("BRD Creation", lightweight_doc_body("BRD", "SharePoint / Drive"), 28, 38),
                ("BRD Sign-Off", lightweight_doc_body("signed BRD", "SharePoint / Drive"), 38, 45),
            ],
        },
        {
            "parent_title": "3. Development",
            "parent_goal": "Build the solution. Sprints managed via Sprint field on board.",
            "planned_start_offset": 45, "planned_end_offset": 135,
            "phase_name": "3. Development",
            "subs": [],
        },
        {
            "parent_title": "4. UAT",
            "parent_goal": "Conduct UAT and obtain client sign-off.",
            "planned_start_offset": 135, "planned_end_offset": 165,
            "phase_name": "4. UAT",
            "subs": [
                ("Test Cases", lightweight_doc_body("UAT test cases", "TestRail / Jira / external"), 135, 155),
                ("UAT Sign-Off", lightweight_doc_body("signed UAT sign-off", "SharePoint / Drive"), 155, 165),
            ],
        },
        {
            "parent_title": "5. Production Deployment",
            "parent_goal": "Deploy, go live, sign-off.",
            "planned_start_offset": 165, "planned_end_offset": 195,
            "phase_name": "5. Production Deployment",
            "subs": [
                ("Access to Client Environment",
                 operational_body("Obtain access and permissions",
                                  ["Credentials received", "VPN verified", "Roles granted"]),
                 165, 175),
                ("Setup in Client Environment",
                 operational_body("Deploy and smoke-test in client production",
                                  ["Infra provisioned", "App deployed",
                                   "Smoke tests pass", "Monitoring configured"]),
                 175, 185),
                ("Go Live",
                 operational_body("Execute go-live cutover",
                                  ["Cutover plan approved", "Go-live executed",
                                   "Verification complete", "Stakeholders notified"]),
                 185, 190),
                ("Production Sign-Off",
                 lightweight_doc_body("signed production sign-off", "SharePoint / Drive"),
                 190, 195),
            ],
        },
        {
            "parent_title": "6. Closure",
            "parent_goal": "Deliver user guide and close the project.",
            "planned_start_offset": 195, "planned_end_offset": 215,
            "phase_name": "6. Closure",
            "subs": [
                ("User Guide", lightweight_doc_body("User Guide", "SharePoint / Drive"), 195, 210),
            ],
        },
    ]

    all_for_board = []
    all_for_board.append({"issue": charter, "phase": "1. Initiation & Planning",
                          "planned_start": today.isoformat(),
                          "planned_end": (today + timedelta(days=10)).isoformat()})
    all_for_board.append({"issue": dashboard, "phase": None,
                          "planned_start": None, "planned_end": None})

    parent_node_ids = []  # for the dashboard workflow to find them later

    for phase in structure:
        # IMPORTANT: type:phase label only - no other phase labels needed
        parent = create_issue(phase["parent_title"], phase_parent_body(phase["parent_title"], phase["parent_goal"]),
                              ["type:phase"])
        parent_node_ids.append(parent["node_id"])
        all_for_board.append({
            "issue": parent, "phase": phase["phase_name"],
            "planned_start": (today + timedelta(days=phase["planned_start_offset"])).isoformat(),
            "planned_end": (today + timedelta(days=phase["planned_end_offset"])).isoformat(),
        })

        for sub_title, sub_body, ps_off, pe_off in phase["subs"]:
            # ONLY is:sub-task label - keeps things minimal
            sub = create_issue(sub_title, sub_body, ["is:sub-task"])
            try:
                add_sub_issue(parent["node_id"], sub["node_id"])
                print(f"    Linked #{sub['number']} as sub-issue of #{parent['number']}")
            except Exception as exc:
                print(f"    Could not link sub-issue #{sub['number']}: {exc}")
            all_for_board.append({
                "issue": sub, "phase": phase["phase_name"],
                "planned_start": (today + timedelta(days=ps_off)).isoformat(),
                "planned_end": (today + timedelta(days=pe_off)).isoformat(),
            })

    # Pin ONLY the dashboard initially. The dashboard workflow updates the second pin
    # to be the currently-active phase.
    print("\nPinning Dashboard...\n")
    try:
        pin_issue(dashboard["node_id"])
        print(f"  Pinned #{dashboard['number']} (Dashboard)")
    except Exception as exc:
        print(f"  Could not pin dashboard: {exc}")

    # Clone the golden Project board
    if not PROJECTS_TOKEN:
        print("\nPROJECTS_TOKEN missing - skipping Project board cloning.")
        return
    if not GOLDEN_OWNER or not GOLDEN_NUMBER:
        print("\nGOLDEN_PROJECT_OWNER and/or GOLDEN_PROJECT_NUMBER not set.")
        return

    print(f"\nCloning golden project ({GOLDEN_OWNER}/projects/{GOLDEN_NUMBER})...\n")
    try:
        source_id, source_title = get_golden_project_node_id()
        print(f"  Source: {source_title}")

        target_owner_id = get_owner_node_id(OWNER)
        repo_id = get_repo_node_id()
        new_title = f"{REPO.split('/')[-1]} - Tracker"

        cloned = copy_project(source_id, target_owner_id, new_title)
        project_id = cloned["id"]
        project_url = cloned["url"]
        print(f"  Cloned: {project_url}")

        link_repo_to_project(project_id, repo_id)
        print(f"  Linked to repo")

        print("\n  Discovering fields...")
        fields = list_project_fields(project_id)
        phase_field = fields.get("Phase")
        planned_start_field = fields.get("Planned Start")
        planned_end_field = fields.get("Planned End")

        print("\n  Adding issues to board...")
        for entry in all_for_board:
            issue = entry["issue"]
            item_id = add_issue_to_project(project_id, issue["node_id"])
            if not item_id:
                continue
            if entry.get("planned_start") and planned_start_field:
                try:
                    set_field_date(project_id, item_id, planned_start_field["id"], entry["planned_start"])
                except Exception:
                    pass
            if entry.get("planned_end") and planned_end_field:
                try:
                    set_field_date(project_id, item_id, planned_end_field["id"], entry["planned_end"])
                except Exception:
                    pass
            if entry.get("phase") and phase_field and phase_field.get("options"):
                opt_id = phase_field["options"].get(entry["phase"])
                if opt_id:
                    try:
                        set_field_single_select(project_id, item_id, phase_field["id"], opt_id)
                    except Exception:
                        pass

        print(f"\nBoard ready: {project_url}")
    except Exception as exc:
        print(f"\nBoard cloning failed: {exc}")


if __name__ == "__main__":
    main()
