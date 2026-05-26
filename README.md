# Project Template

Standardized GitHub template for software delivery projects. Provides:

- 6 delivery phases as parent issues with sub-issues
- A Project board with custom fields, dates, and sprint iterations
- GitHub Milestones for contract-based invoicing
- A live, auto-updating dashboard
- **Cross-repo aggregation** — pulls engineering activity from linked code repos
- Daily snapshots and weekly progress timeline

> **For admins setting this up:** see [`docs/admin-setup.md`](docs/admin-setup.md).
> **For PMs starting a new project:** read on.

---

## Quick start for PMs (5 steps, ~5 minutes)

After creating a repo from this template, do these once per new project:

### 1. Enable workflow write permissions
**Settings → Actions → General** → scroll to **Workflow permissions** → **Read and write permissions** → Save.

### 2. Confirm required secrets/variables are in place
Settings → Secrets and variables → Actions. Verify these are inherited from your org (ask admin if not):

| Type | Name | Purpose |
|---|---|---|
| Secret | `PROJECTS_TOKEN` | PAT for board/sub-issue operations |
| Secret | `APP_TOKEN` | PMO GitHub App installation token (cross-repo reads) |
| Variable | `GOLDEN_PROJECT_OWNER` | Owner of the golden Project board to clone |
| Variable | `GOLDEN_PROJECT_NUMBER` | Number of the golden Project |

### 3. Edit `project-config.yml`
Open the file in this repo and fill in:

```yaml
project:
  slug: your-project-slug              # kebab-case, becomes label "project:<slug>"
  display_name: "Your Project Name"

linked_repos:
  - your-org/code-repo-1
  - your-org/code-repo-2

invoice_triggers:                       # optional, see file for details
  - milestone: "Invoice 1 — BRD Sign-Off"
    trigger: { type: pmo_milestone }
```

Commit the file. A validator runs automatically and posts an issue if anything is wrong.

### 4. Run setup workflow
**Actions tab → 🛠 Setup project → Run workflow** → wait ~90 seconds.

This creates: the 8 PMO phase issues, the Project board (cloned from golden), and the 3 invoice milestones.

### 5. Sync labels to engineering repos
**Actions tab → 🔗 Sync labels to linked repos → Run workflow** → wait ~30 seconds.

This creates `project:<your-slug>`, `needs-pm`, `severity:S1`–`S4`, and `project:none` labels in each linked engineering repo. One-click, idempotent.

After this, **tell your engineering leads** the project label name. Engineers should apply `project:<your-slug>` to all relevant issues. The PMO GitHub App will nudge unlabeled issues automatically.

---

## What you get

### Issues tab
- 📈 Project Dashboard (pinned, auto-updating)
- 📋 Project Charter (pinned)
- 6 phase parent issues with sub-issues nested inside each

### Projects tab
A board cloned from the golden template, with custom fields and pre-configured views.

### Milestones tab
3 invoice milestone placeholders to rename per your contract.

### Cross-repo visibility
The dashboard surfaces:
- **Engineering Activity** — open/closed/recently-active issues per linked repo
- **Needs PM Attention** — issues engineers tagged with `needs-pm`
- **High-Severity Bugs** — S1/S2 bugs in linked repos
- **Sprint Status** — open milestones in linked engineering repos
- **Recently Closed** — engineering work closed in last 7 days
- **Invoice Trigger Status** — cross-repo milestone state for invoicing

---

## How cross-repo linkage works

### The label convention
Every issue in a linked engineering repo that belongs to this project should have the label `project:<your-slug>`. Engineers apply this when filing or triaging issues.

### Enforcement levels

1. **PMO GitHub App nudge** — instant comment + `needs-project-label` flag on new issues without a project label
2. **Daily audit** — once a day, lists all unlabeled engineering issues in a PMO repo issue
3. **PM review** — periodically check the audit issue and nudge engineering leads

### Multiple projects per code repo
A shared service can be linked to multiple PMO projects. Engineers apply multiple `project:*` labels when issues span projects.

### What if engineers don't apply labels?
- Those issues won't appear in the PMO dashboard
- They'll show up in the daily audit report
- The App will nudge but can't force compliance
- This is a process discipline issue — work with engineering leadership

---

## Your daily workflow

| When | What | Where |
|---|---|---|
| Start of day | Open Dashboard issue, scan health | Issues tab |
| Engineering question raised | See it in "Needs PM Attention" → respond in code repo | Dashboard → deep link |
| Update progress | Status / % Complete fields | Project board |
| New risk | New Issue → ⚠️ Risk | Issues tab |
| Friday status | New Issue → 📊 Weekly Status Report | Issues tab |
| Invoice trigger met | Close the milestone | Milestones tab |

---

## Automations

| Workflow | Schedule | Purpose |
|---|---|---|
| 🛠 Setup project | Manual | One-time scaffolding |
| 🔄 Dashboard update | Every 5 min + on changes | Refresh dashboard with PMO + cross-repo data |
| 🔗 Sync labels | Manual | Create project labels in linked code repos |
| ✅ Validate project-config | On config change + 03:00 UTC daily | Catch drift |
| 🔍 Daily audit | 02:00 UTC daily | Report unlabeled engineering issues |
| 📅 Daily snapshot | 02:00 UTC daily | Save dashboard archive |
| 📈 Weekly summary | Monday 09:00 UTC | Append timeline row |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Setup fails: "Resource not accessible" | Workflow permissions read-only |
| No board cloned | `PROJECTS_TOKEN` or `GOLDEN_PROJECT_*` missing |
| Cross-repo sections empty | `APP_TOKEN` missing or no `linked_repos` |
| Validation issue opens | `project-config.yml` has problems |
| Engineering not applying labels | Process issue — work with eng leads |

See `docs/PM-quick-reference.md` and `docs/admin-setup.md`.
