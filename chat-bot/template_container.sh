#!/bin/bash

podman run \
        --detach \
        --restart=always \
        --env=http_proxy= \
        --env=https_proxy= \
        --env=no_proxy= \
        --env=MATTERMOST_URL=matter.example.com \
        --env=MATTERMOST_PORT=443 \
        --env=MATTERMOST_API_PATH=/api/v4 \
        --env=SCHEME=https \
        --env=BOT_TOKEN= \
        --env=MATTERMOST_ORIGIN= \
        --env=LLM_PROVIDER=gemini \
        --env=AI_MODEL=gemini-1.5-flash \
        --env=AI_MAX_TOKENS=4096 \
        --env=AI_API_KEY= \
        --name=ai-chat-bot \
        ai-chat-bot:20240101
