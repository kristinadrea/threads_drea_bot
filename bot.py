import html
import json
import logging
import mimetypes
import os
import random
import re
from pathlib import Path
from typing import Iterable

import boto3
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram import Message, Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("threads_drea_bot")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
STATE_BACKUP_FILE = DATA_DIR / "state.backup.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
SOURCE_CHANNEL_ID = os.getenv("SOURCE_CHANNEL_ID", "").strip()
THREADS_USER_ID = os.getenv("THREADS_USER_ID", "").strip()
THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
THREADS_API_BASE = os.getenv("THREADS_API_BASE", "https://graph.threads.net/v1.0").rstrip("/")

MAX_THREAD_CHARS = int(os.getenv("MAX_THREAD_CHARS", "480"))
MAX_THREAD_PARTS = int(os.getenv("MAX_THREAD_PARTS", "5"))
CROSSPOST_IMAGES = os.getenv("CROSSPOST_IMAGES", "false").lower() == "true"
REQUIRE_THREADS_TAG = os.getenv("REQUIRE_THREADS_TAG", "false").lower() == "true"
THREADS_TAG = os.getenv("THREADS_TAG", "#threads")
TELEGRAM_LINK = os.getenv("TELEGRAM_LINK", "").strip()
ADD_TELEGRAM_LINK_EVERY_N_POSTS = int(os.getenv("ADD_TELEGRAM_LINK_EVERY_N_POSTS", "0"))

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
        "SOURCE_CHANNEL_ID": SOURCE_CHANNEL_ID,
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
    },
)
state.setdefault("posted_count", 0)
state.setdefault("telegram_message_ids", [])
state.setdefault("weekly_castaneda_used_indexes", [])


def save_state() -> None:
    save_json(STATE_FILE, STATE_BACKUP_FILE, state)


def message_matches_source(message: Message) -> bool:
    source = SOURCE_CHANNEL_ID.strip()
    chat = message.chat
    return str(chat.id) == source or bool(chat.username and f"@{chat.username}" == source)


def has_unsupported_media(message: Message) -> bool:
    return any(getattr(message, kind, None) is not None for kind in UNSUPPORTED_MESSAGE_KINDS)


def get_message_text(message: Message) -> str:
    return (message.text or message.caption or "").strip()


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


def split_text_for_threads(text: str, limit: int = MAX_THREAD_CHARS, max_parts: int = MAX_THREAD_PARTS) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= limit:
        return [text]

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        if current:
            chunks.append(current.strip())
            current = ""

    for paragraph in paragraphs:
        if len(paragraph) <= limit:
            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= limit:
                current = candidate
            else:
                flush_current()
                current = paragraph
            continue

        flush_current()
        for sentence in split_long_paragraph(paragraph, limit):
            if len(sentence) <= limit:
                chunks.append(sentence)
            else:
                chunks.extend(hard_wrap(sentence, limit))

    flush_current()
    if len(chunks) <= max_parts:
        return chunks

    allowed = chunks[: max_parts - 1]
    remainder = "\n\n".join(chunks[max_parts - 1 :])
    suffix = "\n\nFull text in Telegram."
    available = max(1, limit - len(suffix))
    allowed.append(remainder[:available].rstrip() + suffix)
    return allowed


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


def create_threads_container(text: str, media_type: str = "TEXT", image_url: str | None = None, reply_to_id: str | None = None) -> str:
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
    response.raise_for_status()
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
    response.raise_for_status()
    post_id = response.json().get("id")
    if not post_id:
        raise RuntimeError(f"Threads publish response did not include id: {response.text}")
    return post_id


def publish_threads_post(text: str, image_url: str | None = None, reply_to_id: str | None = None) -> str:
    media_type = "IMAGE" if image_url else "TEXT"
    container_id = create_threads_container(text, media_type=media_type, image_url=image_url, reply_to_id=reply_to_id)
    return publish_threads_container(container_id)


def publish_threads_chain(parts: Iterable[str], image_url: str | None = None) -> list[str]:
    post_ids: list[str] = []
    previous_id: str | None = None
    for index, part in enumerate(parts):
        post_id = publish_threads_post(part, image_url=image_url if index == 0 else None, reply_to_id=previous_id)
        post_ids.append(post_id)
        previous_id = post_id
    return post_ids


async def upload_telegram_photo_to_r2(message: Message, context: ContextTypes.DEFAULT_TYPE) -> str | None:
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


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.channel_post
    if not message or not message_matches_source(message):
        return

    if message.message_id in state.get("telegram_message_ids", []):
        logger.info("Skipping already processed Telegram message %s", message.message_id)
        return

    if has_unsupported_media(message):
        logger.info("Skipping Telegram message %s: unsupported media", message.message_id)
        remember_message(message.message_id)
        return

    if message.photo and not CROSSPOST_IMAGES:
        logger.info("Skipping Telegram message %s: image crossposting is disabled", message.message_id)
        remember_message(message.message_id)
        return

    raw_text = get_message_text(message)
    if not should_crosspost_text(raw_text):
        logger.info("Skipping Telegram message %s: no text or missing marker", message.message_id)
        remember_message(message.message_id)
        return

    text = clean_threads_marker(raw_text)
    try:
        image_url = await upload_telegram_photo_to_r2(message, context)
        parts = maybe_add_telegram_link(split_text_for_threads(text))
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


def choose_weekly_castaneda_post() -> tuple[str, str | None] | None:
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

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))

    scheduler = configure_scheduler()
    logger.info("threads_drea_bot started. Listening to %s", SOURCE_CHANNEL_ID)
    try:
        app.run_polling(allowed_updates=["channel_post"])
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
