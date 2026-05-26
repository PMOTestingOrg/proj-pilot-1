# Admin Setup — PMO Cross-Repo System

One-time setup by your platform admin. After this, PMs self-serve project creation
and engineering is largely untouched.

**Time required:** ~1 hour for initial setup + 30 min for the GitHub App + 30 min
to pilot the first project. So roughly **half a day** end-to-end.

**Components to deploy:**
1. **GitHub Organization** on Team plan or higher (one-time)
2. **`pm-template` repository** — what PMs clone from
3. **Golden Project board** — cloned per project
4. **PMO GitHub App** (`pmo-github-app` repo) — runs label-enforcement nudges
5. **Org-level secrets/variables** — credentials inherited by every PMO project repo

---

## Step 1 — Create the GitHub Organization

If you don't already have one:

1. Go to https://github.com/organizations/new
2. Choose **Team** plan ($4/user/month, required for org secrets and proper permission boundaries at scale)
3. Create teams (recommended baseline):
   - `pmo` — your 20 PMs + platform admins (write to PMO repos)
   - `engineering` — 250 engineers (write to code repos)
   - `engineering-leads` — admin on code repos
   - `platform-admins` — owners of the PMO infrastructure (you + 1 backup)

---

## Step 2 — Build the Golden Project board

This is the master Project board that gets cloned for every new PMO project.

1. Go to `https://github.com/<your-org>?tab=projects`
2. **New project** → **Table** layout → name it `Template Project - DO NOT USE FOR REAL WORK` → Create
3. Add custom fields (click `+` to the right of column headers):

| Field | Type | Options |
|---|---|---|
| `Phase` | Single select | `1. Initiation & Planning`, `2. Requirement Gathering`, `3. Development`, `4. UAT`, `5. Production Deployment`, `6. Closure` |
| `Health` | Single select | `🟢 Green`, `🟡 Yellow`, `🔴 Red` |
| `Priority` | Single select | `P0 - Critical`, `P1 - High`, `P2 - Medium`, `P3 - Low` |
| `Planned Start` | Date | — |
| `Planned End` | Date | — |
| `Actual Start` | Date | — |
| `Actual End` | Date | — |
| `% Complete` | Number | — |
| `Sprint` | Iteration | 14-day duration; add 3 starter iterations |

4. Create these views (click `+ New view`):

| View | Layout | Group by | Filter |
|---|---|---|---|
| `All Work` | Table | (none) | (none) |
| `By Phase` | Table | Phase | (none) |
| `Sprint Board` | Board | Sprint | `phase:"3. Development"` |
| `At Risk` | Table | (none) | `health:"🔴 Red"` |

5. Note the URL — `https://github.com/orgs/<your-org>/projects/N`. Write down the number `N`.

---

## Step 3 — Push the `pm-template` repo

1. Create empty repo `pm-template` (or `template`) in your org
2. Push the contents of this bundle's `pm-template/` directory
3. Settings → General → check **Template repository** ✅
4. Settings → Actions → General → **Workflow permissions** → **Read and write permissions** → Save

---

## Step 4 — Generate the Personal Access Token

Used by setup-time operations (board cloning, sub-issues).

1. Sign in as platform admin
2. https://github.com/settings/tokens → **Generate new token (classic)**
3. Note: `PMO board cloning + sub-issues`
4. Expiration: 1 year (set a calendar reminder)
5. Scopes: ✅ `repo` and ✅ `project`
6. Generate → copy the `ghp_...` value

---

## Step 5 — Register the PMO GitHub App

The App listens to issue events and nudges engineers to apply project labels.

### 5a. Register the App

1. `https://github.com/organizations/<your-org>/settings/apps` → **New GitHub App**
2. Fill in:
   - **GitHub App name:** `PMO Project Tracker`
   - **Homepage URL:** URL of your `pmo-github-app` repo
   - **Webhook → Active:** Uncheck (polling mode initially)
3. **Repository permissions:**
   - Contents: Read-only
   - Issues: Read and write
   - Metadata: Read-only
   - Pull requests: Read-only
4. **Where can this be installed?** Only on this account
5. Click **Create GitHub App**

### 5b. Generate App credentials

1. Note the **App ID** at the top
2. Scroll to **Private keys** → **Generate a private key** → save the `.pem` file safely

### 5c. Install the App on your org

1. App settings → **Install App** in sidebar
2. **Install** next to your org → choose **All repositories** → Install
3. URL becomes `.../installations/<INSTALLATION_ID>` — note this number

### 5d. Push the `pmo-github-app` repo

1. Create empty repo `pmo-github-app` in your org
2. Push the contents of this bundle's `pmo-github-app/` directory
3. Settings → Actions → General → Read and write → Save
4. Settings → Secrets and variables → Actions:

| Type | Name | Value |
|---|---|---|
| Secret | `APP_ID` | App ID from 5b |
| Secret | `APP_PRIVATE_KEY` | Full contents of the `.pem` file |
| Secret | `APP_INSTALLATION_ID` | Installation ID from 5c |
| Variable | `PMO_ORG` | Your org name |
| Variable | `PMO_REPO_PREFIX` | `proj-` (or your convention) |

5. Actions → 🤖 PMO App — polling → **Run workflow** to test (should report "0 PMO projects found")

---

## Step 6 — Set org-level secrets and variables

Org Settings → Secrets and variables → Actions:

| Type | Name | Value |
|---|---|---|
| Secret | `PROJECTS_TOKEN` | The PAT from Step 4 |
| Secret | `APP_TOKEN` | Same PAT (or installation token for higher rate limits) |
| Variable | `GOLDEN_PROJECT_OWNER` | Your org name |
| Variable | `GOLDEN_PROJECT_NUMBER` | Number from Step 2 |

---

## Step 7 — Pilot a project

1. From `pm-template` → **Use this template** → name `proj-pilot-test`
2. Settings → Actions → General → Read and write → Save
3. Edit `project-config.yml`:
   ```yaml
   project:
     slug: pilot-test
     display_name: "Pilot Test Project"
   linked_repos:
     - your-org/some-existing-code-repo
   ```
4. Commit. Validator runs.
5. Actions → 🛠 Setup project → Run workflow (~90s)
6. Actions → 🔗 Sync labels → Run workflow (~30s)
7. Verify dashboard, cross-repo sections, audit, App nudge as described in build plan tests.

---

## Step 8 — Roll out to PMs and engineers

**PM message template:**
```
Hi PMs — we have a new project template at <url>.
For any new project:
1. Use this template → name it proj-<your-slug>
2. Edit project-config.yml (instructions in README)
3. Run Setup + Sync labels workflows
4. Tell engineering the project:<slug> label name

Full guide: <link to template README>
```

**Engineering message template:**
```
Heads up — when filing issues in <list of repos>, please apply:
- project:<slug> if it belongs to a tracked project
- project:none if it's not project work
- needs-pm if you need PM input
- severity:S1 / S2 for blocker / major bugs

The PMO bot will nudge if you forget. Apply labels at file time
or shortly after to keep PMO dashboards accurate.
```

---

## Operational concerns

### Rate limits
- PAT polling: 5000 req/hr
- App installation token: 15000 req/hr (worth migrating to if scale grows)
- At pilot scale (5 projects × 5 repos × 12 polls/hr) = ~300 req/hr — well within either limit

### Token rotation
- PAT: rotate annually (set calendar reminder)
- App private key: rotate annually as best practice

### When a linked repo is renamed
- Validator catches within 24 hours
- Opens issue in PMO repo with broken link
- PM updates `project-config.yml` to fix

### When you need webhooks instead of polling
- The App code supports `MODE=webhook`
- Deploy as serverless function (Cloudflare Workers, AWS Lambda)
- Set webhook URL in App settings, provide `WEBHOOK_SECRET`
- Not necessary unless 5-min lag becomes a real problem

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Setup fails "Resource not accessible" | Workflow permissions read-only (Step 3) |
| 401 Unauthorized on board | PAT scopes wrong — must include `repo` + `project` |
| App doesn't nudge new issues | Check `APP_INSTALLATION_ID` and that App is installed on the right repo |
| Cross-repo sections empty in dashboard | `APP_TOKEN` missing on PMO repo |
| Validator opens issue immediately | `project-config.yml` not edited from defaults |
| Many issues in daily audit | Engineering not applying labels — process discipline issue |
| Rate limit errors | Migrate from PAT to App installation token |

---

## Maintaining the system

| Task | Frequency |
|---|---|
| Rotate PAT | Annually |
| Rotate App private key | Annually |
| Review unlabeled audit reports | Weekly (PMs) |
| Update golden project | As needed |
| Update `pm-template` | As needed |

When the template updates: existing PMO repos don't auto-update. Either PMs manually copy new files, or provide a sync workflow (out of scope for v1).
