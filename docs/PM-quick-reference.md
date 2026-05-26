# PM Quick Reference

Print and keep next to your screen.

## Where to look

| Want to know... | Look at... |
|---|---|
| Project health right now | 📈 Project Dashboard (pinned issue) |
| What we're working on this week | Currently In section of dashboard |
| What's overdue | Items Past Planned End section |
| What's coming up | Earliest Planned End in Phase Progress |
| Invoice pipeline | Invoice Milestones section, or Milestones tab |
| Last Monday's status | `status-history/timeline.md` |
| What it looked like on date X | `status-history/X.md` |
| Sub-tasks of a phase | Open the phase issue → Sub-issues section |

## Where to update

| Want to update... | Update on... |
|---|---|
| Planned/Actual dates | Project board card fields |
| Status (Todo/In Progress/Done) | Project board card field |
| Sprint assignment | Project board Sprint field |
| % Complete | Project board card field |
| Issue title/body content | The issue itself |
| Invoice details | The milestone (Milestones tab) |

## When to create a new issue

| Scenario | Template |
|---|---|
| Risk identified | ⚠️ Risk |
| Defect found | 🐞 Bug |
| Scope/budget change | 🔄 Change Request |
| Friday status | 📊 Weekly Status Report |
| New sub-task in a phase | Open parent → "+ Add sub-issue" |

## Tips that save time

- **Set Actual Start the moment work begins.** Without it the dashboard can't compute true variance.
- **Close issues, don't just mark Done.** Closing triggers GitHub's native progress counters.
- **Use sub-issues for hierarchies, not body checklists.** GitHub displays sub-issue progress natively.
- **Don't edit auto-managed issues.** Anything labeled `auto-managed` gets overwritten on next refresh.
- **Bookmark "Parents only".** Available in the Dashboard's Quick Views — way cleaner than the default Issues view.

## When something feels off

- **Dashboard hasn't refreshed?** Actions → 🔄 Dashboard update → Run workflow. Manual refresh in ~10 seconds.
- **Pinned issue is wrong?** Dashboard workflow manages pins. Trigger a manual run.
- **Sub-issue counts wrong?** Make sure the issue was created as a real sub-issue (parent → "+ Add sub-issue"), not just labeled.
- **Phase shows "Empty"?** That phase's parent has no sub-issues yet. Add some via the parent's "+ Add sub-issue" button.

## End-of-phase checklist

When closing out a phase:

1. [ ] All sub-issues closed
2. [ ] Actual End set on all sub-issues
3. [ ] Phase parent's Actual End set on the board
4. [ ] Any related invoice milestone is also closed (if applicable)
5. [ ] Close the phase parent issue → dashboard auto-advances Currently In to next phase
