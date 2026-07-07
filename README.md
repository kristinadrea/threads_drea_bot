# threads_drea_bot

A small Python service that listens to a Telegram channel and crossposts suitable posts to Threads.

It is designed for original text posts first. Audio, video, documents, voice messages, and generic files are skipped. Images can be enabled later through Cloudflare R2, because Threads needs public media URLs.

## What It Does

- Watches one Telegram source channel.
- Crossposts text posts to Threads.
- Splits long Telegram posts into a Threads chain.
- Skips audio, video, documents, voice messages, and unsupported media.
- Optionally posts image posts if `CROSSPOST_IMAGES=true` and R2 is configured.
- Optionally requires a marker tag, for example `#threads`, before crossposting.
- Has a placeholder for one weekly random Castaneda post from local quote/media files.

## Setup

1. Create a bot with BotFather and add it as admin to the Telegram source channel.
2. Copy `.env.example` to `.env` and fill in secrets.
3. Install dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

4. Run locally:

```bash
.venv/bin/python bot.py
```

## Required Environment Variables

- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `SOURCE_CHANNEL_ID`: Telegram channel id or username to listen to.
- `THREADS_USER_ID`: Threads user id from Meta.
- `THREADS_ACCESS_TOKEN`: Threads access token.

## Optional Safety Settings

- `REQUIRE_THREADS_TAG=true`: only crosspost Telegram posts containing `THREADS_TAG`.
- `THREADS_TAG=#threads`: marker tag removed before publishing to Threads.
- `CROSSPOST_IMAGES=false`: keep image crossposting off until media rights and R2 are ready.
- `MAX_THREAD_PARTS=5`: prevents huge Telegram posts from becoming giant Threads chains.
- `ADD_TELEGRAM_LINK_EVERY_N_POSTS=0`: set to a number like `7` to add the Telegram link occasionally.

## Notes

Threads text posts are short, so this bot uses `MAX_THREAD_CHARS=480` by default instead of using the full public limit. That gives room for punctuation and future suffixes.

Do not commit `.env`, `data/`, `secrets/`, or local state files.
