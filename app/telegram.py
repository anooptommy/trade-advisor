"""Small Telegram Bot API sender for recommendation alerts."""

from __future__ import annotations

import asyncio
import json
import os
import mimetypes
from pathlib import Path
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TelegramDeliveryError(RuntimeError):
    """A Telegram message could not be delivered."""


def _send_message_sync(token: str, chat_id: str, message: str, timeout: float) -> int | None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise TelegramDeliveryError(f"Telegram returned HTTP {exc.code}.") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise TelegramDeliveryError(f"Telegram request failed ({type(exc).__name__}).") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TelegramDeliveryError("Telegram returned an unreadable response.") from None

    if not isinstance(result, dict) or result.get("ok") is not True:
        raise TelegramDeliveryError("Telegram did not accept the message.")
    message_info = result.get("result")
    message_id = message_info.get("message_id") if isinstance(message_info, dict) else None
    return message_id if isinstance(message_id, int) else None


async def send_telegram_message(
    message: str,
    *,
    token: str | None = None,
    chat_id: str | None = None,
    timeout: float = 15.0,
) -> int | None:
    """Send one message; credentials default to environment variables."""
    bot_token = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN")
    destination = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not destination:
        raise TelegramDeliveryError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required.")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("Telegram message must be a non-empty string.")
    return await asyncio.to_thread(_send_message_sync, bot_token, destination, message, timeout)


def _send_photo_sync(token: str, chat_id: str, image_path: str | Path,
                     caption: str, timeout: float) -> int | None:
    """Upload a PNG with Telegram's existing bot credentials."""
    path = Path(image_path)
    boundary = f"----CodexTelegram{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks = []
    for name, value in (("chat_id", chat_id), ("caption", caption)):
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
    chunks.append(path.read_bytes())
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    request = Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise TelegramDeliveryError(f"Telegram returned HTTP {exc.code} for photo.") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise TelegramDeliveryError(f"Telegram photo request failed ({type(exc).__name__}).") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TelegramDeliveryError("Telegram returned an unreadable photo response.") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise TelegramDeliveryError("Telegram did not accept the photo.")
    message_info = result.get("result")
    message_id = message_info.get("message_id") if isinstance(message_info, dict) else None
    return message_id if isinstance(message_id, int) else None


async def send_telegram_photo(
    image_path: str | Path,
    caption: str,
    *,
    token: str | None = None,
    chat_id: str | None = None,
    timeout: float = 30.0,
) -> int | None:
    """Send an image through the same Telegram bot and chat as text alerts."""
    bot_token = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN")
    destination = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not destination:
        raise TelegramDeliveryError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required.")
    path = Path(image_path)
    if not path.is_file():
        raise ValueError("Telegram photo path must be an existing file.")
    if not isinstance(caption, str) or not caption.strip():
        raise ValueError("Telegram photo caption must be a non-empty string.")
    return await asyncio.to_thread(_send_photo_sync, bot_token, destination, path, caption[:1024], timeout)
