# Claude Code

Follow `AGENTS.md` and `docs/AUDIT_PROGRESS.md`. `AGENTS.md` is the single source of truth for all rules; this file adds nothing that overrides it.

Claude-specific:
- Branch prefix: `claude/`. When starting, set `Active agent: claude` in `docs/AUDIT_PROGRESS.md` on your branch; set it back to `none` with the final Handoff update.
- If interrupted, start the next reply with what is deployed, what is in progress and the exact next step, after re-reading the checkpoint and verifying the actual GitHub and Railway state.
