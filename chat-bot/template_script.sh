#!/bin/bash

# Proxy
# export http_proxy=
# export https_proxy=
# export no_proxy=

# Mattermost
export MATTERMOST_URL=matter.example.com
export MATTERMOST_PORT=8065
export MATTERMOST_API_PATH=/api/v4
export SCHEME=https
export BOT_TOKEN=
# Override the WebSocket Origin header (e.g. when the bot connects to an
# internal host but the Mattermost server validates against the public URL).
# If unset, the Origin is derived from SCHEME + MATTERMOST_URL.
# export MATTERMOST_ORIGIN=https://matter.example.com

# LLM provider: "gemini", "claude" or "deepseek"
export LLM_PROVIDER=gemini

# Shared LLM settings. Pick the values that match the provider above.
#   Gemini:   AI_MODEL=gemini-1.5-flash
#   Claude:   AI_MODEL=claude-sonnet-4-6
#   DeepSeek: AI_MODEL=deepseek-chat
export AI_MODEL=gemini-1.5-flash
# AI_MAX_TOKENS is used by Claude and DeepSeek; ignored by Gemini.
export AI_MAX_TOKENS=4096
export AI_API_KEY=

# DeepSeek only: override the API endpoint if needed.
# export DEEPSEEK_BASE_URL=https://api.deepseek.com

python3 ai-chat-bot.py
