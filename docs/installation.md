# Install Lemma on your computer

Lemma Desktop runs the whole of Lemma on your own machine. You don't need
Docker, Python, Postgres or anything else; the app installs what it needs.

To run Lemma for a team on a server instead, see [self-hosting](self-hosting.md).

## Supported systems

| Platform | Minimum | Status |
| --- | --- | --- |
| macOS | macOS 14, Apple silicon | Supported |
| Windows | Windows 11 23H2, x86-64 | Experimental |

Intel Macs, Windows on Arm and Linux desktops are not supported yet. Leave
about 12 GB free for the runtime, plus room for your own data.

## Install on macOS

1. Download Lemma from [lemma.work/download](https://lemma.work/download).
2. Open the DMG and drag **Lemma** into **Applications**.
3. Open Lemma from Applications, choose **Use Local Lemma**, and select
   **Install local services**.

The first start downloads and verifies the rest of the runtime, so it takes a
few minutes and needs an internet connection. Progress is shown as it goes, and
an interrupted download picks up where it stopped.

If macOS asks for **Local Network** access, allow it: Lemma uses it to reach the
small virtual machine it runs on. If setup says it cannot connect, check
**System Settings → Privacy & Security → Local Network**, then select **Try
again**. Your data is not affected.

## Install on Windows (experimental)

The Windows installer is built on every release but not published yet, because
it hasn't been tested end to end. To try it:

1. Open the latest **Release Lemma Desktop** run in
   [GitHub Actions](https://github.com/lemma-work/lemma-platform/actions/workflows/release-desktop.yml).
2. Download the `lemma-desktop-windows-<version>` artifact, unzip it, and run
   the installer.
3. Open Lemma, choose **Use Local Lemma**, and select **Install local services**.

Lemma runs in its own private WSL2 distribution and leaves your existing WSL
setup alone. If WSL2 isn't enabled, Lemma asks for permission to turn it on;
restart if asked, and setup resumes.

## Create your account

When Lemma says it's ready, select **Create account**. The first account on an
installation is always let in. No email setup is needed for this.

## Set up the server

Some features need a key that only you can supply. After you sign up, Lemma
shows a short checklist. The same list lives in **Settings → This Mac → Server
setup** (This PC on Windows), where each item shows whether it's ready and has a
**Test** button.

| Item | What it's for | What you need |
| --- | --- | --- |
| **AI model** (required) | Everything an agent does | A provider and its key |
| **Email** | Sending invitations, password resets and sign-in codes | A Resend API key, or an SMTP server |
| **Connectors** | Gmail, GitHub, Slack, Notion and more | A Composio key, or your own OAuth app |
| **Channels** | Talking to teammates in Telegram, Slack, email, WhatsApp and Teams | A bot token or app token |
| **Voice** | Voice notes | A Deepgram key |
| **Web search** | Looking things up | Nothing; a Brave Search key is optional |

### AI model

Pick a provider (OpenAI, Anthropic, OpenRouter, or any OpenAI- or
Anthropic-compatible endpoint), enter its key, and select **List models**. Choose
the model agents use. You can also add a cheaper fast model for titles and
summaries, and a model that can read images if your main one can't.

No API key? Start [Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai)
on the same machine. Lemma finds them automatically and needs no key.

Keys are stored in macOS Keychain or Windows Credential Manager. More providers
can be added for your organization in **Settings → Models**.

### Email

Without email, you can still invite people by sending them the invitation link
yourself. To send email, either verify a domain in Resend and enter its API key,
or enter your SMTP server details (for Gmail, use an app password). **Send a test
email** checks it works.

### Connectors and channels

Telegram and Slack work without a public address. WhatsApp and Teams need one,
so they only work while you're [sharing Lemma publicly](#share-it-with-others).
Attach a channel to a teammate in **Agent Surfaces**.

## Run agents on your Claude Code or Codex login

You can run agents on the Claude Code, Codex, Cursor or OpenCode subscription
you already have, instead of paying for API usage.

In Lemma, open **Models**, select **Connect this computer**, then **Add to chat
models** on the coding agents it finds. To point an agent at one from the CLI:

```bash
lemma runtime profiles list
lemma agent update <name> --data '{"agent_runtime": {"profile_id": "<profile_id>"}}'
```

## Share it with others

By default only you can open your local Lemma. To let others in, open
**Settings → This Mac → Sharing**:

- **Local network** gives you a URL and QR code for people on the same Wi-Fi. Use
  it only on a network you trust.
- **Public link** puts Lemma on the internet through your own ngrok or Cloudflare
  account.

Sharing is **invite-only** by default: only people you've invited can create an
account. You can switch it to **open**. Sharing stops when you quit Lemma. Pod
apps stay local-only for now. See [Desktop security](architecture/desktop-security.md)
for details.

## Closing and quitting

**Closing the window** keeps Lemma running in the menu bar (or tray): schedules
fire, agents keep working, and shared links stay up.

**Quitting** (⌘Q) stops everything. Lemma tells you first if anything is running.
Your data stays on your machine either way.

## Using the CLI

Install the `lemma` CLI and connect it to your local Lemma:

```bash
uv tool install lemma-terminal
curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.sh | bash -s -- --cli-only
lemma servers select local
lemma auth login
```

Use `uv`, not `pip`: the CLI needs Python 3.14, and `uv` installs it for you.
The second line installs `lemma-stack`, which controls the local server and
tells the `lemma` CLI where to find it.

```bash
lemma-stack status           # what is running
lemma-stack restart          # restart without losing data
lemma-stack logs backend --follow
lemma-stack doctor           # check for problems
```

## Troubleshooting

| Problem | Try |
| --- | --- |
| Setup can't connect to local services | Allow Local Network access (see above), then check VPN or firewall rules |
| Agents answer "Set up an AI model" | Add one in **Settings → This Mac → Server setup** |
| `Server not found: local` in the CLI | `lemma-stack self register-cli --use` |
| The CLI and the app disagree | `lemma doctor` checks for version mismatches |
| Something won't start | `lemma-stack doctor`, then the logs |

Logs are in **Settings → This Mac → Server setup → Advanced**, with passwords and
keys removed. **Troubleshoot** in the tray menu can restart Lemma or repair its
runtime files without touching your data.

## Updates

### Checking for updates

Lemma checks for updates when you open **Settings → This Mac → Updates** and
select **Check now**, and once on its own about 20 seconds after it starts
(unless updates are turned off, or it was started in Recovery). A newer version
shows as a menu item; nothing is downloaded until you choose it. It fetches one
file:

```
https://github.com/lemma-work/lemma-platform/releases/latest/download/latest.json
```

Nightly builds read `.../releases/download/desktop-nightly/latest.json` instead.
The request carries nothing about your machine. Updates are signature-checked
before they're installed, and they never reset your data.

## Anonymous install health

Official builds send a few anonymous events about whether the app started and
installed correctly: `desktop.launched`, `desktop.runtime_install`,
`desktop.runtime_ready`, `desktop.mode_selected` and `desktop.quit`. Each carries
only the OS, architecture, app version and a rough timing. Nothing else about
your machine, and nothing about your pods, organization, users or files, is sent.

Events go to `https://eu.i.posthog.com` under a random ID created for this
installation. To turn it off, use the switch in **Settings → This Mac → Server
setup → Advanced**, or set `LEMMA_TELEMETRY=0`. Builds you make yourself never
send it.

## Your data and uninstalling

Your data lives in:

- macOS: `~/Library/Application Support/Lemma`
- Windows: `%LOCALAPPDATA%\Lemma`

Local doesn't mean offline: the AI provider, connectors and web search you set up
still receive what you send them.

To uninstall, quit Lemma, back up anything you want to keep, and delete the app.
Delete the folder above only if you also want to erase your data.
