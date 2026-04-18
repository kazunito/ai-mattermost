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
podman build -t gemini-chat-bot .
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

## Usage

The bot uses the channel header as a system prompt.
Please edit the header first.

Mention the bot and it will respond with a thread.
If you reply to the thread, you can continue the conversation.
You do not need to re-mention the bot at this time.

<!-- TODO: replace with a new chat sample image -->

## License

MIT License — free for anyone to use, modify, and distribute.
See [LICENSE](LICENSE) for the full text.

Copyright (c) 2023-2024 Sadao Hiratsuka (original author)  
Copyright (c) 2026 kazunito
