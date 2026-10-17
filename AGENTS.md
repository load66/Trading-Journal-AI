# Agent rules

This file is the single source of truth for every coding agent working in this repository (Claude Code, ChatGPT/Codex, others). Agent-specific files such as `CLAUDE.md` only point here and add tool-specific notes; they never add or relax rules.

## Multi-agent coordination

- Only one agent works on this repository at a time.
- Before starting, read the **Active agent** line in `docs/AUDIT_PROGRESS.md`. If it names another agent, stop and ask the user instead of starting work.
- When starting, set **Active agent** to your agent name, the release and your branch, and commit that on your branch with your first change.
- Use branch prefixes `claude/` (Claude Code) or `codex/` (ChatGPT/Codex). Never push work to another agent's branch.
- When done, update the **Handoff** section (deployed versions, in progress, exact next step) and set `Active agent: none`, bundled with your final change on the release branch. No standalone checkpoint commits to `main`.
- `docs/AUDIT_PROGRESS.md` is public-safe: versions, commit hashes, PR links, sanitized issue titles and test status only. Never account names, numbers, amounts, transactions, secrets, logs, or details of unfixed security issues (write "Security review pending").

## Scope

This repository also hosts the trading journal. Finance work (BonusTracker Finances backend: `backend/bonus_simplefin.py`, `bonus_plaid_accounts.py`, `bonus_plaid_trial.py`, `finance_category_rules.py`, `finance_income_rules.py`, `finance_account_management.py` and their tests) must not modify trading journal features. The frontend lives in `load66/bonus-tracker`; its `AGENTS.md` carries the same coordination rules.

## Financial data safety

- Never delete, reset or overwrite real financial history. Migrations are additive and recoverable. Empty provider responses never erase history.
- Manual user decisions (categories, confirmed transfers, rules) always override automation and persist through sync, reconnect and deploy.
- Never call real provider APIs in tests: mock SimpleFIN, Plaid and Groq.
- Plaid Trial has a fixed lifetime Item allowance. Never create a new Item; use update mode for reconnects. Anything that would need a new Item is reported as a blocker.
- Groq quota is shared with the trading journal: no bulk or live re-categorization; keep cached classifications.
- Never log tokens, secrets or private transactions. Never change Railway environment variables or print secrets. Never weaken auth to make tests pass.

## Releases

- Fix root causes with a failing-before/passing-after regression test that checks real state and money results.
- Railway auto-deploys `main`. Merge only passing code, then confirm the Railway deploy succeeded on the expected commit. Deploy backend before the matching frontend release.
