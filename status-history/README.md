# Status History

This folder is auto-populated by two workflows:

- **`YYYY-MM-DD.md`** — daily snapshots, committed at 02:00 UTC by the `📅 Daily snapshot` workflow.
  Each file is a frozen-in-time copy of that day's dashboard. Scroll back through them to see exactly what the project looked like on any given day.

- **`timeline.md`** — single rolling file, appended every Monday at 09:00 UTC by the `📈 Weekly summary` workflow.
  One row per week. Acts as a compact EKG of project health over time — paste sections into status decks for sponsors.

Both files are managed by automation. Don't edit them manually — your changes will be lost on the next run.

To trigger either workflow manually: Actions tab → pick the workflow → Run workflow.
