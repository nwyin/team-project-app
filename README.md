# Second Brain

A shared knowledge base for a small set of trusted users, driven by
[hermes-agent](https://hermes-agent.nousresearch.com) as the harness. We maintain three
things (see `spec.md` for the full design):

1. **`brain`** — Python CLI owning the SQLite source-of-truth DB (schema, search, embeddings).
2. **Sync layer** — `brain sync notion` / `brain sync drive`, two-way, run from hermes cron.
3. **`second-brain` skill** — `skills/second-brain/SKILL.md`, teaches the agent to capture,
   triage, search, and write back through the CLI.

## Development

```bash
uv sync
uv run pytest          # offline; Notion/Drive/LLM are faked
uvx ruff check
uvx ruff format --check
uvx ty check
```

## VPS runbook

**Deploying on Railway?** Follow `SETUP.md` instead — a guided walkthrough (Railway
service + volume via the repo `Dockerfile`, plus every service token). The steps below
are for a classic VPS with systemd and plain SSH.

Any 1 vCPU / 1 GB box. No GPU — all inference is remote API calls.

### 1. hermes-agent

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes setup                      # interactive: LLM provider + API key -> ~/.hermes/.env
hermes gateway install            # writes and manages its own systemd unit — do not hand-write one
hermes gateway start
```

### 2. Telegram

- Create a bot with @BotFather, put the token in `~/.hermes/.env` as `TELEGRAM_BOT_TOKEN`.
- Allowlist each user: `TELEGRAM_ALLOWED_USERS=<numeric ids>` in `.env`, **or** have each
  user DM the bot and approve with `hermes pairing approve telegram <code>`.

### 3. brain

```bash
mkdir ~/brain && cd ~/brain
cp /path/to/repo/config.example.toml config.toml   # set users, embeddings, tokens
uv tool install /path/to/repo                      # installs the `brain` command
brain migrate                                      # creates brain.db, seeds users/spaces
```

Set `BRAIN_DIR=~/brain` in the environment cron and the agent run under (or always
`cd ~/brain` first, as the cron job does).

### 4. Notion

1. Create an **internal integration** at notion.so/my-integrations; copy the `ntn_…` token
   into `config.toml` under `[notion]`.
2. For each space you want synced: create a Notion database, share it with the integration
   (page ••• menu → Connections), and register it:

```bash
brain space set-target shared --notion-db <database-id>
```

There is no API for public/guest sharing — sharing a page with externals stays a manual
step in the Notion UI.

### 5. Google Drive

OAuth **user** credentials, not a service account (service accounts have no storage quota
and cannot own files). One Drive identity: all synced folders live in the primary
account's My Drive, shared with the other users.

1. In Google Cloud console: create a project, enable the Drive API, create OAuth desktop
   credentials, and set the consent screen to **In production** (Testing mode expires
   refresh tokens every 7 days and silently kills the sync).
2. On the laptop, run the one-time consent flow with the full `drive` scope
   (`InstalledAppFlow.run_local_server()` from `google-auth-oauthlib`), save the resulting
   credentials JSON, and copy it to the VPS as `~/brain/drive-token.json`.
3. Register a folder per synced space:

```bash
brain space set-target shared --drive-folder <folder-id>
```

### 6. Skill + cron

```bash
cp -r /path/to/repo/skills/second-brain ~/.hermes/skills/   # no registration step
# then create the two jobs in cron/README.md
```

### 7. Local harness access (Claude Code / Codex)

On each laptop:

1. SSH key access to the VPS; add a host alias to `~/.ssh/config`.
2. `export BRAIN_SSH_HOST=<alias>` (e.g. in the shell profile).
3. Claude Code: symlink or copy `skills/second-brain/` into `~/.claude/skills/`.
   Codex: reference the same SKILL.md from `AGENTS.md`.

The skill runs every command as `ssh $BRAIN_SSH_HOST brain …` when the variable is set —
no other interface exists.

## Health checks

```bash
hermes gateway status   # gateway down = cron down
hermes cron status
```

Manual real-API verification: `SMOKE.md`.
