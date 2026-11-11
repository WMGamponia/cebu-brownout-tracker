---
description: 'Update ROADMAP.md with what this session finished, changed or learned'
agent: 'agent'
---

Update `ROADMAP.md` for the work done since its last progress-log entry.

1. Gather what changed: `git log --oneline` since the last date in the Progress log, plus `git status`. Read the commit bodies for numbers.
2. **Status**: update the current phase line if a phase started or finished (with the date).
3. **Progress log**: add one dated bullet (`- **YYYY-MM-DD** — …`) per milestone, in the existing voice — what was done, the key numbers (posts, outages, warnings, failures, test count), where the spec/plan/output lives. Don't rewrite older entries.
4. **Checkboxes**: tick finished steps with a short `→ how it was done` note.
5. **Known issues**: add anything deferred, with the post slug it affects; remove items that are now fixed.
6. **Lessons for later phases**: only lessons that would change how someone works next time (a surprise, a wrong estimate, a trap in the data or a library). One bold lead sentence, then the detail.

Use absolute dates. Never invent a number — if one isn't in the commits or reports, ask. Show me the diff, don't commit.
