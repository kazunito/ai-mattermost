import json
import logging
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


class LLMProvider(ABC):
    @abstractmethod
    def build_messages(self, thread, bot_id: str, bot_username: str,
                       context: str) -> list:
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

    def build_messages(self, thread, bot_id, bot_username, context):
        requestMessages = []

        for post_id in thread["order"]:
            post = thread["posts"][post_id]
            if post["user_id"] == bot_id:
                requestMessages.append(
                    {"role": "model", "parts": [post["message"]]})
            else:
                # Remove mentions of the bot.
                message = post["message"].replace("@" + bot_username, "")

                if len(requestMessages) == 0:
                    # Add context to the first message.
                    requestMessages.append(
                        {
                            "role": "user",
                            "parts": ["Context: " + context + "\nMessage: " + message]
                        }
                    )
                else:
                    if requestMessages[-1]["role"] == "user":
                        # If there are consecutive user posts, add them to parts.
                        requestMessages[-1]["parts"][0] += "\nMessage: " + message
                    else:
                        requestMessages.append(
                            {"role": "user", "parts": ["Message: " + message]})

        return requestMessages

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

    def build_messages(self, thread, bot_id, bot_username, context):
        requestMessages = []

        for post_id in thread["order"]:
            post = thread["posts"][post_id]
            if post["user_id"] == bot_id:
                requestMessages.append(
                    {"role": "assistant", "content": post["message"]})
            else:
                # Remove mentions of the bot.
                message = post["message"].replace("@" + bot_username, "")

                # Claude rejects consecutive messages with the same role,
                # so merge them.
                if (len(requestMessages) > 0
                        and requestMessages[-1]["role"] == "user"):
                    requestMessages[-1]["content"] += "\n" + message
                else:
                    requestMessages.append(
                        {"role": "user", "content": message})

        return requestMessages

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

    def build_messages(self, thread, bot_id, bot_username, context):
        requestMessages = []

        for post_id in thread["order"]:
            post = thread["posts"][post_id]
            if post["user_id"] == bot_id:
                requestMessages.append(
                    {"role": "assistant", "content": post["message"]})
            else:
                # Remove mentions of the bot.
                message = post["message"].replace("@" + bot_username, "")

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

        # Assemble the request message.
        # TODO: Check if the number of tokens is exceeded.
        requestMessages = self.provider.build_messages(
            thread, self.driver.user_id, self.driver.username, context)
        log.info("API Request: " +
                 json.dumps(requestMessages, ensure_ascii=False))

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

        return protocol + self.settings.MATTERMOST_URL + ":" + \
            str(self.settings.MATTERMOST_PORT) + \
            self.settings.MATTERMOST_API_PATH + "/websocket"

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
