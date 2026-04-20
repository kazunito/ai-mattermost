import base64
import json
import logging
import mimetypes
import os
import re
import signal
import sys
import threading
import time
import traceback
from abc import ABC, abstractmethod
from typing import Iterator

import anthropic
import google.generativeai
import openai
import websocket

from mmpy_bot import (
    Bot,
    Message,
    Plugin,
    Settings,
    listen_to
)

log = logging.getLogger("ai-chat-bot")


def handler(signum, frame):
    print(f"Signal {signum} received.")
    sys.exit(0)


CLAUDE_IMAGE_MIME_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp"
}
CLAUDE_DOCUMENT_MIME_TYPES = {"application/pdf"}

GEMINI_SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "image/png", "image/jpeg", "image/webp",
    "image/heic", "image/heif"
}


class LLMProvider(ABC):
    @abstractmethod
    def build_messages(self, thread, bot_id: str, bot_username: str,
                       context: str, files_by_post_id: dict) -> list:
        ...

    @abstractmethod
    def stream(self, messages: list, context: str) -> Iterator[str]:
        ...


class GeminiProvider(LLMProvider):
    def __init__(self):
        # gRPC does not support SOCKS5 proxy, so use REST
        google.generativeai.configure(
            api_key=os.environ.get("AI_API_KEY", ""),
            transport="rest"
        )

        self.model = google.generativeai.GenerativeModel(
            os.environ.get("AI_MODEL") or "gemini-1.5-flash"
        )

    def build_messages(self, thread, bot_id, bot_username, context,
                       files_by_post_id):
        requestMessages = []

        for post_id in thread["order"]:
            post = thread["posts"][post_id]
            if post["user_id"] == bot_id:
                requestMessages.append(
                    {"role": "model", "parts": [post["message"]]})
            else:
                # Remove mentions of the bot.
                message = post["message"].replace("@" + bot_username, "")

                file_parts, skipped = self._build_file_parts(
                    files_by_post_id.get(post_id) or [])
                if skipped:
                    message = (message + "\n" + skipped).strip()

                text_prefix = ("Context: " + context + "\nMessage: "
                               if len(requestMessages) == 0 else "Message: ")
                new_parts = file_parts + [text_prefix + message]

                if (len(requestMessages) > 0
                        and requestMessages[-1]["role"] == "user"):
                    # Append to the existing user turn so consecutive user
                    # posts (and their files) go into a single request entry.
                    requestMessages[-1]["parts"].extend(new_parts)
                else:
                    requestMessages.append(
                        {"role": "user", "parts": new_parts})

        return requestMessages

    def _build_file_parts(self, files):
        parts = []
        skipped = []
        for f in files:
            mime = f["mime_type"]
            if mime in GEMINI_SUPPORTED_MIME_TYPES:
                parts.append({"mime_type": mime, "data": f["data"]})
            else:
                skipped.append(
                    f"[Attachment '{f['filename']}' ({mime}) skipped: "
                    "unsupported by Gemini]")
        return parts, "\n".join(skipped)

    def stream(self, messages, context):
        response = self.model.generate_content(messages, stream=True)
        for chunk in response:
            for part in chunk.parts:
                yield part.text


class ClaudeProvider(LLMProvider):
    def __init__(self):
        self.client = anthropic.Anthropic(
            api_key=os.environ.get("AI_API_KEY", "")
        )
        self.model = os.environ.get("AI_MODEL") or "claude-sonnet-4-6"
        self.max_tokens = int(os.environ.get("AI_MAX_TOKENS") or "4096")

    def build_messages(self, thread, bot_id, bot_username, context,
                       files_by_post_id):
        requestMessages = []

        for post_id in thread["order"]:
            post = thread["posts"][post_id]
            if post["user_id"] == bot_id:
                requestMessages.append(
                    {"role": "assistant", "content": post["message"]})
            else:
                # Remove mentions of the bot.
                message = post["message"].replace("@" + bot_username, "")

                file_blocks, skipped = self._build_file_blocks(
                    files_by_post_id.get(post_id) or [])
                if skipped:
                    message = (message + "\n" + skipped).strip()

                blocks = list(file_blocks)
                if message or not blocks:
                    blocks.append({"type": "text", "text": message})

                # Claude rejects consecutive messages with the same role,
                # so merge them into a single content list.
                if (len(requestMessages) > 0
                        and requestMessages[-1]["role"] == "user"):
                    prev = requestMessages[-1]["content"]
                    if isinstance(prev, str):
                        prev = [{"type": "text", "text": prev}]
                    prev.extend(blocks)
                    requestMessages[-1]["content"] = prev
                else:
                    requestMessages.append(
                        {"role": "user", "content": blocks})

        return requestMessages

    def _build_file_blocks(self, files):
        blocks = []
        skipped = []
        for f in files:
            mime = f["mime_type"]
            data_b64 = base64.standard_b64encode(f["data"]).decode("ascii")
            if mime in CLAUDE_DOCUMENT_MIME_TYPES:
                blocks.append({
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": data_b64,
                    },
                })
            elif mime in CLAUDE_IMAGE_MIME_TYPES:
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": data_b64,
                    },
                })
            else:
                skipped.append(
                    f"[Attachment '{f['filename']}' ({mime}) skipped: "
                    "unsupported by Claude]")
        return blocks, "\n".join(skipped)

    def stream(self, messages, context):
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=context,
            messages=messages,
        ) as response:
            for text in response.text_stream:
                yield text


class DeepSeekProvider(LLMProvider):
    def __init__(self):
        self.client = openai.OpenAI(
            api_key=os.environ.get("AI_API_KEY", ""),
            base_url=os.environ.get(
                "DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
        )
        self.model = os.environ.get("AI_MODEL") or "deepseek-chat"
        self.max_tokens = int(os.environ.get("AI_MAX_TOKENS") or "4096")

    def build_messages(self, thread, bot_id, bot_username, context,
                       files_by_post_id):
        # DeepSeek is text-only; attachments are ignored here.
        requestMessages = []

        for post_id in thread["order"]:
            post = thread["posts"][post_id]
            if post["user_id"] == bot_id:
                requestMessages.append(
                    {"role": "assistant", "content": post["message"]})
            else:
                # Remove mentions of the bot.
                message = post["message"].replace("@" + bot_username, "")

                skipped = [
                    f"[Attachment '{f['filename']}' ({f['mime_type']}) "
                    "skipped: DeepSeek does not accept files]"
                    for f in files_by_post_id.get(post_id) or []
                ]
                if skipped:
                    message = (message + "\n" + "\n".join(skipped)).strip()

                # Merge consecutive user messages to keep the turn
                # structure simple.
                if (len(requestMessages) > 0
                        and requestMessages[-1]["role"] == "user"):
                    requestMessages[-1]["content"] += "\n" + message
                else:
                    requestMessages.append(
                        {"role": "user", "content": message})

        return requestMessages

    def stream(self, messages, context):
        stream = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "system", "content": context}, *messages],
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


def create_provider() -> LLMProvider:
    name = (os.environ.get("LLM_PROVIDER") or "gemini").lower()
    if name == "gemini":
        return GeminiProvider()
    if name == "claude":
        return ClaudeProvider()
    if name == "deepseek":
        return DeepSeekProvider()
    raise ValueError(
        f"Unknown LLM_PROVIDER: {name}. "
        "Expected 'gemini', 'claude' or 'deepseek'.")


class ChatBot(Plugin):
    def __init__(self):
        super().__init__()

        self.provider = create_provider()

    @listen_to("")
    def respond(self, message: Message):
        if self.driver is None:
            raise ValueError("self.driver is None")

        if self.settings is None:
            raise ValueError("self.setting is None")

        # Get the entire thread.
        thread = self.driver.get_post_thread(message.id)

        # Fetch channel info up-front since both the mention check and the
        # system prompt depend on it.
        channels = self.driver.channels  # type: ignore
        channel = channels.get_channel(message.channel_id)

        # If you use needs_mention in the listen_to decorator,
        # you can only see the last remark, so don't use this,
        # but look at the whole thread to determine if you should reply to the message.
        if not self.is_reply_required(thread, message.sender_name, channel):
            return

        # Use channel header as context.
        context = channel["header"]

        # Download files attached to thread posts so they can be passed to
        # multimodal providers (Claude / Gemini).
        files_by_post_id = self._load_thread_files(thread)

        # Assemble the request message.
        # TODO: Check if the number of tokens is exceeded.
        requestMessages = self.provider.build_messages(
            thread, self.driver.user_id, self.driver.username, context,
            files_by_post_id)
        log.info("API Request: " +
                 json.dumps(requestMessages, ensure_ascii=False,
                            default=self._log_default))

        ws = None
        stop_typing = threading.Event()

        try:
            # Start typing.
            # Connect separately as I cannot find a way to use the WebSocket connection
            # used by the bot itself.
            websocket_auth = self.build_websocket_auth()
            websocket_typing = self.build_websocket_typing(message)
            ws = websocket.WebSocket()
            ws.connect(self.build_websocket_url(),
                       origin=self.build_websocket_origin())
            ws.send(json.dumps(websocket_auth))
            self.send_typing(ws, websocket_typing, stop_typing)

            # Reply with an empty message and update with stream data.
            reply = self.driver.reply_to(message, "")
            reply_id = reply["id"]
            reply_chunks = []
            last_time = time.time()

            for text in self.provider.stream(requestMessages, context):
                reply_chunks.append(text)

                current_time = time.time()

                # Stores stream data for one second before outputting it.
                if last_time + 1.0 <= current_time:
                    last_time = current_time
                    reply_messages = "".join(reply_chunks)
                    reply_chunks = [reply_messages]

                    self.driver.posts.update_post(  # type: ignore
                        post_id=reply_id,
                        options={
                            "id": reply_id,
                            "message": reply_messages + "▌"
                        }
                    )

            # Update again with the last set of data.
            reply_messages = "".join(reply_chunks)
            log.info("API Response: " + reply_messages)

            self.driver.posts.update_post(  # type: ignore
                post_id=reply_id,
                options={
                    "id": reply_id,
                    "message": reply_messages
                }
            )
        except Exception:
            stacktrace = traceback.format_exc()
            log.error(f"Exception:\n{stacktrace}")
            self.driver.reply_to(
                message,
                "An internal error occurred while generating the reply. "
                "Please try again later."
            )
        finally:
            # Stop typing.
            stop_typing.set()
            if ws is not None:
                ws.close()

    def _load_thread_files(self, thread) -> dict:
        """
        Download files attached to posts in the thread.
        Returns dict[post_id -> list of {file_id, filename, mime_type, data}].
        """

        if self.driver is None:
            raise ValueError("self.driver is None")

        files_by_post_id: dict = {}

        for post_id in thread["order"]:
            post = thread["posts"][post_id]
            file_ids = post.get("file_ids") or []
            if not file_ids:
                continue

            # Prefer metadata.files when available — it already contains
            # the per-file info (name, mime_type, size) so we can avoid
            # an extra get_file_info call per file.
            metadata = post.get("metadata") or {}
            info_map = {
                f.get("id"): f for f in (metadata.get("files") or [])
            }

            loaded = []
            for file_id in file_ids:
                try:
                    info = info_map.get(file_id)
                    if info is None:
                        info = self.driver.files.get_file_info(  # type: ignore
                            file_id)
                    if not isinstance(info, dict):
                        info = dict(info)

                    mime = info.get("mime_type") or ""
                    if not mime:
                        guessed, _ = mimetypes.guess_type(
                            info.get("name") or "")
                        mime = guessed or "application/octet-stream"

                    raw = self.driver.files.get_file(file_id)  # type: ignore
                    if isinstance(raw, (bytes, bytearray)):
                        data = bytes(raw)
                    elif hasattr(raw, "content"):
                        data = raw.content  # httpx.Response
                    else:
                        data = bytes(raw)

                    loaded.append({
                        "file_id": file_id,
                        "filename": info.get("name") or file_id,
                        "mime_type": mime,
                        "data": data,
                    })
                except Exception:
                    log.warning(
                        f"Failed to download file {file_id}:\n"
                        f"{traceback.format_exc()}")

            if loaded:
                files_by_post_id[post_id] = loaded

        return files_by_post_id

    @staticmethod
    def _log_default(obj):
        if isinstance(obj, (bytes, bytearray)):
            return f"<bytes len={len(obj)}>"
        return f"<{type(obj).__name__}>"

    def is_reply_required(self, thread, sender_name: str, channel) -> bool:
        """
        Determine if the bot should reply to the thread.
        """

        if self.driver is None:
            raise ValueError("self.driver is None")

        # To prevent bots from talking to each other,
        # do not reply to messages by users beginning with "ai-".
        if sender_name.startswith("ai-"):
            return False

        # Direct messages ("D") and group messages ("G") are addressed to the
        # bot by definition, so reply without requiring an @mention.
        if channel.get("type") in ("D", "G"):
            return True

        # Reply to any mentions of the bot in the thread.
        # I couldn't find a function to extract mentions in mmpy_bot,
        # so I extracted them on my own.
        # username regex pattern: @([a-z0-9\.\-_]+)
        username_escaped = self.driver.username.replace(".", "\\.")
        pattern = fr"(^|\s)@{username_escaped}(?=$|\s)"

        for post_id in thread["order"]:
            if re.search(pattern, thread["posts"][post_id]["message"]):
                return True

        # If the conditions up to this point are not met, the bot will not reply.
        return False

    def build_websocket_url(self) -> str:
        """
        Assemble and return WebSocket connection URL.
        """

        if self.settings is None:
            raise ValueError("self.setting is None")

        protocol = "ws://"

        if self.settings.SCHEME == "https":
            protocol = "wss://"

        api_path = self.settings.MATTERMOST_API_PATH \
            or os.environ.get("MATTERMOST_API_PATH") \
            or "/api/v4"

        return protocol + self.settings.MATTERMOST_URL + ":" + \
            str(self.settings.MATTERMOST_PORT) + \
            api_path + "/websocket"

    def build_websocket_origin(self) -> str:
        """
        Return the Origin header value for the WebSocket handshake.
        Falls back to deriving it from SCHEME + MATTERMOST_URL when
        MATTERMOST_ORIGIN is not set.
        """

        if self.settings is None:
            raise ValueError("self.setting is None")

        origin = os.environ.get("MATTERMOST_ORIGIN")
        if origin:
            return origin

        scheme = "https://" if self.settings.SCHEME == "https" else "http://"
        return scheme + self.settings.MATTERMOST_URL

    def build_websocket_auth(self) -> dict:
        if self.settings is None:
            raise ValueError("self.setting is None")

        return {
            "seq": 1,
            "action": "authentication_challenge",
            "data": {
                "token": self.settings.BOT_TOKEN
            }
        }

    def build_websocket_typing(self, message: Message) -> dict:
        return {
            "action": "user_typing",
            "seq": 2,
            "data": {
                "channel_id": message.channel_id,
                # Use message.root_id. Note that message.parent_id is not defined.
                "parent_id": message.root_id
            }
        }

    def send_typing(self, ws: websocket.WebSocket, websocket_typing: dict,
                    stop_typing: threading.Event):
        """
        Notify that the bot is typing.
        """

        if stop_typing.is_set():
            return

        ws.send(json.dumps(websocket_typing))
        timer = threading.Timer(
            1.0, self.send_typing, args=[ws, websocket_typing, stop_typing])
        timer.daemon = True
        timer.start()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handler)
    bot = Bot(settings=Settings(), plugins=[ChatBot()])
    bot.run()
