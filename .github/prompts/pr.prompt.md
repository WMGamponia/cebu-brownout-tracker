---
description: 'Write the PR title and description for the current branch'
agent: 'agent'
---

Write a pull request title and description for my current changes.

1. Gather the changes: `git branch --show-current`, `git log --oneline main..HEAD`, `git diff main...HEAD --stat`, then `git status` for uncommitted work. Read the diff of code and hand-edited files; for fixtures (`tests/fixtures/posts/*.html`) use `--stat` only.
2. Pick the type: a `phaseN-…` branch → Phase; `fix/` → Fix; otherwise infer it from the conventional-commit prefixes in the log.
3. Title: `[Phase 1] Ingest, parse, store and backfill`, `[Fix] …`. Imperative, under 72 characters.
4. Body: fill in `.github/pull_request_template.md`.
   - Describe changes by file path, grouped by area.
   - Fill "Results" from the commit bodies and the `ROADMAP.md` progress log (backfill numbers). Never invent a number.
   - Tick a checklist item only if the diff shows it's true; otherwise leave it unticked with a short note.
   - List anything deferred, as recorded in `ROADMAP.md` known issues.
5. No `Co-Authored-By:` lines, no "Generated with …" footer, no AI attribution of any kind.

Output: the whole result inside **one** fenced code block tagged `markdown` — the title as the first line (`# <title>`), then the body. Under 4000 characters.
