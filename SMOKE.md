# Smoke checklist (manual, real APIs)

Run after deploy or dependency bumps. pytest covers logic offline; this covers the real
integrations that tests fake.

- [ ] **Capture a link**: DM the Telegram bot a URL with a comment; confirm
      `brain item list --user <you>` shows it with `kind=link` and the source URL.
- [ ] **Capture a screenshot**: send a photo with text; confirm the saved item body
      contains the OCR extraction and the image landed under `files/<item-id>/`.
- [ ] **Ask a question**: ask the bot something answerable from the KB; confirm the answer
      cites item IDs that exist.
- [ ] **Notion sync**: edit a synced Notion row, run `brain sync notion`; confirm the local
      item updated. Edit locally, sync again; confirm Notion updated. Trash the Notion row,
      sync; confirm the local item is archived (not deleted).
- [ ] **Drive sync**: drop a `.md` file into a synced folder, run `brain sync drive`;
      confirm it appears as an item. Edit the item locally, sync; confirm the file updated.
- [ ] **Google Doc import**: create a native Google Doc in the folder, sync; confirm it
      imports as a read-only markdown item.
- [ ] **Weekly review**: trigger the weekly review job (`hermes cron list`, then trigger);
      confirm a digest arrives on Telegram and proposed triage waits for confirmation.
- [ ] **Local harness**: from a laptop with `BRAIN_SSH_HOST` set, run
      `ssh $BRAIN_SSH_HOST brain search "anything" --user <you>` and confirm results;
      then drive it through Claude Code with the skill loaded.
- [ ] **Cron health**: `hermes cron status` shows both jobs scheduled and the gateway up;
      `hermes cron list` shows the sync job ran within the last 15 minutes.
- [ ] **429 behavior**: bulk-import a legacy Notion inbox (first sync of a big space);
      confirm sync completes without crashing on rate limits.
