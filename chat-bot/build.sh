#!/bin/bash

YYYYMMDD=$(date +%Y%m%d)

podman build --tag=ai-chat-bot:${YYYYMMDD} .
