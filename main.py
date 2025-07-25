# -*- coding: utf-8 -*-
import os
import re
import time
import sqlite3
import logging
import platform
import psutil
from telegram.error import BadRequest
from dotenv import load_dotenv

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
)
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, CallbackContext,
    CallbackQueryHandler, Filters
)

# === LOAD CONFIG ===
load_dotenv()
FILE_BOT_TOKEN = os.getenv("FILE_BOT_TOKEN")
REPOST_BOT_TOKEN = os.getenv("REPOST_BOT_TOKEN")
SOURCE_CHANNEL_ID = int(os.getenv("SOURCE_CHANNEL_ID"))
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
ALLOWED_USERNAME = os.getenv("ALLOWED_USERNAME")
REQUIRED_CHANNELS = os.getenv("REQUIRED_CHANNELS", "").split(",")
COVER_IMAGE_URL = os.getenv("COVER_IMAGE_URL")
WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL")

DB_PATH = "file_tokens.db"
media_group_cache = {}
start_time = time.time()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# === DATABASE ===
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS file_tokens (
            token TEXT PRIMARY KEY,
            file_id TEXT,
            file_name TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )""")


def save_token(token, file_id, file_name):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO file_tokens VALUES (?, ?, ?)", (token, file_id, file_name))


def get_token(token):
    with sqlite3.connect(DB_PATH) as conn:
        res = conn.execute("SELECT file_id, file_name FROM file_tokens WHERE token = ?", (token,)).fetchone()
        return res if res else (None, None)


def count_tokens():
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("SELECT COUNT(*) FROM file_tokens").fetchone()[0]


def save_user(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))


def get_user_count():
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


# === HELPERS ===
def extract_quality(name):
    name = name.lower()
    for q in ["480p", "720p", "1080p", "hdrip", "4k", "2k"]:
        if q in name:
            return q.upper()
    return "UNKNOWN"


def extract_audio(name):
    name = name.lower()
    if "dub" in name and "sub" in name:
        return "English [Dub+Sub]"
    elif "dub" in name:
        return "English [Dub+Sub]"
    elif "sub" in name:
        return "Japanese [Sub]"
    return "N/A"


def extract_season_episode(name):
    name = name.lower()
    season_match = re.search(r'\b(?:s|season)[\s:_-]*(\d{1,2})\b', name)
    season = season_match.group(1).zfill(2) if season_match else "01"
    episode_match = re.search(r'\b(?:ep|episode)[\s:_-]*(\d{1,3})\b', name)
    episode = episode_match.group(1).zfill(2) if episode_match else "01"
    return season, episode


def get_unsubscribed_channels(bot, user_id):
    not_joined = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = bot.get_chat_member(f"@{ch}", user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                not_joined.append(ch)
        except Exception as e:
            logging.warning(f"Check failed for @{ch}: {e}")
            not_joined.append(ch)
    return not_joined


def media_handler(update: Update, context: CallbackContext):
    msg = update.message
    if not msg:
        return
    if msg.media_group_id:
        media_group_cache.setdefault(msg.media_group_id, []).append((msg, time.time()))
        for gid in list(media_group_cache):
            media_group_cache[gid] = [(m, t) for m, t in media_group_cache[gid] if time.time() - t < 60]
            if not media_group_cache[gid]:
                del media_group_cache[gid]


def delete_sent_file(context: CallbackContext):
    data = context.job.context
    try:
        context.bot.delete_message(chat_id=data["chat_id"], message_id=data["message_id"])
    except Exception as e:
        logging.warning(f"Auto-delete failed: {e}")


# === STATUS ===
def status(update: Update, context: CallbackContext):
    user = update.effective_user
    if user.username != ALLOWED_USERNAME:
        return
    uptime = int(time.time() - start_time)
    h, r = divmod(uptime, 3600)
    m, s = divmod(r, 60)
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    text = (
        "<b>📊 Bot Status Report</b>\n"
        "<blockquote>"
        f" Uptime   : {h}h {m}m {s}s\n"
        f" Files    : {count_tokens()}\n"
        f" Users    : {get_user_count()}\n"
        f" CPU      : {cpu}%\n"
        f" RAM      : {ram.percent}% "
        f"({round(ram.used / 1024**2)}MB / {round(ram.total / 1024**2)}MB)"
        "</blockquote>"
    )
    update.message.reply_text(text, parse_mode=ParseMode.HTML)


# === POSTFILE ===
def postfile(update: Update, context: CallbackContext):
    msg = update.message
    if msg.from_user.username != ALLOWED_USERNAME:
        msg.reply_text("⛔ You're not allowed.")
        return
    if not msg.reply_to_message:
        msg.reply_text("❗ Reply to a media file.")
        return

    replied = msg.reply_to_message
    media_files = []
    if replied.media_group_id and replied.media_group_id in media_group_cache:
        time.sleep(1)
        media_files = [m for m, _ in media_group_cache[replied.media_group_id]]
    else:
        media_files = [replied]

    quality_map = {}
    audio_text = "N/A"
    first_token = None
    season, episode = "01", "01"

    for m in media_files:
        if not m.document:
            continue
        file_name = m.document.file_name or "file"
        quality = extract_quality(file_name)
        audio_text = extract_audio(file_name)
        season, episode = extract_season_episode(file_name)
        token = f"file_{quality}_{m.message_id}"
        file_id = m.document.file_id
        save_token(token, file_id, file_name)
        quality_map[quality] = token
        if not first_token:
            first_token = token

    if not quality_map:
        msg.reply_text("❌ No valid document found.")
        return

    _, file_name = get_token(first_token)
    title = os.path.splitext(file_name)[0] if file_name else "Untitled"
    bot_username = context.bot.username
    buttons = []
    row = []
    for q in sorted(quality_map):
        link = f"https://t.me/{bot_username}?start={quality_map[q]}"
        row.append(InlineKeyboardButton(q, url=link))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    quality_text = "Multi" if len(quality_map) > 1 else list(quality_map.keys())[0]
    caption = (
        f"⬡ {title}\n"
        f"╭━━━━━━━━━━━━━━━━━━━━━\n"
        f"‣ Season : {season}\n"
        f"‣ Episode : {episode}\n"
        f"‣ Quality : {quality_text}\n"
        f"‣ Audio   : {audio_text}\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━\n"
        f"⬡ Powered By : @{REQUIRED_CHANNELS[1]}"
    )

    context.bot.send_photo(
        chat_id=CHANNEL_ID,
        photo=COVER_IMAGE_URL,
        caption=caption,
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    msg.reply_text("✅ Posted.")


# === START ===
def start(update: Update, context: CallbackContext):
    user = update.effective_user
    save_user(user.id)
    args = context.args
    if not args:
        try:
            buttons = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Main Channel", url="https://t.me/chitogeedits2"),
                    InlineKeyboardButton("Backup Channel", url="https://t.me/blabla658")
                ]
            ])
            context.bot.send_photo(
                chat_id=user.id,
                photo=WELCOME_IMAGE_URL,
                caption="Welcome!\n\nThis bot doesn't support browsing directly.\n\nPowered by @chitogeedits2",
                reply_markup=buttons
            )
        except Exception as e:
            logging.error(f"Welcome error: {e}")
        return

    token = args[0]
    file_id, file_name = get_token(token)
    if not file_id:
        update.message.reply_text("File not available.")
        return

    not_joined = get_unsubscribed_channels(context.bot, user.id)
    if not_joined:
        # Create buttons with 2 per row
        buttons = []
        row = []
        for ch in not_joined:
            row.append(InlineKeyboardButton("Join Channel", url=f"https://t.me/{ch}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        # Add final Try Again button
        buttons.append([InlineKeyboardButton("Try Again", callback_data=f"retry:{token}")])

        try:
            context.bot.send_photo(
    chat_id=user.id,
    photo="https://i.ibb.co/KcRWdnbf/Daco-212971.jpg",
    caption="H-Hey! Join the required channels before touching the file, okay!? Hmph!",
    reply_markup=InlineKeyboardMarkup(buttons),
    parse_mode=ParseMode.HTML
)

        except Exception as e:
            logging.error(f"Force join GIF send failed: {e}")
        return

    try:
        sent = context.bot.send_document(
            chat_id=user.id,
            document=file_id,
            caption=file_name,
            parse_mode=ParseMode.HTML
        )
        context.bot.send_message(
            chat_id=user.id,
            text="<blockquote>This file will delete automatically in 10 minutes. Forward it to your Saved Messages.</blockquote>",
            parse_mode=ParseMode.HTML
        )
        context.job_queue.run_once(delete_sent_file, 600, context={"chat_id": user.id, "message_id": sent.message_id})
    except Exception as e:
        update.message.reply_text("Failed to send file.")
        logging.error(f"Start error: {e}")


# === RETRY ===
def retry_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    user = query.from_user
    query.answer()
    token_match = re.match(r"retry:(file_\w+_\d+)", query.data)
    if not token_match:
        query.answer("❌ Invalid token.", show_alert=True)
        return

    token = token_match.group(1)
    file_id, file_name = get_token(token)
    if not file_id:
        query.answer("❌ File not available.", show_alert=True)
        return

    not_joined = get_unsubscribed_channels(context.bot, user.id)
    if not_joined:
        buttons = []
        row = []
        for ch in not_joined:
            row.append(InlineKeyboardButton("Join Channel", url=f"https://t.me/{ch}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("Try Again", callback_data=f"retry:{token}")])

        try:
            query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass  # Ignore this specific error
            else:
                logging.error(f"Retry markup update failed: {e}")
        query.answer("❗ Still missing required channels.")
        return

    try:
        sent = context.bot.send_document(
            chat_id=user.id,
            document=file_id,
            caption=file_name,
            parse_mode=ParseMode.HTML
        )
        context.bot.send_message(
            chat_id=user.id,
            text="<blockquote>This File is deleting automatically in 10 minutes. Forward in your Saved Messages..!.</blockquote>",
            parse_mode=ParseMode.HTML
        )
        context.job_queue.run_once(delete_sent_file, 600, context={"chat_id": user.id, "message_id": sent.message_id})
        context.bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)
        query.answer("✅ File sent to your DM.")
    except Exception as e:
        logging.error(f"Retry send failed: {e}")
        query.answer("❌ Could not send file.")


# === REPOST HANDLER ===
def repost_handler(update: Update, context: CallbackContext):
    msg = update.channel_post
    if not msg or msg.chat.id != SOURCE_CHANNEL_ID:
        return

    try:
        file_id = None
        file_name = None
        quality = "UNKNOWN"
        token = None

        if msg.document:
            file_id = msg.document.file_id
            file_name = msg.document.file_name or "Untitled"
            quality = extract_quality(file_name)
        elif msg.video:
            file_id = msg.video.file_id
            file_name = msg.caption or "Untitled"
            quality = "HD"
        elif msg.photo:
            file_id = msg.photo[-1].file_id
            file_name = msg.caption or "Untitled"
            quality = "HD"
        else:
            return

        token = f"file_{quality}_{msg.message_id}"
        save_token(token, file_id, file_name)

        # ✅ Get the public username of the source channel
        source_channel_username = msg.chat.username  # this is None if the channel is private

        if not source_channel_username:
            logging.error("Source channel does not have a public username.")
            return

        # ✅ Create public post link
        post_link = f"https://t.me/{source_channel_username}/{msg.message_id}"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Download", url=post_link)]
        ])

        context.bot.send_photo(
            chat_id=TARGET_CHANNEL_ID,
            photo=COVER_IMAGE_URL,
            caption=f"{file_name}\n<blockquote>Contact @{ALLOWED_USERNAME} for any issues.</blockquote>",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        logging.error(f"Repost failed: {e}")

# === RUN BOT ===
def run_bots():
    file_updater = Updater(FILE_BOT_TOKEN, use_context=True)
    repost_updater = Updater(REPOST_BOT_TOKEN, use_context=True)

    file_dp = file_updater.dispatcher
    file_dp.add_handler(CommandHandler("start", start))
    file_dp.add_handler(CommandHandler("postfile", postfile))
    file_dp.add_handler(CommandHandler("status", status))
    file_dp.add_handler(MessageHandler(Filters.document | Filters.video, media_handler))
    file_dp.add_handler(CallbackQueryHandler(retry_callback, pattern="^retry(:.+)?$"))

    repost_dp = repost_updater.dispatcher
    repost_dp.add_handler(MessageHandler(Filters.update.channel_posts, repost_handler))

    file_updater.job_queue.start()
    logging.info("📁 File Bot running...")
    logging.info("🔁 Repost Bot running...")
    file_updater.start_polling()
    repost_updater.start_polling()
    file_updater.idle()
    repost_updater.idle()


if __name__ == "__main__":
    init_db()
    run_bots()
