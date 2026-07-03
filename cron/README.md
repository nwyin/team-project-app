# Cron jobs

Two jobs, created once on the VPS with `hermes cron create`. They live in
`~/.hermes/cron/jobs.json` and tick **inside the gateway process** — if the gateway is
down, cron is down (`hermes cron status` to check).

## 1. Sync every 15 minutes (no-agent script job)

Runs the sync + reindex pipeline directly, no LLM involved:

```bash
hermes cron create "every 15m" "cd ~/brain && brain sync notion && brain sync drive && brain index" --no-agent
```

## 2. Weekly review, Sundays (prompt job, delivered to Telegram)

A prompt job that runs the agent with the `second-brain` skill and messages the result:

```bash
hermes cron create "every sunday at 17:00" "Run the weekly review from the second-brain skill: list both users' inboxes, propose triage into shared/project spaces, and summarize the week. Deliver to Telegram."
```

Verify flags against `hermes cron create --help` on the installed version — the schedule
grammar and `--no-agent` spelling are the two things most likely to differ across releases.

After creating both:

```bash
hermes cron list
hermes cron status
```
