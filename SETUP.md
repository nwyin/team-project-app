# SETUP — Railway deploy + service tokens, step by step

A guided setup: run through it top to bottom with an assistant (Claude Code with this repo
open works well — it can run every command here while you do the browser steps and paste
tokens). Each step says who does what: **[you]** = browser/phone work only you can do,
**[terminal]** = commands the assistant can run.

What you'll have at the end: hermes-agent running 24/7 on Railway with the `second-brain`
skill, a Telegram bot both users can talk to, `brain` syncing Notion and Drive every 15
minutes, and laptop access via `railway ssh`.

Researched 2026-07 against Railway docs, hermes docs, and each provider's current console.
UI click-paths drift — if a menu isn't where this says, it moved, not disappeared.

---

## 0. Prerequisites

- A GitHub account (Railway deploys this repo from GitHub) with this repo pushed to it.
- Railway account on the **Hobby plan ($5/mo)** — the free/trial tier cannot set the
  `Always` restart policy, which an always-on gateway needs. Sign up at railway.com.
- The two-ish humans who'll use the system, reachable on Telegram.
- Decisions to make now (the assistant should ask):
  1. User names for `config.toml` (short, lowercase: `alice`, `bob`).
  2. LLM provider: OpenRouter (any model, one key) or Anthropic (Claude only).
  3. Which Google account owns the synced Drive folders (the "primary account" —
     one OAuth identity, folders shared to the other user).

## 1. Railway project [you + terminal]

1. **[you]** railway.com → New Project → "Deploy from GitHub repo" → pick this repo.
   Railway detects the `Dockerfile` at the repo root and uses it (base image
   `nousresearch/hermes-agent` + the `brain` CLI baked in).
2. **[you]** Service → **Settings**:
   - **Restart policy**: `Always` (Hobby+ only; default `On Failure` gives up after 10 crashes).
   - **Custom start command**: leave empty — the Dockerfile's `CMD ["gateway", "run"]` runs
     the gateway in the foreground under the container's s6 init. (Never `hermes gateway
     install`; there's no systemd in a container.)
   - **Networking**: no public domain, no TCP proxy — the gateway makes outbound calls only.
     Leave **App sleeping / serverless OFF** (it's opt-in; a sleeping gateway is a dead bot).
3. **[you]** Service → **Volume** (right-click service → Attach volume): mount path
   `/opt/data`. This holds ALL state: hermes config, sessions, skills, and the brain DB
   (`/opt/data/brain`). One volume per service is a Railway hard limit, which is why they share.
4. **[terminal]** Install the CLI and link the project:

```bash
npm i -g @railway/cli    # or: brew install railway
railway login
railway link             # pick the project + service
```

5. If the deploy log shows permission errors on `/opt/data`: **[you]** add the service
   variable `RAILWAY_RUN_UID=0` (Railway runs non-root images against volumes badly;
   the hermes image remaps its internal user itself and expects to start as root).

## 2. LLM provider key [you + terminal]

Pick one:

- **OpenRouter**: openrouter.ai → sign in → Settings → **Keys** (openrouter.ai/settings/keys)
  → Create Key. Prefix `sk-or-v1-…`. Buy a small credit pack first (prepaid; keys 402 without credit).
- **Anthropic**: console.anthropic.com → Settings → **API keys** → Create Key. Prefix
  `sk-ant-…`. Requires adding billing/credits before first use.

**[terminal]** Set it (assistant: ask the user to paste the key, don't echo it back):

```bash
railway variable set OPENROUTER_API_KEY=sk-or-v1-...   # or ANTHROPIC_API_KEY=sk-ant-...
```

## 3. Telegram bot [you + terminal]

1. **[you]** In Telegram, message **@BotFather** (the verified one) → `/newbot` →
   pick a display name, then a username ending in `bot` (e.g. `ourbrain_bot`).
   BotFather replies with the token: `<numeric-id>:<secret>` (e.g. `110201543:AAHdqT…`).
   Treat it like a password (`/revoke` regenerates it if leaked).
2. **[terminal]**

```bash
railway variable set TELEGRAM_BOT_TOKEN=110201543:AAHdqT...
```

3. Allowlist both users, either way:
   - **IDs up front**: each user messages **@userinfobot** to get their numeric ID, then
     `railway variable set TELEGRAM_ALLOWED_USERS=111111111,222222222`.
   - **Pairing codes**: skip the variable; after step 4 each user DMs the bot, gets a
     pairing code, and you approve it: `railway ssh -- hermes pairing approve telegram <code>`.

(Privacy mode / group settings don't matter — this bot is DM-only.)

## 4. hermes setup + first boot [terminal]

Variables set → redeploy → configure interactively inside the container:

```bash
railway up --detach                        # or push to GitHub; Railway auto-deploys
railway ssh                                # interactive shell in the running container
  hermes setup                             # wizard: confirm provider/model, Telegram on
  exit
railway ssh -- hermes gateway status       # should be running
```

`hermes setup` writes to `/opt/data` (the volume), so it survives redeploys. Env vars you
set in Railway override `.env` values, so tokens stay in Railway variables.

**Checkpoint**: DM the bot from an allowlisted account — it should answer.

## 5. brain init [terminal]

```bash
railway ssh -- mkdir -p /opt/data/brain
cat <<'EOF' | railway ssh -- bash -c 'cat > /opt/data/brain/config.toml'
users = ["alice", "bob"]

[embeddings]
model = "openai/text-embedding-3-small"
# api_base = "https://openrouter.ai/api/v1"

[notion]
token = "REPLACED-IN-STEP-6"

[drive]
token_path = "drive-token.json"
EOF
railway ssh -- brain migrate
railway ssh -- brain space list            # expect personal:<each user> + shared
```

(`BRAIN_DIR=/opt/data/brain` is baked into the image, so `brain` works from anywhere.)

## 6. Notion [you + terminal]

1. **[you]** notion.so/profile/integrations (redirects from notion.so/my-integrations) →
   **New integration** → name it, pick the workspace, type **Internal** → under
   Capabilities enable **Read**, **Insert**, and **Update content** → copy the token
   (prefix `ntn_…`).
2. **[terminal]** Put the token into `/opt/data/brain/config.toml` under `[notion]`
   (edit via `railway ssh`, same pattern as step 5).
3. **[you]** For each space to sync, create a Notion **database** (full page, not inline),
   then connect it: open the database → `•••` menu (top right) → **Connections** →
   **Add connections** → pick your integration (this also grants access to child pages).
4. **[you]** Copy each database ID: it's the 32-char hex string in the URL after the
   workspace slug, before the `?` (`notion.so/<workspace>/<THIS-PART>?v=…`).
5. **[terminal]**

```bash
railway ssh -- brain space set-target shared --notion-db <database-id>
railway ssh -- brain sync notion           # first sync ingests everything already in the DB
```

## 7. Google Drive [you + laptop terminal]

OAuth **user** credentials for the primary account — not a service account (they can't own
files). The consent flow needs a browser, so it runs on a laptop; only the resulting token
JSON goes to Railway.

1. **[you]** console.cloud.google.com → project picker → **New Project**.
2. **[you]** APIs & Services → **Library** → search "Google Drive API" → **Enable**.
3. **[you]** APIs & Services → **Google Auth Platform** (the old "OAuth consent screen" —
   it's a 4-tab wizard now: Branding, Audience, Data Access, Clients):
   - Branding: name the app, set your email.
   - Audience: **External**.
   - Data Access: add the scope `https://www.googleapis.com/auth/drive` (full Drive —
     `drive.file` can't see files humans drop into folders).
   - Audience tab → **Publish app** → status **In production**. Do not skip: in Testing,
     refresh tokens die every 7 days and the sync silently stops. The app stays
     unverified — that's fine for personal use (≤100 users forever); you'll click through
     one "unverified app" warning during consent (**Advanced → Go to <app> (unsafe)**).
4. **[you]** Google Auth Platform → **Clients** → Create Client → type **Desktop app** →
   download the JSON (`client_secret_….json`).
5. **[laptop terminal]** Run the consent flow as the primary account:

```bash
uv run --with google-auth-oauthlib python - <<'EOF'
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", ["https://www.googleapis.com/auth/drive"])
creds = flow.run_local_server(port=0)
open("drive-token.json", "w").write(creds.to_json())
print("wrote drive-token.json")
EOF
cat drive-token.json | railway ssh -- bash -c 'cat > /opt/data/brain/drive-token.json'
```

6. **[you]** In Drive (as the primary account): create a folder per synced space, share
   each with the other user, copy the folder ID from its URL (`drive.google.com/drive/folders/<THIS-PART>`).
7. **[terminal]**

```bash
railway ssh -- brain space set-target shared --drive-folder <folder-id>
railway ssh -- brain sync drive
```

## 8. Skill + cron [terminal]

The skill ships inside the image at `/opt/brain-src/skills`; copy it onto the volume where
hermes looks for skills, then create the two jobs (see `cron/README.md` for details):

```bash
railway ssh -- bash -c 'mkdir -p /opt/data/skills && cp -r /opt/brain-src/skills/second-brain /opt/data/skills/'
railway ssh -- hermes cron create "every 15m" "brain sync notion && brain sync drive && brain index" --no-agent
railway ssh -- hermes cron create "every sunday at 17:00" "Run the weekly review from the second-brain skill: list both users' inboxes, propose triage into shared/project spaces, and summarize the week. Deliver to Telegram."
railway ssh -- hermes cron status
```

## 9. Laptop access (Claude Code / Codex) [laptop terminal]

Railway has no plain sshd, so the `BRAIN_SSH_HOST` convention from the skill maps to
`railway ssh --` here. On each laptop:

```bash
npm i -g @railway/cli && railway login && railway link   # once
railway ssh -- brain search "anything" --user alice      # this is the pattern
```

Copy `skills/second-brain/` into `~/.claude/skills/` (Claude Code) or reference it from
`AGENTS.md` (Codex), and tell the harness to use `railway ssh -- brain …` as the command
prefix instead of `ssh $BRAIN_SSH_HOST brain …`.

(If you later want true `ssh` semantics — e.g. for scripts that can't use the Railway
CLI — a classic $5 VPS per `README.md` gives the exact `BRAIN_SSH_HOST` flow. Railway's
TCP proxy could in principle front a real sshd in the container, but that path is
undocumented and untested — not recommended.)

## 10. Verify

Run the quick end of `SMOKE.md`:

- DM the bot a link → `railway ssh -- brain item list --user alice` shows it.
- Ask the bot a question → answer cites item IDs.
- Edit the Notion row, wait ≤15m (or `railway ssh -- brain sync notion`) → local item updated.
- `railway ssh -- hermes cron status` → both jobs scheduled, gateway up.
- Redeploy (`railway up --detach`), then `railway ssh -- brain space list` — state survived
  the redeploy (it's on the volume).

## Token summary

| Credential | Where it lives | Format |
|---|---|---|
| LLM key | Railway variable `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` | `sk-or-v1-…` / `sk-ant-…` |
| Telegram bot token | Railway variable `TELEGRAM_BOT_TOKEN` | `<digits>:<secret>` |
| Telegram user IDs | Railway variable `TELEGRAM_ALLOWED_USERS` (or pairing) | comma-sep digits |
| Notion token | `/opt/data/brain/config.toml` `[notion]` | `ntn_…` |
| Drive OAuth token | `/opt/data/brain/drive-token.json` | JSON w/ refresh_token |
| Drive client secret | stays on the laptop | `client_secret_….json` |
