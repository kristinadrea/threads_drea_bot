import asyncio
import html
import json
import logging
import mimetypes
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone, time as datetime_time
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional

import boto3
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    Message,
    Update,
)
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger("threads_drea_bot")
ADMIN_BOT = None

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
STATE_BACKUP_FILE = DATA_DIR / "state.backup.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
SOURCE_CHANNEL_IDS_RAW = os.getenv("SOURCE_CHANNEL_IDS", os.getenv("SOURCE_CHANNEL_ID", "")).strip()
SOURCE_CHANNEL_IDS = [item.strip() for item in SOURCE_CHANNEL_IDS_RAW.split(",") if item.strip()]
CASTANEDA_CHANNEL_ID = os.getenv("CASTANEDA_CHANNEL_ID", "-1004445804313").strip()
CASTANEDA_TELEGRAM_LINK = os.getenv("CASTANEDA_TELEGRAM_LINK", "https://t.me/carlos_castaneda_quotes").strip()
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()
THREADS_USER_ID = os.getenv("THREADS_USER_ID", "").strip()
THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
THREADS_API_BASE = os.getenv("THREADS_API_BASE", "https://graph.threads.net/v1.0").rstrip("/")

BLUESKY_SERVICE = os.getenv("BLUESKY_SERVICE", "https://bsky.social").rstrip("/")
BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE", "").strip()
BLUESKY_APP_PASSWORD = os.getenv("BLUESKY_APP_PASSWORD", "").strip()
BLUESKY_ENABLED_DEFAULT = os.getenv("BLUESKY_ENABLED", "false").lower() == "true"
BLUESKY_MAX_CHARS = int(os.getenv("BLUESKY_MAX_CHARS", "280"))
BLUESKY_MAX_PARTS = int(os.getenv("BLUESKY_MAX_PARTS", "8"))
BLUESKY_IMAGE_ALT = os.getenv("BLUESKY_IMAGE_ALT", "").strip()
BLUESKY_MAX_IMAGE_BYTES = int(os.getenv("BLUESKY_MAX_IMAGE_BYTES", str(2 * 1024 * 1024)))
BLUESKY_TAGS = [tag.strip() for tag in os.getenv("BLUESKY_TAGS", "").split(",") if tag.strip()]
BLUESKY_MIN_TAGS = int(os.getenv("BLUESKY_MIN_TAGS", "2"))
BLUESKY_MAX_TAGS_PER_POST = int(os.getenv("BLUESKY_MAX_TAGS_PER_POST", "4"))
URL_PATTERN = re.compile(r"https?://[^\s<>()]+")

SEMANTIC_TAG_RULES = [
    ("Castaneda", ["castaneda", "don juan", "nagual", "tonal", "sorcerer", "warrior", "stalking", "dreaming", "intent", "assemblage point", "second attention", "impeccability"]),
    ("WarriorPath", ["warrior", "path", "impeccable", "discipline", "courage", "fear", "controlled folly"]),
    ("Awareness", ["awareness", "attention", "consciousness", "perception", "witness", "presence"]),
    ("Intent", ["intent", "will", "power", "direction", "summon"]),
    ("Dreaming", ["dream", "dreaming", "lucid", "night", "sleep"]),
    ("Stalking", ["stalk", "stalking", "strategy", "behavior", "self-importance", "personal importance"]),
    ("Silence", ["silence", "inner silence", "quiet", "stillness", "meditation"]),
    ("Energy", ["energy", "luminous", "light", "force", "vibration", "body"]),
    ("Freedom", ["freedom", "liberation", "free", "limit", "choice"]),
    ("Death", ["death", "mortality", "finite", "last moment"]),
    ("Philosophy", ["meaning", "truth", "wisdom", "knowledge", "reality", "existence"]),
    ("Spirituality", ["spirit", "soul", "sacred", "divine", "god", "mystic", "transcend"]),
    ("Creativity", ["create", "creator", "art", "imagination", "vision"]),
    ("Universe", ["universe", "cosmos", "infinity", "infinite", "stars"]),
    ("Psychology", ["mind", "emotion", "ego", "self", "identity", "memory"]),
    ("Love", ["love", "heart", "compassion", "tender"]),
]
DEFAULT_BLUESKY_TAGS = ["Spirituality", "Philosophy", "Awareness", "Quotes"]
SPIRITUAL_QUESTIONS = [
    "If your soul chose this life before you were born, what lesson do you think it came here to learn?",
    "What if your biggest weakness is actually the doorway to your real power?",
    "Do you think people meet by accident, or does the soul recognize certain people before the mind understands why?",
    "If the universe keeps repeating the same lesson, why do you think you still resist it?",
    "What if peace is not something you find, but something you stop disturbing?",
    "Have you ever felt that a person entered your life just to awaken a part of you?",
    "What if the life you are trying to escape is the exact place where your transformation begins?",
    "Do you believe intuition is a higher intelligence, or just the mind noticing what it cannot yet explain?",
    "What part of yourself are you still calling \"dark\" because you have not learned how to use its power?",
    "If your pain had a message, what would it be trying to tell you?",
    "What if the people who trigger you are showing you the places where you are still not free?",
    "Is forgiveness really about the other person, or is it the moment your soul refuses to stay chained?",
    "What if your anxiety is not a flaw, but energy looking for direction?",
    "Have you ever outgrown a version of yourself but kept living as if you were still that person?",
    "What if your \"bad timing\" was actually protection?",
    "Do you think the universe tests your faith, or only reveals where you never truly had it?",
    "What is one belief you had to lose in order to become more yourself?",
    "If your higher self could send you one message today, what do you think it would say?",
    "What if the real spiritual path is not becoming special, but becoming honest?",
    "Why do we call it loneliness when sometimes it is just the soul asking us to listen?",
    "What if the person you are becoming requires you to disappoint the person you used to be?",
    "Do you think some endings are actually initiations?",
    "What if your purpose is not one big mission, but the way your presence changes every room you enter?",
    "If you stopped trying to be understood by everyone, what would you finally allow yourself to become?",
    "What if the wound you keep hiding is the exact place where your medicine lives?",
    "Do you think spiritual growth makes life easier, or simply makes you harder to deceive?",
    "What if your intuition has been right all along, but your fear kept negotiating with it?",
    "Is \"letting go\" an act of surrender, or the highest form of self-respect?",
    "What if the universe does not give you what you want until you become the kind of person who can hold it?",
]

MAX_THREAD_CHARS = int(os.getenv("MAX_THREAD_CHARS", "480"))
MAX_THREAD_PARTS = int(os.getenv("MAX_THREAD_PARTS", "5"))
THREADS_REPLY_DELAY_SECONDS = float(os.getenv("THREADS_REPLY_DELAY_SECONDS", "4"))
THREADS_PUBLISH_RETRY_ATTEMPTS = int(os.getenv("THREADS_PUBLISH_RETRY_ATTEMPTS", "5"))
THREADS_PUBLISH_RETRY_DELAY_SECONDS = float(os.getenv("THREADS_PUBLISH_RETRY_DELAY_SECONDS", "5"))
CROSSPOST_IMAGES = os.getenv("CROSSPOST_IMAGES", "false").lower() == "true"
CROSSPOST_CASTANEDA_IMMEDIATELY = os.getenv("CROSSPOST_CASTANEDA_IMMEDIATELY", "false").lower() == "true"
REQUIRE_THREADS_TAG = os.getenv("REQUIRE_THREADS_TAG", "false").lower() == "true"
THREADS_TAG = os.getenv("THREADS_TAG", "#threads")
TELEGRAM_LINK = os.getenv("TELEGRAM_LINK", "").strip()
ADD_TELEGRAM_LINK_EVERY_N_POSTS = int(os.getenv("ADD_TELEGRAM_LINK_EVERY_N_POSTS", "0"))
THREADS_ENABLED_DEFAULT = os.getenv("THREADS_ENABLED", "true").lower() == "true"

WEEKLY_CASTANEDA_ENABLED = os.getenv("WEEKLY_CASTANEDA_ENABLED", "false").lower() == "true"
WEEKLY_CASTANEDA_DAY = os.getenv("WEEKLY_CASTANEDA_DAY", "thursday").lower()
WEEKLY_CASTANEDA_START_TIME = os.getenv("WEEKLY_CASTANEDA_START_TIME", "07:07")
WEEKLY_CASTANEDA_END_TIME = os.getenv("WEEKLY_CASTANEDA_END_TIME", "18:59")
TIMEZONE = os.getenv("TIMEZONE", "America/New_York")
CASTANEDA_QUOTES_FILE = Path(os.getenv("CASTANEDA_QUOTES_FILE", str(BASE_DIR / "castaneda_quotes.txt")))
CASTANEDA_MEDIA_URLS_FILE = Path(os.getenv("CASTANEDA_MEDIA_URLS_FILE", str(BASE_DIR / "castaneda_media_urls.txt")))
WEEKLY_SPIRITUAL_QUESTIONS_ENABLED = os.getenv("WEEKLY_SPIRITUAL_QUESTIONS_ENABLED", "true").lower() == "true"
WEEKLY_SPIRITUAL_QUESTIONS_DAY = os.getenv("WEEKLY_SPIRITUAL_QUESTIONS_DAY", "wednesday").lower()
WEEKLY_SPIRITUAL_QUESTIONS_START_TIME = os.getenv("WEEKLY_SPIRITUAL_QUESTIONS_START_TIME", "07:07")
WEEKLY_SPIRITUAL_QUESTIONS_END_TIME = os.getenv("WEEKLY_SPIRITUAL_QUESTIONS_END_TIME", "18:59")

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


def load_castaneda_quotes() -> list[str]:
    if not CASTANEDA_QUOTES_FILE.exists():
        logger.warning("Castaneda quotes file is missing: %s", CASTANEDA_QUOTES_FILE)
        return []
    raw_text = CASTANEDA_QUOTES_FILE.read_text(encoding="utf-8")
    quotes = [quote.strip() for quote in re.split(r"\n\s*---\s*\n", raw_text) if quote.strip()]
    return quotes


def load_castaneda_media_urls() -> list[str]:
    if not CASTANEDA_MEDIA_URLS_FILE.exists():
        logger.warning("Castaneda media URLs file is missing: %s", CASTANEDA_MEDIA_URLS_FILE)
        return []
    return [
        line.strip()
        for line in CASTANEDA_MEDIA_URLS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


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
        "weekly_castaneda_enabled": WEEKLY_CASTANEDA_ENABLED,
        "weekly_castaneda_next_run": None,
        "weekly_spiritual_questions_enabled": WEEKLY_SPIRITUAL_QUESTIONS_ENABLED,
        "weekly_spiritual_questions_next_run": None,
        "weekly_spiritual_questions_index": 0,
        "latest_castaneda_post": None,
        "castaneda_post_index": [],
        "castaneda_threads_published_message_ids": [],
        "castaneda_bluesky_published_message_ids": [],
        "castaneda_threads_fallback_index": 0,
        "castaneda_bluesky_fallback_index": 0,
        "castaneda_manual_fallback_index": 0,
        "threads_enabled": THREADS_ENABLED_DEFAULT,
        "bluesky_enabled": BLUESKY_ENABLED_DEFAULT,
        "max_thread_parts": MAX_THREAD_PARTS,
        "awaiting_threads_parts_user_id": None,
    },
)
state.setdefault("posted_count", 0)
state.setdefault("telegram_message_ids", [])
state.setdefault("weekly_castaneda_used_indexes", [])
state.setdefault("weekly_castaneda_enabled", WEEKLY_CASTANEDA_ENABLED)
state.setdefault("weekly_castaneda_next_run", None)
state.setdefault("weekly_spiritual_questions_enabled", WEEKLY_SPIRITUAL_QUESTIONS_ENABLED)
state.setdefault("weekly_spiritual_questions_next_run", None)
state.setdefault("weekly_spiritual_questions_index", 0)
state.setdefault("latest_castaneda_post", None)
state.setdefault("castaneda_post_index", [])
state.setdefault("castaneda_threads_published_message_ids", [])
state.setdefault("castaneda_bluesky_published_message_ids", [])
state.setdefault("castaneda_threads_fallback_index", 0)
state.setdefault("castaneda_bluesky_fallback_index", 0)
state.setdefault("castaneda_manual_fallback_index", 0)
state.setdefault("threads_enabled", THREADS_ENABLED_DEFAULT)
state.setdefault("bluesky_enabled", BLUESKY_ENABLED_DEFAULT)
state.setdefault("max_thread_parts", MAX_THREAD_PARTS)
state.setdefault("awaiting_threads_parts_user_id", None)


def save_state() -> None:
    save_json(STATE_FILE, STATE_BACKUP_FILE, state)


def threads_enabled() -> bool:
    return bool(state.get("threads_enabled", THREADS_ENABLED_DEFAULT))


def set_threads_enabled(enabled: bool) -> None:
    state["threads_enabled"] = enabled
    save_state()


def bluesky_enabled() -> bool:
    return bool(state.get("bluesky_enabled", BLUESKY_ENABLED_DEFAULT))


def set_bluesky_enabled(enabled: bool) -> None:
    state["bluesky_enabled"] = enabled
    save_state()


def bluesky_configured() -> bool:
    return bool(BLUESKY_HANDLE and BLUESKY_APP_PASSWORD)


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


def weekly_castaneda_enabled() -> bool:
    return bool(state.get("weekly_castaneda_enabled", WEEKLY_CASTANEDA_ENABLED))


def weekly_spiritual_questions_enabled() -> bool:
    return bool(state.get("weekly_spiritual_questions_enabled", WEEKLY_SPIRITUAL_QUESTIONS_ENABLED))


def spiritual_questions_finished() -> bool:
    try:
        index = int(state.get("weekly_spiritual_questions_index", 0) or 0)
    except (TypeError, ValueError):
        index = 0
    return index >= len(SPIRITUAL_QUESTIONS)


def set_weekly_castaneda_enabled(enabled: bool) -> None:
    state["weekly_castaneda_enabled"] = enabled
    if enabled:
        ensure_weekly_castaneda_next_run(force=True)
    else:
        state["weekly_castaneda_next_run"] = None
    save_state()


def threads_status_text() -> str:
    status = "ON" if threads_enabled() else "OFF"
    threads_last_error = state.get("threads_last_error")
    if threads_last_error:
        status += f" ({threads_last_error})"
    bluesky_status = "ON" if bluesky_enabled() else "OFF"
    if bluesky_enabled() and not bluesky_configured():
        bluesky_status += " (missing handle/app password)"
    weekly_status = "ON" if weekly_castaneda_enabled() else "OFF"
    next_run = format_weekly_castaneda_next_run()
    questions_status = "ON" if weekly_spiritual_questions_enabled() else "OFF"
    next_questions = format_weekly_spiritual_questions_next_run()
    return f"Threads posting: {status}\nBluesky posting: {bluesky_status}\nMax thread parts: {get_max_thread_parts()}\nWeekly Castaneda: {weekly_status}\nNext Castaneda: {next_run}\nWeekly questions: {questions_status}\nNext question: {next_questions}"


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


async def weekly_castaneda_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_non_admin(update):
        return
    set_weekly_castaneda_enabled(not weekly_castaneda_enabled())
    await update.effective_message.reply_text(threads_status_text())


async def post_castaneda_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_non_admin(update):
        return
    post_to_threads = threads_enabled()
    post_to_bluesky = bluesky_enabled() and bluesky_configured()

    if bluesky_enabled() and not bluesky_configured():
        logger.warning("Skipping manual Castaneda Bluesky post: missing handle/app password")

    if not post_to_threads and not post_to_bluesky:
        await update.effective_message.reply_text("Threads and Bluesky posting are OFF or not configured.\n\n" + threads_status_text())
        return

    entry = newest_unpublished_castaneda_for_manual(post_to_threads=post_to_threads, post_to_bluesky=post_to_bluesky)
    if not entry:
        await update.effective_message.reply_text("No unpublished Castaneda quotes left for enabled platforms yet.")
        return

    text = strip_html_tags(str(entry.get("text") or "").strip())
    image_url = entry.get("image_url") or None
    preview = publication_preview(text or "Castaneda quote")

    threads_post_ids: list[str] = []
    bluesky_post_uris: list[str] = []
    failures: list[str] = []

    if post_to_threads and not castaneda_entry_was_published_to_threads(entry):
        parts = append_castaneda_telegram_link(split_text_for_threads(text) if text else ["Castaneda quote"])
        progress_message = await send_publication_progress(context, preview, len(parts), platform="Threads")
        uploaded_count = 0

        try:
            async def report_threads_progress(uploaded: int, total: int, post_id: str) -> None:
                nonlocal uploaded_count
                uploaded_count = uploaded
                await update_publication_progress(progress_message, preview, total, uploaded, platform="Threads")

            threads_post_ids = await publish_threads_chain_with_progress(parts, image_url=image_url, progress_callback=report_threads_progress)
            clear_threads_error()
            mark_castaneda_threads_published(entry)
            await update_publication_progress(progress_message, preview, len(parts), uploaded_count, status="Done", platform="Threads")
        except Exception as exc:
            remember_threads_error(exc)
            failures.append("Threads: " + platform_error_status(exc))
            logger.exception("Failed to publish manual Castaneda post to Threads")
            await update_publication_progress(progress_message, preview, len(parts), uploaded_count, status=platform_error_status(exc), platform="Threads")

    if post_to_bluesky and not castaneda_entry_was_published_to_bluesky(entry):
        parts = split_text_for_bluesky(text) if text else ["Castaneda quote"]
        if CASTANEDA_TELEGRAM_LINK:
            parts = append_suffix_to_thread_parts(
                parts,
                f"\n\nMore daily quotes in Telegram:\n{CASTANEDA_TELEGRAM_LINK}",
                limit=BLUESKY_MAX_CHARS,
                max_parts=BLUESKY_MAX_PARTS,
            )
        progress_message = await send_publication_progress(context, preview, len(parts), platform="Bluesky")
        uploaded_count = 0

        try:
            async def report_bluesky_progress(uploaded: int, total: int, post_uri: str) -> None:
                nonlocal uploaded_count
                uploaded_count = uploaded
                await update_publication_progress(progress_message, preview, total, uploaded, platform="Bluesky")

            bluesky_post_uris = await publish_bluesky_chain_with_progress(parts, image_url=image_url, progress_callback=report_bluesky_progress)
            mark_castaneda_bluesky_published(entry)
            await update_publication_progress(progress_message, preview, len(parts), uploaded_count, status="Done", platform="Bluesky")
        except Exception:
            failures.append("Bluesky: failed")
            logger.exception("Failed to publish manual Castaneda post to Bluesky")
            await update_publication_progress(progress_message, preview, len(parts), uploaded_count, status="Failed", platform="Bluesky")

    summary_parts = []
    if threads_post_ids:
        summary_parts.append("Threads: " + ", ".join(threads_post_ids))
    elif post_to_threads:
        summary_parts.append("Threads: already posted")
    if bluesky_post_uris:
        summary_parts.append("Bluesky: " + ", ".join(bluesky_post_uris))
    elif post_to_bluesky:
        summary_parts.append("Bluesky: already posted")
    summary_parts.extend(failures)
    await update.effective_message.reply_text("Castaneda post finished.\n" + "\n".join(summary_parts))


async def bluesky_toggle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_non_admin(update):
        return
    set_bluesky_enabled(not bluesky_enabled())
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
    public_scopes = [
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeAllGroupChats(),
        BotCommandScopeAllChatAdministrators(),
    ]
    for scope in public_scopes:
        await app.bot.delete_my_commands(scope=scope)

    if not ADMIN_USER_ID:
        logger.warning("ADMIN_USER_ID is not set; Telegram command menu will not be scoped to admin")
        return

    commands = [
        BotCommand("threads", "Threads on/off"),
        BotCommand("bluesky", "Bluesky on/off"),
        BotCommand("weekly_castaneda", "Weekly Castaneda on/off"),
        BotCommand("post_castaneda", "Post Castaneda now"),
        BotCommand("threads_parts", "Set max thread parts"),
        BotCommand("threads_status", "Threads status"),
    ]
    await app.bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=int(ADMIN_USER_ID)))


def chat_candidates(message: Message) -> set[str]:
    chat = message.chat
    candidates = {str(chat.id)}
    if chat.username:
        candidates.add(f"@{chat.username}")
        candidates.add(chat.username)
    return candidates


def message_matches_source(message: Message) -> bool:
    candidates = chat_candidates(message)
    return any(source in candidates for source in SOURCE_CHANNEL_IDS)


def message_matches_castaneda_channel(message: Message) -> bool:
    if not CASTANEDA_CHANNEL_ID:
        return False
    return CASTANEDA_CHANNEL_ID in chat_candidates(message)


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


def append_suffix_to_thread_parts(
    parts: list[str],
    suffix: str,
    limit: int = MAX_THREAD_CHARS,
    max_parts: Optional[int] = None,
) -> list[str]:
    if not suffix:
        return parts
    parts = list(parts) or [""]
    if len(parts[-1]) + len(suffix) <= limit:
        parts[-1] = parts[-1].rstrip() + suffix
        return parts

    if max_parts is None:
        max_parts = get_max_thread_parts()
    if len(parts) < max_parts and len(suffix.strip()) <= limit:
        parts.append(suffix.strip())
        return parts

    available = max(1, limit - len(suffix))
    parts[-1] = trim_to_sentence_or_readable_boundary(parts[-1], available).rstrip() + suffix
    return parts


def append_castaneda_telegram_link(parts: list[str]) -> list[str]:
    if not CASTANEDA_TELEGRAM_LINK:
        return parts
    suffix = f"\n\nMore daily quotes in Telegram:\n{CASTANEDA_TELEGRAM_LINK}"
    return append_suffix_to_thread_parts(parts, suffix)


def castaneda_post_key(entry: dict) -> Optional[str]:
    chat_id = entry.get("chat_id")
    message_id = entry.get("message_id")
    if chat_id is None or message_id is None:
        return None
    return f"{chat_id}:{message_id}"


def castaneda_threads_published_keys() -> set[str]:
    return {str(key) for key in state.get("castaneda_threads_published_message_ids", []) if key}


def castaneda_bluesky_published_keys() -> set[str]:
    return {str(key) for key in state.get("castaneda_bluesky_published_message_ids", []) if key}


def mark_castaneda_threads_published(entry: dict) -> None:
    key = castaneda_post_key(entry)
    if not key:
        return
    published = list(state.get("castaneda_threads_published_message_ids", []))
    if key not in published:
        published.append(key)
    state["castaneda_threads_published_message_ids"] = published[-1000:]
    save_state()


def mark_castaneda_bluesky_published(entry: dict) -> None:
    key = castaneda_post_key(entry)
    if not key:
        return
    published = list(state.get("castaneda_bluesky_published_message_ids", []))
    if key not in published:
        published.append(key)
    state["castaneda_bluesky_published_message_ids"] = published[-1000:]
    save_state()


def castaneda_entry_was_published_to_threads(entry: dict) -> bool:
    key = castaneda_post_key(entry)
    return bool(key and key in castaneda_threads_published_keys())


def castaneda_entry_was_published_to_bluesky(entry: dict) -> bool:
    key = castaneda_post_key(entry)
    return bool(key and key in castaneda_bluesky_published_keys())


def index_castaneda_post(entry: dict) -> None:
    key = castaneda_post_key(entry)
    if not key:
        return
    indexed = [
        item for item in state.get("castaneda_post_index", [])
        if castaneda_post_key(item) != key
    ]
    indexed.append(entry)
    state["castaneda_post_index"] = indexed[-500:]


def fallback_state_key_for_platform(platform: str) -> str:
    if platform == "manual":
        return "castaneda_manual_fallback_index"
    if platform == "bluesky":
        return "castaneda_bluesky_fallback_index"
    return "castaneda_threads_fallback_index"


def fallback_castaneda_entry(index: int, quote: str, media_urls: list[str]) -> dict:
    return {
        "text": quote,
        "image_url": media_urls[index % len(media_urls)] if media_urls else None,
        "telegram_url": CASTANEDA_TELEGRAM_LINK,
        "message_id": f"quote-{index}",
        "chat_id": "fallback-castaneda",
        "quote_index": index,
        "source": "castaneda_quotes_file",
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }


def newest_unpublished_castaneda_from_files(platform: str, published: set[str]) -> Optional[dict]:
    quotes = load_castaneda_quotes()
    if not quotes:
        return None
    media_urls = load_castaneda_media_urls()
    state_key = fallback_state_key_for_platform(platform)
    start_index = int(state.get(state_key, 0) or 0) % len(quotes)

    for offset in range(len(quotes)):
        quote_index = (start_index + offset) % len(quotes)
        entry = fallback_castaneda_entry(quote_index, quotes[quote_index], media_urls)
        key = castaneda_post_key(entry)
        if key and key not in published:
            state[state_key] = (quote_index + 1) % len(quotes)
            return entry
    return None


def newest_unpublished_castaneda_for_threads() -> Optional[dict]:
    published = castaneda_threads_published_keys()
    return newest_unpublished_castaneda_for_platform("threads", published)


def newest_unpublished_castaneda_for_bluesky() -> Optional[dict]:
    published = castaneda_bluesky_published_keys()
    return newest_unpublished_castaneda_for_platform("bluesky", published)


def newest_unpublished_castaneda_for_manual(post_to_threads: bool, post_to_bluesky: bool) -> Optional[dict]:
    published_sets: list[set[str]] = []
    if post_to_threads:
        published_sets.append(castaneda_threads_published_keys())
    if post_to_bluesky:
        published_sets.append(castaneda_bluesky_published_keys())
    if not published_sets:
        return None
    fully_published = set.intersection(*published_sets) if len(published_sets) > 1 else published_sets[0]
    return newest_unpublished_castaneda_for_platform("manual", fully_published)


def newest_unpublished_castaneda_for_platform(platform: str, published: set[str]) -> Optional[dict]:
    candidates = list(state.get("castaneda_post_index", []))
    latest = state.get("latest_castaneda_post")
    if isinstance(latest, dict):
        latest_key = castaneda_post_key(latest)
        if latest_key and all(castaneda_post_key(item) != latest_key for item in candidates):
            candidates.append(latest)

    seen: set[str] = set()
    for entry in reversed(candidates):
        if not isinstance(entry, dict):
            continue
        key = castaneda_post_key(entry)
        if not key or key in seen or key in published:
            continue
        seen.add(key)
        if entry.get("text") or entry.get("image_url"):
            return entry
    return newest_unpublished_castaneda_from_files(platform, published)


def split_text_for_threads(
    text: str,
    limit: int = MAX_THREAD_CHARS,
    max_parts: Optional[int] = None,
    source_url: Optional[str] = None,
    truncation_suffix: Optional[str] = None,
) -> list[str]:
    if max_parts is None:
        max_parts = get_max_thread_parts()
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= limit:
        return [text]

    if truncation_suffix is None:
        suffix = f"\n\nMore in Telegram:\n{source_url}" if source_url else "\n\nFull text in Telegram."
    else:
        suffix = truncation_suffix
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


def text_exceeds_social_capacity(text: str, limit: int, max_parts: int) -> bool:
    normalized = re.sub(r"\n{3,}", "\n\n", text.strip())
    return len(normalized) > max(1, limit) * max(1, max_parts)


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


def trim_to_sentence_or_readable_boundary(text: str, limit: int) -> str:
    piece = text[:limit].rstrip()
    if not piece:
        return piece

    sentence_ends = [match.end() for match in re.finditer(r"[.!?][\"')\]]*(?=\s|$)", piece)]
    if sentence_ends:
        return piece[: sentence_ends[-1]].rstrip()

    return trim_to_readable_boundary(piece)


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


def platform_error_status(exc: Exception) -> str:
    text = str(exc)
    if "API access blocked" in text:
        return "Failed: API access blocked"
    if "Error validating access token" in text or "Invalid OAuth" in text:
        return "Failed: invalid token"
    compact = re.sub(r"\s+", " ", text).strip()
    return f"Failed: {compact[:80]}" if compact else "Failed"


def remember_threads_error(exc: Exception) -> None:
    state["threads_last_error"] = platform_error_status(exc).replace("Failed: ", "", 1)
    save_state()


def clear_threads_error() -> None:
    if "threads_last_error" in state:
        state.pop("threads_last_error", None)
        save_state()


def check_threads_api_access() -> None:
    if not threads_enabled() or not THREADS_USER_ID or not THREADS_ACCESS_TOKEN:
        return
    try:
        response = requests.get(
            f"{THREADS_API_BASE}/me",
            params={"fields": "id,username", "access_token": THREADS_ACCESS_TOKEN},
            timeout=20,
        )
        raise_for_threads_response(response, "access check")
        clear_threads_error()
        logger.info("Threads API access check passed")
    except Exception as exc:
        remember_threads_error(exc)
        logger.warning("Threads API access check failed: %s", platform_error_status(exc))


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


def should_retry_threads_publish(response: requests.Response) -> bool:
    if response.status_code >= 500:
        return True
    try:
        error = response.json().get("error", {})
    except ValueError:
        return False
    return error.get("code") == 24 or error.get("error_subcode") == 4279009


def publish_threads_container(container_id: str) -> str:
    last_response: Optional[requests.Response] = None
    for attempt in range(1, THREADS_PUBLISH_RETRY_ATTEMPTS + 1):
        response = requests.post(
            f"{THREADS_API_BASE}/{THREADS_USER_ID}/threads_publish",
            data={"creation_id": container_id, "access_token": THREADS_ACCESS_TOKEN},
            timeout=30,
        )
        last_response = response
        if response.ok:
            post_id = response.json().get("id")
            if not post_id:
                raise RuntimeError(f"Threads publish response did not include id: {response.text}")
            return post_id

        if attempt < THREADS_PUBLISH_RETRY_ATTEMPTS and should_retry_threads_publish(response):
            logger.warning(
                "Threads publish attempt %s/%s failed with retryable response: %s",
                attempt,
                THREADS_PUBLISH_RETRY_ATTEMPTS,
                response.text,
            )
            time.sleep(THREADS_PUBLISH_RETRY_DELAY_SECONDS)
            continue

        raise_for_threads_response(response, "publish")

    if last_response is not None:
        raise_for_threads_response(last_response, "publish")
    raise RuntimeError("Threads publish failed without a response")


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


async def publish_threads_chain_with_progress(
    parts: Iterable[str],
    image_url: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, str], Awaitable[None]]] = None,
) -> list[str]:
    parts = list(parts)
    post_ids: list[str] = []
    previous_id: Optional[str] = None
    total = len(parts)

    for index, part in enumerate(parts):
        if previous_id and THREADS_REPLY_DELAY_SECONDS > 0:
            await asyncio.sleep(THREADS_REPLY_DELAY_SECONDS)
        logger.info("Publishing Threads part %s/%s (%s chars)", index + 1, total, len(part))
        post_id = publish_threads_post(part, image_url=image_url if index == 0 else None, reply_to_id=previous_id)
        post_ids.append(post_id)
        previous_id = post_id
        if progress_callback:
            await progress_callback(index + 1, total, post_id)

    return post_ids




def normalize_bluesky_tag(tag: str) -> Optional[str]:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", tag.strip().lstrip("#"))
    if not cleaned:
        return None
    return f"#{cleaned[:64]}"


def count_tag_keyword(text: str, keyword: str) -> int:
    if " " in keyword:
        return len(re.findall(re.escape(keyword), text))
    return len(re.findall(rf"\b{re.escape(keyword)}\b", text))


def infer_bluesky_tags(text: str) -> list[str]:
    normalized_text = text.lower()
    max_tags = max(1, BLUESKY_MAX_TAGS_PER_POST)
    min_tags = min(max(0, BLUESKY_MIN_TAGS), max_tags)
    scored_tags: list[tuple[int, int, str]] = []

    for order, (tag, keywords) in enumerate(SEMANTIC_TAG_RULES):
        score = sum(count_tag_keyword(normalized_text, keyword) for keyword in keywords)
        if score:
            scored_tags.append((-score, order, tag))

    selected: list[str] = []
    for _, _, tag in sorted(scored_tags):
        normalized_tag = normalize_bluesky_tag(tag)
        if normalized_tag and normalized_tag not in selected:
            selected.append(normalized_tag)
        if len(selected) >= max_tags:
            return selected

    fallback_tags = BLUESKY_TAGS or DEFAULT_BLUESKY_TAGS
    for tag in fallback_tags:
        normalized_tag = normalize_bluesky_tag(tag)
        if normalized_tag and normalized_tag not in selected:
            selected.append(normalized_tag)
        if len(selected) >= max_tags:
            break

    if len(selected) < min_tags:
        return selected
    return selected[:max_tags]


def append_bluesky_tags(parts: list[str], source_text: str) -> list[str]:
    tags = infer_bluesky_tags(source_text)
    if not tags:
        return parts
    parts = list(parts) or [""]

    for tag_count in range(len(tags), 0, -1):
        suffix = "\n\n" + " ".join(tags[:tag_count])
        if len(parts[-1].rstrip()) + len(suffix) <= BLUESKY_MAX_CHARS:
            parts[-1] = parts[-1].rstrip() + suffix
            return parts

    return parts


def split_text_for_bluesky(text: str, source_url: Optional[str] = None) -> list[str]:
    truncated = bool(source_url) and text_exceeds_social_capacity(text, BLUESKY_MAX_CHARS, BLUESKY_MAX_PARTS)
    parts = split_text_for_threads(
        text,
        limit=BLUESKY_MAX_CHARS,
        max_parts=BLUESKY_MAX_PARTS,
        truncation_suffix="",
    )
    parts = append_bluesky_tags(parts, text)
    if truncated and source_url:
        parts = append_suffix_to_thread_parts(
            parts,
            f"\n\nMore in Telegram:\n{source_url}",
            limit=BLUESKY_MAX_CHARS,
            max_parts=BLUESKY_MAX_PARTS,
        )
    return parts


def create_bluesky_session() -> dict:
    if not bluesky_configured():
        raise RuntimeError("Bluesky credentials are missing: add BLUESKY_HANDLE and BLUESKY_APP_PASSWORD")
    response = requests.post(
        f"{BLUESKY_SERVICE}/xrpc/com.atproto.server.createSession",
        json={"identifier": BLUESKY_HANDLE, "password": BLUESKY_APP_PASSWORD},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def download_image_for_bluesky(image_url: str) -> Optional[tuple[bytes, str]]:
    response = requests.get(image_url, timeout=30)
    response.raise_for_status()
    content = response.content
    if len(content) > BLUESKY_MAX_IMAGE_BYTES:
        logger.warning("Skipping Bluesky image: %s bytes is larger than %s", len(content), BLUESKY_MAX_IMAGE_BYTES)
        return None
    content_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
    if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        content_type = "image/jpeg"
    return content, content_type


def upload_bluesky_image(session: dict, image_url: Optional[str]) -> Optional[dict]:
    if not image_url:
        return None
    downloaded = download_image_for_bluesky(image_url)
    if not downloaded:
        return None
    content, content_type = downloaded
    response = requests.post(
        f"{BLUESKY_SERVICE}/xrpc/com.atproto.repo.uploadBlob",
        headers={"Authorization": f"Bearer {session['accessJwt']}", "Content-Type": content_type},
        data=content,
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("blob")


def bluesky_urls_with_byte_ranges(text: str) -> list[tuple[str, int, int]]:
    urls: list[tuple[str, int, int]] = []
    for match in URL_PATTERN.finditer(text):
        raw_url = match.group(0)
        url = raw_url.rstrip(".,!?;:)]}")
        if not url:
            continue
        start_char = match.start()
        end_char = start_char + len(url)
        byte_start = len(text[:start_char].encode("utf-8"))
        byte_end = len(text[:end_char].encode("utf-8"))
        urls.append((url, byte_start, byte_end))
    return urls


def bluesky_link_facets(text: str) -> list[dict]:
    facets = []
    for url, byte_start, byte_end in bluesky_urls_with_byte_ranges(text):
        facets.append(
            {
                "index": {"byteStart": byte_start, "byteEnd": byte_end},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
            }
        )
    return facets


def bluesky_external_embed_for_text(text: str) -> Optional[dict]:
    urls = bluesky_urls_with_byte_ranges(text)
    if not urls:
        return None
    url = urls[0][0]
    if "t.me/" in url or "telegram.me/" in url:
        title = "More in Telegram"
        description = "Open the original Telegram channel or post."
    else:
        title = "Open link"
        description = url
    return {
        "$type": "app.bsky.embed.external",
        "external": {
            "uri": url,
            "title": title,
            "description": description,
        },
    }


def create_bluesky_record(session: dict, text: str, image_blob: Optional[dict] = None, reply: Optional[dict] = None) -> dict:
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    facets = bluesky_link_facets(text)
    if facets:
        record["facets"] = facets
    if image_blob:
        record["embed"] = {
            "$type": "app.bsky.embed.images",
            "images": [{"alt": BLUESKY_IMAGE_ALT, "image": image_blob}],
        }
    else:
        external_embed = bluesky_external_embed_for_text(text)
        if external_embed:
            record["embed"] = external_embed
    if reply:
        record["reply"] = reply

    response = requests.post(
        f"{BLUESKY_SERVICE}/xrpc/com.atproto.repo.createRecord",
        headers={"Authorization": f"Bearer {session['accessJwt']}"},
        json={"repo": session["did"], "collection": "app.bsky.feed.post", "record": record},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def publish_bluesky_chain(parts: Iterable[str], image_url: Optional[str] = None) -> list[str]:
    if not bluesky_enabled() or not bluesky_configured():
        return []
    parts = list(parts)
    session = create_bluesky_session()
    image_blob = upload_bluesky_image(session, image_url)
    post_uris: list[str] = []
    root_ref: Optional[dict] = None
    parent_ref: Optional[dict] = None

    for index, part in enumerate(parts):
        logger.info("Publishing Bluesky part %s/%s (%s chars)", index + 1, len(parts), len(part))
        reply = {"root": root_ref, "parent": parent_ref} if root_ref and parent_ref else None
        created = create_bluesky_record(session, part, image_blob=image_blob if index == 0 else None, reply=reply)
        ref = {"uri": created["uri"], "cid": created["cid"]}
        if root_ref is None:
            root_ref = ref
        parent_ref = ref
        post_uris.append(created["uri"])
    return post_uris


async def publish_bluesky_chain_with_progress(
    parts: Iterable[str],
    image_url: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, str], Awaitable[None]]] = None,
) -> list[str]:
    if not bluesky_enabled() or not bluesky_configured():
        return []
    parts = list(parts)
    session = create_bluesky_session()
    image_blob = upload_bluesky_image(session, image_url)
    post_uris: list[str] = []
    root_ref: Optional[dict] = None
    parent_ref: Optional[dict] = None
    total = len(parts)

    for index, part in enumerate(parts):
        logger.info("Publishing Bluesky part %s/%s (%s chars)", index + 1, total, len(part))
        reply = {"root": root_ref, "parent": parent_ref} if root_ref and parent_ref else None
        created = create_bluesky_record(session, part, image_blob=image_blob if index == 0 else None, reply=reply)
        ref = {"uri": created["uri"], "cid": created["cid"]}
        if root_ref is None:
            root_ref = ref
        parent_ref = ref
        post_uris.append(created["uri"])
        if progress_callback:
            await progress_callback(index + 1, total, created["uri"])

    return post_uris


def publication_preview(text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "[image only]")
    if len(first_line) > 140:
        return first_line[:137].rstrip() + "..."
    return first_line


def publication_progress_text(preview: str, total: int, uploaded: int, status: str = "Publishing", platform: str = "Threads") -> str:
    return f"{status} to {platform}\n{preview}\nParts: {total}\n{uploaded}/{total} uploaded"


async def send_publication_progress(
    context: Optional[ContextTypes.DEFAULT_TYPE],
    preview: str,
    total: int,
    platform: str = "Threads",
    bot=None,
) -> Optional[Message]:
    if not ADMIN_USER_ID:
        return None
    telegram_bot = bot or (context.bot if context else None)
    if not telegram_bot:
        return None
    try:
        return await telegram_bot.send_message(
            chat_id=int(ADMIN_USER_ID),
            text=publication_progress_text(preview, total, 0, platform=platform),
        )
    except Exception:
        logger.exception("Failed to send publication progress message to admin")
        return None


async def update_publication_progress(
    progress_message: Optional[Message],
    preview: str,
    total: int,
    uploaded: int,
    status: str = "Publishing",
    platform: str = "Threads",
) -> None:
    if not progress_message:
        return
    try:
        await progress_message.edit_text(publication_progress_text(preview, total, uploaded, status=status, platform=platform))
    except Exception:
        logger.exception("Failed to update publication progress message")


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
    image_url: Optional[str] = None
    try:
        image_url = await upload_telegram_photo_to_r2(message, context)
    except Exception:
        logger.exception("Failed to upload Telegram image for message %s", message.message_id)
        return

    is_castaneda_post = message_matches_castaneda_channel(message)
    if is_castaneda_post:
        remember_latest_castaneda_post(message, text, image_url)
        if not CROSSPOST_CASTANEDA_IMMEDIATELY:
            remember_message(message.message_id)
            logger.info("Stored Castaneda message %s for weekly posting only", message.message_id)
            return

    bluesky_active = bluesky_enabled() and bluesky_configured()
    if bluesky_enabled() and not bluesky_configured():
        logger.warning("Skipping Bluesky for Telegram message %s: missing handle/app password", message.message_id)

    if not threads_enabled() and not bluesky_active:
        logger.info("Skipping Telegram message %s: Threads and Bluesky posting are disabled", message.message_id)
        remember_message(message.message_id)
        return

    preview = publication_preview(text)
    progress_message: Optional[Message] = None
    bluesky_progress_message: Optional[Message] = None
    uploaded_count = 0
    bluesky_uploaded_count = 0
    total_parts = 0
    bluesky_total_parts = 0
    post_ids: list[str] = []
    bluesky_post_uris: list[str] = []

    if threads_enabled():
        try:
            parts = maybe_add_telegram_link(split_text_for_threads(text, source_url=telegram_post_url(message)))
            total_parts = len(parts)
            logger.info("Telegram message %s split into %s Threads part(s): %s", message.message_id, total_parts, [len(part) for part in parts])
            progress_message = await send_publication_progress(context, preview, total_parts)

            async def report_progress(uploaded: int, total: int, post_id: str) -> None:
                nonlocal uploaded_count
                uploaded_count = uploaded
                await update_publication_progress(progress_message, preview, total, uploaded, platform="Threads")

            post_ids = await publish_threads_chain_with_progress(parts, image_url=image_url, progress_callback=report_progress)
            clear_threads_error()
            await update_publication_progress(progress_message, preview, total_parts, uploaded_count, status="Done", platform="Threads")
        except Exception as exc:
            remember_threads_error(exc)
            logger.exception("Failed to crosspost Telegram message %s to Threads", message.message_id)
            if progress_message:
                await update_publication_progress(progress_message, preview, total_parts, uploaded_count, status=platform_error_status(exc), platform="Threads")

    if bluesky_active:
        try:
            bluesky_parts = split_text_for_bluesky(text, source_url=telegram_post_url(message))
            bluesky_total_parts = len(bluesky_parts)
            logger.info("Telegram message %s split into %s Bluesky part(s): %s", message.message_id, bluesky_total_parts, [len(part) for part in bluesky_parts])
            bluesky_progress_message = await send_publication_progress(context, preview, bluesky_total_parts, platform="Bluesky")

            async def report_bluesky_progress(uploaded: int, total: int, post_uri: str) -> None:
                nonlocal bluesky_uploaded_count
                bluesky_uploaded_count = uploaded
                await update_publication_progress(bluesky_progress_message, preview, total, uploaded, platform="Bluesky")

            bluesky_post_uris = await publish_bluesky_chain_with_progress(bluesky_parts, image_url=image_url, progress_callback=report_bluesky_progress)
            await update_publication_progress(bluesky_progress_message, preview, bluesky_total_parts, bluesky_uploaded_count, status="Done", platform="Bluesky")
        except Exception:
            logger.exception("Failed to crosspost Telegram message %s to Bluesky", message.message_id)
            if bluesky_progress_message:
                await update_publication_progress(bluesky_progress_message, preview, bluesky_total_parts, bluesky_uploaded_count, status="Failed", platform="Bluesky")

    if not post_ids and not bluesky_post_uris:
        logger.error("Telegram message %s was not crossposted to any platform", message.message_id)
        return

    state["posted_count"] = int(state.get("posted_count", 0)) + 1
    remember_message(message.message_id, save=False)
    save_state()
    if threads_enabled():
        logger.info("Crossposted Telegram message %s to Threads posts: %s", message.message_id, ", ".join(post_ids))
    if bluesky_active:
        logger.info("Crossposted Telegram message %s to Bluesky posts: %s", message.message_id, ", ".join(bluesky_post_uris))


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


def parse_hhmm(value: str) -> tuple[int, int]:
    hour_text, minute_text = value.split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time: {value}")
    return hour, minute


def timezone_now() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def day_index(day_name: str, default: int) -> int:
    aliases = {
        "mon": 0,
        "monday": 0,
        "tue": 1,
        "tuesday": 1,
        "wed": 2,
        "wednesday": 2,
        "thu": 3,
        "thursday": 3,
        "fri": 4,
        "friday": 4,
        "sat": 5,
        "saturday": 5,
        "sun": 6,
        "sunday": 6,
    }
    return aliases.get(day_name, default)


def weekly_day_index() -> int:
    return day_index(WEEKLY_CASTANEDA_DAY, 3)


def weekly_spiritual_questions_day_index() -> int:
    return day_index(WEEKLY_SPIRITUAL_QUESTIONS_DAY, 2)


def next_random_weekly_castaneda_run(after: Optional[datetime] = None) -> datetime:
    now = (after or timezone_now()).astimezone(ZoneInfo(TIMEZONE))
    target_day = weekly_day_index()
    days_ahead = (target_day - now.weekday()) % 7
    start_hour, start_minute = parse_hhmm(WEEKLY_CASTANEDA_START_TIME)
    end_hour, end_minute = parse_hhmm(WEEKLY_CASTANEDA_END_TIME)
    start_total = start_hour * 60 + start_minute
    end_total = end_hour * 60 + end_minute
    if end_total < start_total:
        raise ValueError("WEEKLY_CASTANEDA_END_TIME must be later than WEEKLY_CASTANEDA_START_TIME")

    if days_ahead == 0:
        today_latest = datetime.combine(now.date(), datetime_time(end_hour, end_minute), tzinfo=now.tzinfo)
        if now >= today_latest:
            days_ahead = 7

    run_date = (now + timedelta(days=days_ahead)).date()
    random_minute = random.randint(start_total, end_total)
    run_at = datetime.combine(
        run_date,
        datetime_time(random_minute // 60, random_minute % 60),
        tzinfo=now.tzinfo,
    )
    if run_at <= now:
        run_at = next_random_weekly_castaneda_run(after=now + timedelta(days=1))
    return run_at


def next_random_weekly_spiritual_questions_run(after: Optional[datetime] = None) -> datetime:
    now = (after or timezone_now()).astimezone(ZoneInfo(TIMEZONE))
    target_day = weekly_spiritual_questions_day_index()
    days_ahead = (target_day - now.weekday()) % 7
    start_hour, start_minute = parse_hhmm(WEEKLY_SPIRITUAL_QUESTIONS_START_TIME)
    end_hour, end_minute = parse_hhmm(WEEKLY_SPIRITUAL_QUESTIONS_END_TIME)
    start_total = start_hour * 60 + start_minute
    end_total = end_hour * 60 + end_minute
    if end_total < start_total:
        raise ValueError("WEEKLY_SPIRITUAL_QUESTIONS_END_TIME must be later than WEEKLY_SPIRITUAL_QUESTIONS_START_TIME")

    if days_ahead == 0:
        today_latest = datetime.combine(now.date(), datetime_time(end_hour, end_minute), tzinfo=now.tzinfo)
        if now >= today_latest:
            days_ahead = 7

    run_date = (now + timedelta(days=days_ahead)).date()
    random_minute = random.randint(start_total, end_total)
    run_at = datetime.combine(
        run_date,
        datetime_time(random_minute // 60, random_minute % 60),
        tzinfo=now.tzinfo,
    )
    if run_at <= now:
        run_at = next_random_weekly_spiritual_questions_run(after=now + timedelta(days=1))
    return run_at


def parse_state_datetime(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(TIMEZONE))
    return parsed.astimezone(ZoneInfo(TIMEZONE))


def ensure_weekly_castaneda_next_run(force: bool = False) -> Optional[datetime]:
    if not weekly_castaneda_enabled():
        return None
    now = timezone_now()
    run_at = parse_state_datetime(state.get("weekly_castaneda_next_run"))
    if force or run_at is None:
        run_at = next_random_weekly_castaneda_run(after=now)
        state["weekly_castaneda_next_run"] = run_at.isoformat()
        save_state()
    return run_at


def ensure_weekly_spiritual_questions_next_run(force: bool = False) -> Optional[datetime]:
    if not weekly_spiritual_questions_enabled():
        return None
    if spiritual_questions_finished():
        state["weekly_spiritual_questions_enabled"] = False
        state["weekly_spiritual_questions_next_run"] = None
        save_state()
        return None
    now = timezone_now()
    run_at = parse_state_datetime(state.get("weekly_spiritual_questions_next_run"))
    if force or run_at is None:
        run_at = next_random_weekly_spiritual_questions_run(after=now)
        state["weekly_spiritual_questions_next_run"] = run_at.isoformat()
        save_state()
    return run_at


def format_weekly_castaneda_next_run() -> str:
    if not weekly_castaneda_enabled():
        return "OFF"
    run_at = ensure_weekly_castaneda_next_run()
    if not run_at:
        return "not scheduled"
    return run_at.strftime("%a %Y-%m-%d %H:%M %Z")


def format_weekly_spiritual_questions_next_run() -> str:
    if spiritual_questions_finished():
        return "finished"
    if not weekly_spiritual_questions_enabled():
        return "OFF"
    run_at = ensure_weekly_spiritual_questions_next_run()
    if not run_at:
        return "not scheduled"
    return run_at.strftime("%a %Y-%m-%d %H:%M %Z")


def remember_latest_castaneda_post(message: Message, text: str, image_url: Optional[str]) -> None:
    entry = {
        "text": text,
        "image_url": image_url,
        "telegram_url": telegram_post_url(message),
        "message_id": message.message_id,
        "chat_id": message.chat_id,
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }
    state["latest_castaneda_post"] = entry
    index_castaneda_post(entry)
    save_state()
    logger.info("Remembered latest Castaneda post from Telegram message %s", message.message_id)


def schedule_next_weekly_castaneda() -> None:
    run_at = next_random_weekly_castaneda_run(after=timezone_now() + timedelta(days=1))
    state["weekly_castaneda_next_run"] = run_at.isoformat()
    save_state()
    logger.info("Next weekly Castaneda post scheduled for %s", run_at.isoformat())


def schedule_next_weekly_spiritual_questions() -> None:
    if spiritual_questions_finished():
        state["weekly_spiritual_questions_enabled"] = False
        state["weekly_spiritual_questions_next_run"] = None
        save_state()
        logger.info("Weekly spiritual questions are finished; no next run scheduled")
        return
    run_at = next_random_weekly_spiritual_questions_run(after=timezone_now() + timedelta(days=1))
    state["weekly_spiritual_questions_next_run"] = run_at.isoformat()
    save_state()
    logger.info("Next weekly spiritual question scheduled for %s", run_at.isoformat())


def next_spiritual_question() -> Optional[tuple[int, str]]:
    try:
        index = int(state.get("weekly_spiritual_questions_index", 0) or 0)
    except (TypeError, ValueError):
        index = 0
    index = max(0, index)
    if index >= len(SPIRITUAL_QUESTIONS):
        return None
    return index, SPIRITUAL_QUESTIONS[index]


def advance_spiritual_question_index(index: int) -> None:
    state["weekly_spiritual_questions_index"] = index + 1
    save_state()


async def notify_admin(text: str, bot=None) -> None:
    if not ADMIN_USER_ID:
        return
    telegram_bot = bot or ADMIN_BOT
    if not telegram_bot:
        return
    try:
        await telegram_bot.send_message(chat_id=int(ADMIN_USER_ID), text=text)
    except Exception:
        logger.exception("Failed to send admin notification")


async def finish_weekly_spiritual_questions() -> None:
    state["weekly_spiritual_questions_enabled"] = False
    state["weekly_spiritual_questions_next_run"] = None
    state["weekly_spiritual_questions_index"] = len(SPIRITUAL_QUESTIONS)
    save_state()
    logger.info("Weekly spiritual questions are finished")
    await notify_admin(
        "Weekly spiritual questions are finished.\n"
        "Please add new questions before turning this schedule back on."
    )


async def post_weekly_spiritual_question() -> None:
    bluesky_active = bluesky_enabled() and bluesky_configured()
    if bluesky_enabled() and not bluesky_configured():
        logger.warning("Skipping weekly spiritual question Bluesky post: missing handle/app password")

    if not weekly_spiritual_questions_enabled():
        return

    question_item = next_spiritual_question()
    if question_item is None:
        await finish_weekly_spiritual_questions()
        return
    question_index, question = question_item

    if not threads_enabled() and not bluesky_active:
        logger.info("Skipping weekly spiritual question: Threads and Bluesky posting are disabled")
        schedule_next_weekly_spiritual_questions()
        return

    preview = publication_preview(question)
    post_ids: list[str] = []
    bluesky_post_uris: list[str] = []

    if threads_enabled():
        progress_message: Optional[Message] = None
        uploaded_count = 0
        parts: list[str] = []
        try:
            parts = split_text_for_threads(question)
            progress_message = await send_publication_progress(None, preview, len(parts), platform="Threads", bot=ADMIN_BOT)

            async def report_threads_progress(uploaded: int, total: int, post_id: str) -> None:
                nonlocal uploaded_count
                uploaded_count = uploaded
                await update_publication_progress(progress_message, preview, total, uploaded, platform="Threads")

            post_ids = await publish_threads_chain_with_progress(parts, progress_callback=report_threads_progress)
            clear_threads_error()
            await update_publication_progress(progress_message, preview, len(parts), uploaded_count, status="Done", platform="Threads")
        except Exception as exc:
            remember_threads_error(exc)
            logger.exception("Failed to publish weekly spiritual question to Threads")
            if progress_message:
                await update_publication_progress(progress_message, preview, len(parts), uploaded_count, status=platform_error_status(exc), platform="Threads")

    if bluesky_active:
        bluesky_progress_message: Optional[Message] = None
        bluesky_uploaded_count = 0
        bluesky_parts: list[str] = []
        try:
            bluesky_parts = split_text_for_bluesky(question)
            bluesky_progress_message = await send_publication_progress(None, preview, len(bluesky_parts), platform="Bluesky", bot=ADMIN_BOT)

            async def report_bluesky_progress(uploaded: int, total: int, post_uri: str) -> None:
                nonlocal bluesky_uploaded_count
                bluesky_uploaded_count = uploaded
                await update_publication_progress(bluesky_progress_message, preview, total, uploaded, platform="Bluesky")

            bluesky_post_uris = await publish_bluesky_chain_with_progress(bluesky_parts, progress_callback=report_bluesky_progress)
            await update_publication_progress(bluesky_progress_message, preview, len(bluesky_parts), bluesky_uploaded_count, status="Done", platform="Bluesky")
        except Exception:
            logger.exception("Failed to publish weekly spiritual question to Bluesky")
            if bluesky_progress_message:
                await update_publication_progress(bluesky_progress_message, preview, len(bluesky_parts), bluesky_uploaded_count, status="Failed", platform="Bluesky")

    if post_ids or bluesky_post_uris:
        advance_spiritual_question_index(question_index)
        logger.info("Published weekly spiritual question %s", question_index)
        if spiritual_questions_finished():
            await finish_weekly_spiritual_questions()
            return
    else:
        logger.error("Weekly spiritual question was not published to any platform")

    schedule_next_weekly_spiritual_questions()


async def post_weekly_castaneda() -> None:
    bluesky_active = bluesky_enabled() and bluesky_configured()
    if bluesky_enabled() and not bluesky_configured():
        logger.warning("Skipping weekly Castaneda Bluesky post: missing handle/app password")

    if not threads_enabled() and not bluesky_active:
        logger.info("Skipping weekly Castaneda post: Threads and Bluesky posting are disabled")
        return
    if not weekly_castaneda_enabled():
        return

    latest = state.get("latest_castaneda_post") or {}
    text = strip_html_tags(str(latest.get("text") or "").strip())
    image_url = latest.get("image_url") or None
    if not text and not image_url:
        logger.warning("Weekly Castaneda is enabled, but no latest Castaneda channel post is remembered yet")
        schedule_next_weekly_castaneda()
        return

    post_ids: list[str] = []
    bluesky_post_uris: list[str] = []

    if threads_enabled():
        if castaneda_entry_was_published_to_threads(latest):
            logger.info("Skipping weekly Castaneda Threads post: message %s was already published", latest.get("message_id"))
        else:
            try:
                parts = split_text_for_threads(text) if text else ["Weekly Castaneda"]
                parts = append_castaneda_telegram_link(parts)
                post_ids = publish_threads_chain(parts, image_url=image_url)
                clear_threads_error()
                mark_castaneda_threads_published(latest)
            except Exception as exc:
                remember_threads_error(exc)
                logger.exception("Failed to publish weekly Castaneda post to Threads")

    if bluesky_active:
        if castaneda_entry_was_published_to_bluesky(latest):
            logger.info("Skipping weekly Castaneda Bluesky post: message %s was already published", latest.get("message_id"))
        else:
            preview = publication_preview(text)
            bluesky_parts: list[str] = []
            bluesky_uploaded_count = 0
            bluesky_progress_message: Optional[Message] = None
            try:
                bluesky_parts = split_text_for_bluesky(text) if text else ["Weekly Castaneda"]
                bluesky_parts = append_suffix_to_thread_parts(
                    bluesky_parts,
                    f"\n\nMore daily quotes in Telegram:\n{CASTANEDA_TELEGRAM_LINK}",
                    limit=BLUESKY_MAX_CHARS,
                    max_parts=BLUESKY_MAX_PARTS,
                )
                bluesky_progress_message = await send_publication_progress(None, preview, len(bluesky_parts), platform="Bluesky", bot=ADMIN_BOT)

                async def report_weekly_bluesky_progress(uploaded: int, total: int, post_uri: str) -> None:
                    nonlocal bluesky_uploaded_count
                    bluesky_uploaded_count = uploaded
                    await update_publication_progress(bluesky_progress_message, preview, total, uploaded, platform="Bluesky")

                bluesky_post_uris = await publish_bluesky_chain_with_progress(bluesky_parts, image_url=image_url, progress_callback=report_weekly_bluesky_progress)
                mark_castaneda_bluesky_published(latest)
                await update_publication_progress(bluesky_progress_message, preview, len(bluesky_parts), bluesky_uploaded_count, status="Done", platform="Bluesky")
            except Exception:
                logger.exception("Failed to publish weekly Castaneda post to Bluesky")
                if bluesky_progress_message:
                    await update_publication_progress(bluesky_progress_message, preview, len(bluesky_parts), bluesky_uploaded_count, status="Failed", platform="Bluesky")

    if not post_ids and not bluesky_post_uris:
        logger.error("Weekly Castaneda post was not published to any platform")
        schedule_next_weekly_castaneda()
        return

    schedule_next_weekly_castaneda()
    if threads_enabled():
        logger.info("Published weekly Castaneda post to Threads posts: %s", ", ".join(post_ids))
    if bluesky_active:
        logger.info("Published weekly Castaneda post to Bluesky posts: %s", ", ".join(bluesky_post_uris))


async def check_weekly_castaneda_due() -> None:
    if not weekly_castaneda_enabled():
        return
    run_at = parse_state_datetime(state.get("weekly_castaneda_next_run"))
    if run_at is None:
        ensure_weekly_castaneda_next_run(force=True)
        return
    if timezone_now() >= run_at:
        await post_weekly_castaneda()


async def check_weekly_spiritual_questions_due() -> None:
    if not weekly_spiritual_questions_enabled():
        return
    run_at = parse_state_datetime(state.get("weekly_spiritual_questions_next_run"))
    if run_at is None:
        ensure_weekly_spiritual_questions_next_run(force=True)
        return
    if timezone_now() >= run_at:
        await post_weekly_spiritual_question()


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
    scheduler.add_job(
        check_weekly_castaneda_due,
        "interval",
        minutes=1,
        id="weekly_castaneda_check",
        replace_existing=True,
    )
    scheduler.add_job(
        check_weekly_spiritual_questions_due,
        "interval",
        minutes=1,
        id="weekly_spiritual_questions_check",
        replace_existing=True,
    )
    if weekly_castaneda_enabled():
        run_at = ensure_weekly_castaneda_next_run()
        logger.info("Weekly Castaneda enabled: next run %s", run_at.isoformat() if run_at else "not scheduled")
    else:
        logger.info("Weekly Castaneda disabled")
    if weekly_spiritual_questions_enabled():
        run_at = ensure_weekly_spiritual_questions_next_run()
        logger.info("Weekly spiritual questions enabled: next run %s", run_at.isoformat() if run_at else "not scheduled")
    else:
        logger.info("Weekly spiritual questions disabled")
    scheduler.start()
    return scheduler


def main() -> None:
    global ADMIN_BOT
    require_env()
    if CROSSPOST_IMAGES:
        ensure_r2_configured()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(setup_bot_commands).build()
    ADMIN_BOT = app.bot
    app.add_handler(CommandHandler("threads", threads_toggle_command))
    app.add_handler(CommandHandler("bluesky", bluesky_toggle_command))
    app.add_handler(CommandHandler("weekly_castaneda", weekly_castaneda_command))
    app.add_handler(CommandHandler("post_castaneda", post_castaneda_command))
    app.add_handler(CommandHandler("threads_parts", threads_parts_command))
    app.add_handler(CommandHandler("threads_status", threads_status_command))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, admin_text_message))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))

    scheduler = configure_scheduler()
    check_threads_api_access()
    logger.info("threads_drea_bot started. Listening to %s. %s", ", ".join(SOURCE_CHANNEL_IDS), threads_status_text())
    try:
        app.run_polling(allowed_updates=["channel_post", "message"])
    finally:
        try:
            scheduler.shutdown(wait=False)
        except RuntimeError as exc:
            if "Event loop is closed" not in str(exc):
                raise


if __name__ == "__main__":
    main()
