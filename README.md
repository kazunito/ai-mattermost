# ai-mattermost-bot

This is a Mattermost Bot that uses the Gemini API, the Claude (Anthropic) API,
or the DeepSeek API as its backend.
Switch between them with the `LLM_PROVIDER` environment variable.

## Setup

First, create a Bot account with Mattermost's integration feature.
The bot's name should begin with "ai-".

The following is the setup procedure for launching Python scripts directly.

```bash
git clone https://github.com/kazunito/ai-mattermost-bot.git ai-mattermost-bot
cd ai-mattermost-bot/chat-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp template_script.sh script.sh
vim script.sh
./script.sh
```

The following is the setup instructions for building a Podman/Docker container.

```bash
git clone https://github.com/kazunito/ai-mattermost-bot.git ai-mattermost-bot
cd ai-mattermost-bot/chat-bot
podman build -t ai-chat-bot .
cp template_container.sh container.sh
vim container.sh
./container.sh
```

## Run as a systemd service (Linux)

The repository includes a single environment file `chat-bot/bot.env` and a
single unit file `chat-bot/mattermost-bot.service`. Choose which backend to
run by setting `LLM_PROVIDER` in `bot.env` (and the matching `AI_MODEL` /
`AI_API_KEY`).

```bash
# 1. Deploy the code to /opt/ai-mattermost-bot
sudo mkdir -p /opt
sudo git clone https://github.com/kazunito/ai-mattermost-bot.git /opt/ai-mattermost-bot
cd /opt/ai-mattermost-bot/chat-bot

# 2. Create a dedicated unprivileged user
sudo useradd -r -s /usr/sbin/nologin mmbot

# 3. Prepare the Python venv
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt

# 4. Edit the env file, then lock it down
sudo vim bot.env
sudo chmod 600 bot.env
sudo chown -R mmbot:mmbot /opt/ai-mattermost-bot

# 5. Install the unit file and start the service
sudo cp mattermost-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mattermost-bot

# Switching providers later: edit bot.env (LLM_PROVIDER / AI_MODEL / AI_API_KEY),
# then restart
sudo systemctl restart mattermost-bot

# Check status / logs
sudo systemctl status mattermost-bot
sudo journalctl -u mattermost-bot -f
```

`bot.env` contains the API key and the bot token — keep it out of version
control (see `.gitignore`) and restrict its permissions to the service user.

## Config

Set the following environment variables.
Please refer to `bot.env`, `template_script.sh` and `template_container.sh`.

| Name | Required | Example |
| ---- | ---- | ---- |
| MATTERMOST_URL | yes | matter.example.com |
| MATTERMOST_PORT | yes | 443 |
| MATTERMOST_API_PATH | yes | /api/v4 |
| SCHEME | yes | `http` or `https` |
| BOT_TOKEN | yes | xxxxxxxx |
| MATTERMOST_ORIGIN | no (default: `SCHEME + MATTERMOST_URL`) | <https://matter.example.com> |
| LLM_PROVIDER | no (default: `gemini`) | `gemini`, `claude` or `deepseek` |
| AI_MODEL | yes | `gemini-1.5-flash` / `claude-sonnet-4-6` / `deepseek-chat` |
| AI_MAX_TOKENS | no (default: 4096, Claude/DeepSeek only) | 4096 |
| AI_API_KEY | yes | yyyyyyyy |
| DEEPSEEK_BASE_URL | no (default: `https://api.deepseek.com`, DeepSeek only) | <https://api.deepseek.com> |
| AWS_ALERT_CHANNEL_IDS | no (default: empty — feature disabled) | `abc123...,def456...` |
| AWS_ALERT_RATE_LIMIT | no (default: `5`; `0` disables) | `5` |
| AWS_ALERT_RATE_WINDOW_SECONDS | no (default: `600`) | `600` |

### Example `bot.env` per provider

Gemini:

```env
MATTERMOST_URL=matter.example.com
MATTERMOST_PORT=443
MATTERMOST_API_PATH=/api/v4
SCHEME=https
BOT_TOKEN=xxxxxxxx

LLM_PROVIDER=gemini
AI_MODEL=gemini-1.5-flash
AI_API_KEY=yyyyyyyy
```

Claude:

```env
MATTERMOST_URL=matter.example.com
MATTERMOST_PORT=443
MATTERMOST_API_PATH=/api/v4
SCHEME=https
BOT_TOKEN=xxxxxxxx

LLM_PROVIDER=claude
AI_MODEL=claude-sonnet-4-6
AI_MAX_TOKENS=4096
AI_API_KEY=zzzzzzzz
```

DeepSeek:

```env
MATTERMOST_URL=matter.example.com
MATTERMOST_PORT=443
MATTERMOST_API_PATH=/api/v4
SCHEME=https
BOT_TOKEN=xxxxxxxx

LLM_PROVIDER=deepseek
AI_MODEL=deepseek-chat
AI_MAX_TOKENS=4096
AI_API_KEY=wwwwwwww
# DEEPSEEK_BASE_URL=https://api.deepseek.com
```

To enable [AWS SNS alert handling](#aws-sns-alert-handling-optional) on
top of any provider, append the following to `bot.env`:

```env
# Channel IDs that receive AWS SNS webhooks (comma-separated).
AWS_ALERT_CHANNEL_IDS=<channel-id-1>,<channel-id-2>
# At most 5 auto-replies per 600s per channel.
AWS_ALERT_RATE_LIMIT=5
AWS_ALERT_RATE_WINDOW_SECONDS=600
```

## Usage

The bot uses the channel header as a system prompt.
Please edit the header first.

Mention the bot and it will respond with a thread.
If you reply to the thread, you can continue the conversation.
You do not need to re-mention the bot at this time.

<!-- TODO: replace with a new chat sample image -->

## AWS SNS alert handling (optional)

In addition to regular chat, the bot can act as an AWS operations
assistant for Mattermost channels that receive AWS SNS notifications via
incoming webhooks. When a webhook posts an actionable alert
(Security Hub finding, CloudWatch Alarm firing, RDS Event such as a
storage threshold warning, AWS Backup failure, etc.) in a designated
channel, the bot replies — without requiring an `@mention` — with a
structured Japanese response covering:

1. 事象の要約 (summary)
2. 想定される影響 (impact)
3. 確認手順 (investigation steps — console path or `aws` CLI example)
4. 推奨対応 (recommended actions)

Posts that signal a healthy state (e.g. `:white_check_mark:` AWS Backup
COMPLETED, CloudWatch Alarm clearing to OK) are silently skipped so the
channel does not get spammed.

To enable:

1. Set `AWS_ALERT_CHANNEL_IDS` in `bot.env` to a comma-separated list of
   the Mattermost channel IDs that receive the SNS webhooks.
   To find a channel ID, open the channel in Mattermost and click the
   channel name in the header — the **Channel Info / View Info** dialog
   shows a 26-character alphanumeric ID (e.g.
   `abcdefghijk1234567890mnopq`) that you can copy. Alternatively, on a server with `mmctl` installed:
   `mmctl channel search <channel-name>`.
2. Add the bot user as a member of each of those channels (the bot does
   not receive messages from channels it has not joined).
3. Restart the service. Env vars are loaded once at startup.

To prevent flooding when many alerts arrive at once (e.g. Security Hub
compliance scans flagging dozens of findings), auto-replies are
rate-limited **per channel** via `AWS_ALERT_RATE_LIMIT` and
`AWS_ALERT_RATE_WINDOW_SECONDS` (default: at most 5 replies per 10
minutes per channel; set `AWS_ALERT_RATE_LIMIT=0` to disable).

The expected webhook message format is:

```
:emoji: <header text> (<region>)

<key>: <value>
<key>: <value>
...
```

The first-line `(region)` token is the trigger; both Mattermost-formatted
plain text (as posted by typical SNS-to-webhook Lambda relays) and
Markdown-style payloads are recognized.

## License

MIT License — free for anyone to use, modify, and distribute.
See [LICENSE](LICENSE) for the full text.

Copyright (c) 2023-2024 Sadao Hiratsuka (original author)  
Copyright (c) 2026 kazunito
