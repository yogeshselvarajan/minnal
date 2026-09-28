---
inclusion: always
---

# Git workflow and documentation

- Trunk-based: short-lived branches `feat/<spec>-<task>`, `fix/<short>`, `chore/<short>`; merge within a day.
- **Conventional Commits**: `feat(grid-tools): trace upstream device (R2.1, P4)`. Types: feat, fix, refactor, test, docs, chore, ci, perf, build.
- One logical change per commit; never mix formatting with behaviour.
- Commit messages cite requirement IDs; breaking changes use `!` and a `BREAKING CHANGE:` footer.
- ADRs in `docs/adr/NNNN-title.md` (context, decision, alternatives, consequences, sources).
- Runbooks in `docs/runbooks/`; evidence for the challenge in `docs/evidence/`.
- Challenge rule: **no commits after 5 Oct 23:59 PT until judging ends (19 Oct)**.
