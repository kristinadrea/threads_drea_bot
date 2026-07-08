# threads_drea_bot

A small Python service that listens to a Telegram channel and crossposts suitable posts to Threads.

It is designed for original text and image posts. Audio, video, documents, voice messages, and generic files are skipped. Images are downloaded from Telegram, uploaded to Cloudflare R2, and then sent to Threads as public media URLs.

## What It Does

- Watches one or more Telegram source channels.
- Crossposts text posts to Threads.
- Crossposts Telegram photo posts to Threads when `CROSSPOST_IMAGES=true`.
- Uploads Telegram photos to Cloudflare R2 before publishing, because Threads requires a public image URL.
- Splits long Telegram posts into a Threads chain.
- Skips audio, video, documents, voice messages, and unsupported media.
- Optionally requires a marker tag, for example `#threads`, before crossposting.
- Has a placeholder for one weekly random Castaneda post from local quote/media files.

## Setup

1. Create a bot with BotFather and add it as admin to every Telegram source channel.
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
- `SOURCE_CHANNEL_IDS`: comma-separated Telegram channel ids or usernames to listen to.
- `THREADS_USER_ID`: Threads user id from Meta.
- `THREADS_ACCESS_TOKEN`: Threads access token.

## Optional Safety Settings

- `REQUIRE_THREADS_TAG=true`: only crosspost Telegram posts containing `THREADS_TAG`.
- `THREADS_TAG=#threads`: marker tag removed before publishing to Threads.
- `CROSSPOST_IMAGES=true`: download Telegram photos, upload them to R2, and publish them to Threads.
- `MAX_THREAD_PARTS=5`: prevents huge Telegram posts from becoming giant Threads chains.
- `ADD_TELEGRAM_LINK_EVERY_N_POSTS=0`: set to a number like `7` to add the Telegram link occasionally.



## Telegram Admin Commands

The command menu is scoped to `ADMIN_USER_ID`; global bot commands are cleared on startup.

- `/threads`: toggle Threads posting on/off.
- `/threads_parts`: ask for and save the max number of parts per Threads chain. You can also send `/threads_parts 8`.
- `/threads_status`: show current state, including max thread parts.

The settings are stored in `data/state.json`, so they survive restarts.

## Image Crossposting Through R2

Threads cannot receive a Telegram image file directly. It needs a public image URL. When `CROSSPOST_IMAGES=true`, the bot does this:

1. Downloads the photo from Telegram.
2. Uploads it to Cloudflare R2 under `R2_PREFIX`.
3. Sends the resulting public URL to Threads.

Required R2 variables:

```env
CROSSPOST_IMAGES=true
R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=drea
R2_PUBLIC_BASE_URL=https://pub-....r2.dev
R2_PREFIX=threads_drea_bot
R2_MEDIA_RETENTION_DAYS=3
```

The cleanup job runs once per hour while the bot is running. It only deletes objects under `R2_PREFIX`, so other bucket folders are left alone.

## Notes

Threads text posts are short, so this bot uses `MAX_THREAD_CHARS=480` by default instead of using the full public limit. That gives room for punctuation and future suffixes.

Do not commit `.env`, `data/`, `secrets/`, or local state files.
