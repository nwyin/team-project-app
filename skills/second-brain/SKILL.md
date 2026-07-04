---
name: second-brain
description: Capture, triage, search, and write back to the shared second brain via the brain CLI. Use when a user shares links, notes, or screenshots to save; asks questions answerable from the knowledge base; requests research or content drafts; or runs the weekly review.
---

# second-brain

Everything goes through the `brain` CLI. It is the only interface to the knowledge base.

## Invocation: local vs remote

If the environment variable `BRAIN_SSH_HOST` is set, run **every** command below as
`ssh $BRAIN_SSH_HOST brain …` instead of `brain …`. This is how local harnesses
(Claude Code, Codex) on a laptop drive the VPS. Example:

```
ssh $BRAIN_SSH_HOST brain search "standing desk research" --user alice
```

## Resolving --user

Every read/write takes `--user <name>`. Resolve it from who is talking:

- Telegram: run `brain user list` and match the sender's Telegram id/handle to a name.
- Local harness: the machine's owner, from local config.

```
brain user list
```

Never guess. If you cannot find a match, ask.

## Spaces and RBAC conventions

- Default space is the caller's personal inbox (`personal:<user>`) — `brain item add`
  without `--space` already does this.
- Use `--space shared` only when the user explicitly asks to share.
- Never read or write another user's personal space.

```
brain space list
brain space add project-x --member alice --member bob
```

## Workflow: capture

Shared links, text, and screenshots become items in the sender's personal space.
For images, first extract the text yourself (vision), then save the extraction.

```
brain item add --user alice --kind link --source "https://example.com/article" --title "Article on sleep" --body "Key points: ..."
brain item add --user alice --kind note --body "Random thought: try the standing desk for a month"
brain item add --user alice --kind screenshot --source "telegram" --title "Whiteboard photo" --body "OCR extraction of the whiteboard text" --attach /tmp/whiteboard.jpg
```

Long body from a file or heredoc goes through stdin:

```
brain item add --user alice --title "Meeting notes" --body -
```

## Editing items

Fix a mistake or add detail to an existing item without losing history (the old
version is recorded in `item_history`):

```
brain item edit 42 --user alice --body "corrected text"
```

## Workflow: Q&A

Search first, read the top items, answer citing item IDs.

```
brain search "standing desk" --user alice --json
brain item get 42 --user alice
```

Cite like: "…improves focus (item 42, item 17)". If search returns nothing useful, say so —
do not invent knowledge-base content.

## Workflow: research

Use your web tools to research, then write findings back so they are searchable later:

```
brain search "existing notes on sleep supplements" --user alice
brain item add --user alice --kind research --title "Sleep supplement research 2026-07" --source "web" --body -
```

## Workflow: content drafting

Draft long-form, short-form, or scripts from knowledge-base topics. Pull source material
with `brain search`, then save the draft as an item so it can be iterated on:

```
brain search "productivity experiments" --user alice --limit 20
brain item add --user alice --kind draft --title "Newsletter: what a month of standing taught me" --body -
```

## Workflow: weekly review

1. List each user's inbox.
2. Propose triage: which items to move to `shared` or a project space, which to archive.
3. Apply **only after confirmation**:

```
brain item list --user alice --space personal:alice
brain item move 42 --user alice --space shared
brain item archive 17 --user alice
```

4. Summarize the week: new items, open questions, suggested follow-ups.

## Making a write show up immediately

Sync to Notion/Drive normally runs on a 15-minute cron. If the user is waiting to
see a write ("save this and share it", "add this to the doc"), sync right after:

```
brain item add --user alice --space shared --title "Q3 roadmap" --body - && brain sync notion && brain sync drive
```

This is optional — the cron covers it eventually either way.

## Maintenance commands (cron uses these; rarely needed interactively)

```
brain sync notion
brain sync drive
brain index
```
