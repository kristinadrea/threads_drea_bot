import html
import json
import logging
import mimetypes
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import boto3
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram import BotCommand, BotCommandScopeChat, Message, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger("threads_drea_bot")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
STATE_BACKUP_FILE = DATA_DIR / "state.backup.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
SOURCE_CHANNEL_IDS_RAW = os.getenv("SOURCE_CHANNEL_IDS", os.getenv("SOURCE_CHANNEL_ID", "")).strip()
SOURCE_CHANNEL_IDS = [item.strip() for item in SOURCE_CHANNEL_IDS_RAW.split(",") if item.strip()]
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()
THREADS_USER_ID = os.getenv("THREADS_USER_ID", "").strip()
THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
THREADS_API_BASE = os.getenv("THREADS_API_BASE", "https://graph.threads.net/v1.0").rstrip("/")

MAX_THREAD_CHARS = int(os.getenv("MAX_THREAD_CHARS", "480"))
MAX_THREAD_PARTS = int(os.getenv("MAX_THREAD_PARTS", "5"))
THREADS_REPLY_DELAY_SECONDS = float(os.getenv("THREADS_REPLY_DELAY_SECONDS", "4"))
CROSSPOST_IMAGES = os.getenv("CROSSPOST_IMAGES", "false").lower() == "true"
REQUIRE_THREADS_TAG = os.getenv("REQUIRE_THREADS_TAG", "false").lower() == "true"
THREADS_TAG = os.getenv("THREADS_TAG", "#threads")
TELEGRAM_LINK = os.getenv("TELEGRAM_LINK", "").strip()
ADD_TELEGRAM_LINK_EVERY_N_POSTS = int(os.getenv("ADD_TELEGRAM_LINK_EVERY_N_POSTS", "0"))
THREADS_ENABLED_DEFAULT = os.getenv("THREADS_ENABLED", "true").lower() == "true"

WEEKLY_CASTANEDA_ENABLED = os.getenv("WEEKLY_CASTANEDA_ENABLED", "false").lower() == "true"
WEEKLY_CASTANEDA_DAY = os.getenv("WEEKLY_CASTANEDA_DAY", "sunday").lower()
WEEKLY_CASTANEDA_TIME = os.getenv("WEEKLY_CASTANEDA_TIME", "09:00")
TIMEZONE = os.getenv("TIMEZONE", "America/New_York")
CASTANEDA_QUOTES_FILE = Path(os.getenv("CASTANEDA_QUOTES_FILE", str(BASE_DIR / "castaneda_quotes.txt")))
CASTANEDA_MEDIA_URLS_FILE = Path(os.getenv("CASTANEDA_MEDIA_URLS_FILE", str(BASE_DIR / "castaneda_media_urls.txt")))

R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL", "").strip()
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET = os.getenv("R2_BUCKET", "").strip()
R2_PUBLIC_BASE_URL = os.getenv("R2_PUBLIC_BASE_URL", "").strip().rstrip("/")
R2_PREFIX = os.getenv("R2_PREFIX", "threads_drea_bot/").strip().strip("/")
R2_MEDIA_RETENTION_DAYS = int(os.getenv("R2_MEDIA_RETENTION_DAYS", "3"))

UNSUPPORTED_MESSAGE_KINDS = (
    "audio",
    "video",
    "document",
    "voice",
    "video_note",
    "animation",
    "sticker",
)
DAY_ALIASES = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}


def require_env() -> None:
    required = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "SOURCE_CHANNEL_IDS": SOURCE_CHANNEL_IDS_RAW,
        "THREADS_USER_ID": THREADS_USER_ID,
        "THREADS_ACCESS_TOKEN": THREADS_ACCESS_TOKEN,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default.copy()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        if STATE_BACKUP_FILE.exists():
            logger.warning("State file is broken; loading backup")
            return json.loads(STATE_BACKUP_FILE.read_text(encoding="utf-8"))
        raise


def save_json(path: Path, backup_path: Path, payload: dict) -> None:
    if path.exists():
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


state = load_json(
    STATE_FILE,
    {
        "posted_count": 0,
        "telegram_message_ids": [],
        "weekly_castaneda_used_indexes": [],
        "threads_enabled": THREADS_ENABLED_DEFAULT,
        "max_thread_parts": MAX_THREAD_PARTS,
        "awaiting_threads_parts_user_id": None,
    },
)
state.setdefault("posted_count", 0)
state.setdefault("telegram_message_ids", [])
state.setdefault("weekly_castaneda_used_indexes", [])
state.setdefault("threads_enabled", THREADS_ENABLED_DEFAULT)
state.setdefault("max_thread_parts", MAX_THREAD_PARTS)
state.setdefault("awaiting_threads_parts_user_id", None)


def save_state() -> None:
    save_json(STATE_FILE, STATE_BACKUP_FILE, state)


def threads_enabled() -> bool:
    return bool(state.get("threads_enabled", THREADS_ENABLED_DEFAULT))


def set_threads_enabled(enabled: bool) -> None:
    state["threads_enabled"] = enabled
    save_state()


def is_admin_update(update: Update) -> bool:
    if not ADMIN_USER_ID or not update.effective_user:
        return False
    return str(update.effective_user.id) == ADMIN_USER_ID


def get_max_thread_parts() -> int:
    try:
        value = int(state.get("max_thread_parts", MAX_THREAD_PARTS))
    except (TypeError, ValueError):
        value = MAX_THREAD_PARTS
    return max(1, value)


def set_max_thread_parts(value: int) -> None:
    state["max_thread_parts"] = value
    state["awaiting_threads_parts_user_id"] = None
    save_state()


def threads_status_text() -> str:
    status = "ON" if threads_enabled() else "OFF"
    return f"Threads posting: {status}\nMax thread parts: {get_max_thread_parts()}"


async def reject_non_admin(update: Update) -> bool:
    if is_admin_update(update):
        return False
    if update.effective_message:
        await update.effective_message.reply_text("This command is private.")
    return True


async def threads_toggle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_non_admin(update):
        return
    set_threads_enabled(not threads_enabled())
    await update.effective_message.reply_text(threads_status_text())


def thread_parts_prompt() -> str:
    return f"Сколько частей треда нужно? Сейчас: {get_max_thread_parts()}. Введите число от 1 до 25."


def parse_thread_parts_value(raw_value: str) -> tuple[Optional[int], Optional[str]]:
    cleaned = raw_value.strip()
    if not re.fullmatch(r"[0-9]+", cleaned):
        return None, "digits"
    value = int(cleaned)
    if 1 <= value <= 25:
        return value, None
    return None, "range"


def thread_parts_error_text(reason: Optional[str]) -> str:
    if reason == "digits":
        return f"Можно вводить только цифры.\n\n{thread_parts_prompt()}"
    return f"Введите число от 1 до 25.\n\n{thread_parts_prompt()}"


async def threads_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_non_admin(update):
        return
    await update.effective_message.reply_text(threads_status_text())


async def threads_parts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_non_admin(update):
        return

    if context.args:
        value, error_reason = parse_thread_parts_value(context.args[0])
        if value is None:
            state["awaiting_threads_parts_user_id"] = str(update.effective_user.id)
            save_state()
            await update.effective_message.reply_text(thread_parts_error_text(error_reason))
            return
        set_max_thread_parts(value)
        await update.effective_message.reply_text(threads_status_text())
        return

    state["awaiting_threads_parts_user_id"] = str(update.effective_user.id)
    save_state()
    await update.effective_message.reply_text(thread_parts_prompt())


async def admin_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin_update(update):
        return
    if str(update.effective_user.id) != str(state.get("awaiting_threads_parts_user_id")):
        return

    value, error_reason = parse_thread_parts_value(update.effective_message.text or "")
    if value is None:
        await update.effective_message.reply_text(thread_parts_error_text(error_reason))
        return

    set_max_thread_parts(value)
    await update.effective_message.reply_text(threads_status_text())


async def setup_bot_commands(app: Application) -> None:
    await app.bot.delete_my_commands()
    if not ADMIN_USER_ID:
        logger.warning("ADMIN_USER_ID is not set; Telegram command menu will not be scoped to admin")
        return
    commands = [
        BotCommand("threads", "Threads on/off"),
        BotCommand("threads_parts", "Set max thread parts"),
        BotCommand("threads_status", "Threads status"),
    ]
    await app.bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=int(ADMIN_USER_ID)))


def message_matches_source(message: Message) -> bool:
    chat = message.chat
    candidates = {str(chat.id)}
    if chat.username:
        candidates.add(f"@{chat.username}")
        candidates.add(chat.username)
    return any(source in candidates for source in SOURCE_CHANNEL_IDS)


def has_unsupported_media(message: Message) -> bool:
    return any(getattr(message, kind, None) is not None for kind in UNSUPPORTED_MESSAGE_KINDS)


def get_message_text(message: Message) -> str:
    return (message.text or message.caption or "").strip()


def telegram_post_url(message: Message) -> Optional[str]:
    chat = message.chat
    if chat.username:
        return f"https://t.me/{chat.username}/{message.message_id}"

    chat_id = str(chat.id)
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{message.message_id}"
    return None


def clean_threads_marker(text: str) -> str:
    if not THREADS_TAG:
        return text.strip()
    return re.sub(rf"(^|\s){re.escape(THREADS_TAG)}(\s|$)", " ", text, flags=re.IGNORECASE).strip()


def should_crosspost_text(text: str) -> bool:
    if not text:
        return False
    if REQUIRE_THREADS_TAG and THREADS_TAG.lower() not in text.lower():
        return False
    return True


def split_text_for_threads(
    text: str,
    limit: int = MAX_THREAD_CHARS,
    max_parts: Optional[int] = None,
    source_url: Optional[str] = None,
) -> list[str]:
    if max_parts is None:
        max_parts = get_max_thread_parts()
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= limit:
        return [text]

    suffix = f"\n\nMore in Telegram\n{source_url}" if source_url else "\n\nFull text in Telegram."
    part_count = min(max_parts, max(1, (len(text) + limit - 1) // limit))
    truncated = len(text) > part_count * limit

    if truncated:
        last_limit = max(1, limit - len(suffix))
        visible_limit = max(1, (part_count - 1) * limit + last_limit)
        text = trim_to_readable_boundary(text[:visible_limit].rstrip())
        part_limits = [limit] * max(0, part_count - 1) + [last_limit]
    else:
        part_limits = [limit] * part_count

    parts = split_text_evenly(text, part_limits)
    if truncated:
        parts[-1] = parts[-1].rstrip() + suffix
    return parts


def split_text_evenly(text: str, part_limits: list[int]) -> list[str]:
    tokens = text_to_tokens(text)
    parts: list[str] = []
    token_index = 0

    for part_index, part_limit in enumerate(part_limits):
        remaining_parts = len(part_limits) - part_index
        remaining_len = sum(len(token) for token in tokens[token_index:])
        if remaining_len <= 0:
            break

        target = min(part_limit, max(1, (remaining_len + remaining_parts - 1) // remaining_parts))
        current = ""

        while token_index < len(tokens):
            token = tokens[token_index]
            candidate = current + token if current else token.lstrip()
            current_is_tiny = bool(current) and len(current) < max(80, int(target * 0.85))
            can_fit_part = len(candidate) <= part_limit
            should_take = len(candidate) <= target or current_is_tiny or not current

            if should_take and can_fit_part:
                current = candidate
                token_index += 1
                continue

            if current:
                break

            current, leftover = split_oversized_token(candidate, part_limit)
            tokens[token_index] = leftover
            if not leftover:
                token_index += 1
            break

        if current.strip():
            parts.append(current.strip())

    if token_index < len(tokens):
        tail = "".join(tokens[token_index:]).strip()
        if tail:
            if parts and len(parts[-1]) + 2 + len(tail) <= part_limits[min(len(parts) - 1, len(part_limits) - 1)]:
                parts[-1] = f"{parts[-1]}\n\n{tail}"
            elif len(parts) < len(part_limits):
                extra, _ = split_oversized_token(tail, part_limits[len(parts)])
                parts.append(extra.strip())

    return parts or [text[: part_limits[0]].strip()]


def split_oversized_token(text: str, limit: int) -> tuple[str, str]:
    piece = text[:limit].rstrip()
    boundary = max(piece.rfind(" "), piece.rfind("\n"))
    if boundary > int(limit * 0.65):
        piece = piece[:boundary].rstrip()
    return piece, text[len(piece):].lstrip()


def trim_to_readable_boundary(text: str) -> str:
    if not text:
        return text

    sentence_ends = [match.end() for match in re.finditer(r"[.!?][\"')\]]*(?=\s|$)", text)]
    min_pos = int(len(text) * 0.72)
    usable_sentence_ends = [position for position in sentence_ends if position >= min_pos]
    if usable_sentence_ends:
        return text[: usable_sentence_ends[-1]].rstrip()

    boundary = max(text.rfind(" "), text.rfind("\n"))
    if boundary > min_pos:
        return text[:boundary].rstrip()
    return text.rstrip()


def split_sentences(paragraph: str) -> list[str]:
    sentences = [
        match.group(0).strip()
        for match in re.finditer(r"[^.!?]+(?:[.!?][\"')\]]*)?", paragraph)
        if match.group(0).strip()
    ]
    return sentences or [paragraph.strip()]


def text_to_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    for paragraph_index, paragraph in enumerate(paragraphs):
        sentences = split_sentences(paragraph)
        for sentence_index, sentence in enumerate(sentences):
            if tokens and sentence_index == 0 and paragraph_index > 0:
                tokens.append("\n\n" + sentence)
            elif tokens:
                tokens.append(" " + sentence)
            else:
                tokens.append(sentence)
    return tokens


def split_long_paragraph(paragraph: str, limit: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence.strip()
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = sentence.strip()
    if current:
        chunks.append(current)
    return chunks


def hard_wrap(text: str, limit: int) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = word[:limit]
    if current:
        chunks.append(current)
    return chunks


def maybe_add_telegram_link(parts: list[str]) -> list[str]:
    if not TELEGRAM_LINK or ADD_TELEGRAM_LINK_EVERY_N_POSTS <= 0:
        return parts
    next_count = int(state.get("posted_count", 0)) + 1
    if next_count % ADD_TELEGRAM_LINK_EVERY_N_POSTS != 0:
        return parts

    suffix = f"\n\nMore: {TELEGRAM_LINK}"
    if len(parts[-1]) + len(suffix) <= MAX_THREAD_CHARS:
        parts[-1] += suffix
    elif len(parts) < MAX_THREAD_PARTS:
        parts.append(f"More: {TELEGRAM_LINK}")
    return parts


def raise_for_threads_response(response: requests.Response, action: str) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise RuntimeError(f"Threads {action} failed: {response.status_code} {response.text}") from exc


def create_threads_container(text: str, media_type: str = "TEXT", image_url: Optional[str] = None, reply_to_id: Optional[str] = None) -> str:
    payload = {
        "media_type": media_type,
        "text": text,
        "access_token": THREADS_ACCESS_TOKEN,
    }
    if image_url:
        payload["image_url"] = image_url
    if reply_to_id:
        payload["reply_to_id"] = reply_to_id

    response = requests.post(f"{THREADS_API_BASE}/{THREADS_USER_ID}/threads", data=payload, timeout=30)
    raise_for_threads_response(response, "container creation")
    container_id = response.json().get("id")
    if not container_id:
        raise RuntimeError(f"Threads container response did not include id: {response.text}")
    return container_id


def publish_threads_container(container_id: str) -> str:
    response = requests.post(
        f"{THREADS_API_BASE}/{THREADS_USER_ID}/threads_publish",
        data={"creation_id": container_id, "access_token": THREADS_ACCESS_TOKEN},
        timeout=30,
    )
    raise_for_threads_response(response, "publish")
    post_id = response.json().get("id")
    if not post_id:
        raise RuntimeError(f"Threads publish response did not include id: {response.text}")
    return post_id


def publish_threads_post(text: str, image_url: Optional[str] = None, reply_to_id: Optional[str] = None) -> str:
    media_type = "IMAGE" if image_url else "TEXT"
    container_id = create_threads_container(text, media_type=media_type, image_url=image_url, reply_to_id=reply_to_id)
    return publish_threads_container(container_id)


def publish_threads_chain(parts: Iterable[str], image_url: Optional[str] = None) -> list[str]:
    parts = list(parts)
    post_ids: list[str] = []
    previous_id: Optional[str] = None
    for index, part in enumerate(parts):
        if previous_id and THREADS_REPLY_DELAY_SECONDS > 0:
            time.sleep(THREADS_REPLY_DELAY_SECONDS)
        logger.info("Publishing Threads part %s/%s (%s chars)", index + 1, len(parts), len(part))
        post_id = publish_threads_post(part, image_url=image_url if index == 0 else None, reply_to_id=previous_id)
        post_ids.append(post_id)
        previous_id = post_id
    return post_ids


async def upload_telegram_photo_to_r2(message: Message, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    if not CROSSPOST_IMAGES or not message.photo:
        return None
    ensure_r2_configured()
    photo = message.photo[-1]
    telegram_file = await context.bot.get_file(photo.file_id)
    file_bytes = await telegram_file.download_as_bytearray()
    filename = f"telegram-{message.chat_id}-{message.message_id}-{photo.file_unique_id}.jpg"
    key = f"{R2_PREFIX}/{filename}" if R2_PREFIX else filename

    client = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    )
    content_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
    client.put_object(Bucket=R2_BUCKET, Key=key, Body=bytes(file_bytes), ContentType=content_type)
    return f"{R2_PUBLIC_BASE_URL}/{key}"


def ensure_r2_configured() -> None:
    required = {
        "R2_ENDPOINT_URL": R2_ENDPOINT_URL,
        "R2_ACCESS_KEY_ID": R2_ACCESS_KEY_ID,
        "R2_SECRET_ACCESS_KEY": R2_SECRET_ACCESS_KEY,
        "R2_BUCKET": R2_BUCKET,
        "R2_PUBLIC_BASE_URL": R2_PUBLIC_BASE_URL,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError("CROSSPOST_IMAGES=true but R2 config is missing: " + ", ".join(missing))


def create_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def cleanup_old_r2_media() -> None:
    if not CROSSPOST_IMAGES:
        return
    ensure_r2_configured()
    prefix = f"{R2_PREFIX}/" if R2_PREFIX else ""
    cutoff = datetime.now(timezone.utc) - timedelta(days=R2_MEDIA_RETENTION_DAYS)
    client = create_r2_client()
    deleted = 0

    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=R2_BUCKET, Prefix=prefix):
        for item in page.get("Contents", []):
            last_modified = item.get("LastModified")
            key = item.get("Key")
            if not key or not last_modified or last_modified >= cutoff:
                continue
            client.delete_object(Bucket=R2_BUCKET, Key=key)
            deleted += 1

    if deleted:
        logger.info("Deleted %s old R2 media object(s) from %s", deleted, prefix or R2_BUCKET)


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.channel_post
    if not message or not message_matches_source(message):
        return

    if not threads_enabled():
        logger.info("Skipping Telegram message %s: Threads posting is disabled", message.message_id)
        remember_message(message.message_id)
        return

    if message.message_id in state.get("telegram_message_ids", []):
        logger.info("Skipping already processed Telegram message %s", message.message_id)
        return

    if has_unsupported_media(message):
        logger.info("Skipping Telegram message %s: unsupported media", message.message_id)
        remember_message(message.message_id)
        return

    raw_text = get_message_text(message)
    image_can_be_crossposted = bool(message.photo and CROSSPOST_IMAGES)
    if not should_crosspost_text(raw_text) and not image_can_be_crossposted:
        logger.info("Skipping Telegram message %s: no text or missing marker", message.message_id)
        remember_message(message.message_id)
        return

    if message.photo and not CROSSPOST_IMAGES:
        logger.info("Crossposting Telegram message %s caption without image: image crossposting is disabled", message.message_id)

    text = clean_threads_marker(raw_text)
    try:
        image_url = await upload_telegram_photo_to_r2(message, context)
        parts = maybe_add_telegram_link(split_text_for_threads(text, source_url=telegram_post_url(message)))
        logger.info("Telegram message %s split into %s Threads part(s): %s", message.message_id, len(parts), [len(part) for part in parts])
        post_ids = publish_threads_chain(parts, image_url=image_url)
    except Exception:
        logger.exception("Failed to crosspost Telegram message %s", message.message_id)
        return

    state["posted_count"] = int(state.get("posted_count", 0)) + 1
    remember_message(message.message_id, save=False)
    save_state()
    logger.info("Crossposted Telegram message %s to Threads posts: %s", message.message_id, ", ".join(post_ids))


def remember_message(message_id: int, save: bool = True) -> None:
    ids = state.setdefault("telegram_message_ids", [])
    ids.append(message_id)
    state["telegram_message_ids"] = ids[-1000:]
    if save:
        save_state()


def load_quote_blocks(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [block.strip() for block in text.split("\n---\n") if block.strip()]


def load_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def choose_weekly_castaneda_post() -> Optional[tuple[str, Optional[str]]]:
    quotes = load_quote_blocks(CASTANEDA_QUOTES_FILE)
    if not quotes:
        logger.warning("Weekly Castaneda is enabled, but no quotes were found")
        return None

    used = set(state.get("weekly_castaneda_used_indexes", []))
    if len(used) >= len(quotes):
        used = set()
        state["weekly_castaneda_used_indexes"] = []

    available = [index for index in range(len(quotes)) if index not in used]
    index = random.choice(available)
    state.setdefault("weekly_castaneda_used_indexes", []).append(index)

    media_urls = load_lines(CASTANEDA_MEDIA_URLS_FILE)
    image_url = random.choice(media_urls) if media_urls else None
    return quotes[index], image_url


def post_weekly_castaneda() -> None:
    if not threads_enabled():
        logger.info("Skipping weekly Castaneda post: Threads posting is disabled")
        return
    selected = choose_weekly_castaneda_post()
    if not selected:
        return
    text, image_url = selected
    try:
        parts = split_text_for_threads(strip_html_tags(text))
        post_ids = publish_threads_chain(parts, image_url=image_url)
    except Exception:
        logger.exception("Failed to publish weekly Castaneda post")
        return
    save_state()
    logger.info("Published weekly Castaneda post to Threads posts: %s", ", ".join(post_ids))


def strip_html_tags(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def configure_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    if CROSSPOST_IMAGES:
        scheduler.add_job(
            cleanup_old_r2_media,
            "interval",
            hours=1,
            id="cleanup_old_r2_media",
            replace_existing=True,
        )
        logger.info("R2 media cleanup enabled: deleting %s older than %s day(s)", R2_PREFIX or R2_BUCKET, R2_MEDIA_RETENTION_DAYS)
    if WEEKLY_CASTANEDA_ENABLED:
        hour_text, minute_text = WEEKLY_CASTANEDA_TIME.split(":", 1)
        scheduler.add_job(
            post_weekly_castaneda,
            "cron",
            day_of_week=DAY_ALIASES.get(WEEKLY_CASTANEDA_DAY, WEEKLY_CASTANEDA_DAY[:3]),
            hour=int(hour_text),
            minute=int(minute_text),
            id="weekly_castaneda_post",
            replace_existing=True,
        )
        logger.info("Weekly Castaneda post scheduled: %s at %s %s", WEEKLY_CASTANEDA_DAY, WEEKLY_CASTANEDA_TIME, TIMEZONE)
    scheduler.start()
    return scheduler


def main() -> None:
    require_env()
    if CROSSPOST_IMAGES:
        ensure_r2_configured()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(setup_bot_commands).build()
    app.add_handler(CommandHandler("threads", threads_toggle_command))
    app.add_handler(CommandHandler("threads_parts", threads_parts_command))
    app.add_handler(CommandHandler("threads_status", threads_status_command))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, admin_text_message))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))

    scheduler = configure_scheduler()
    logger.info("threads_drea_bot started. Listening to %s. %s", ", ".join(SOURCE_CHANNEL_IDS), threads_status_text())
    try:
        app.run_polling(allowed_updates=["channel_post", "message"])
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
