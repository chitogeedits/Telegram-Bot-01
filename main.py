# -*- coding: utf-8 -*-
import os
import re
import time
import sqlite3
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
)
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, CallbackContext,
    CallbackQueryHandler, Filters
)

# === CONFIG ===
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# === DB ===
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS file_tokens (
            token TEXT PRIMARY KEY,
            file_id TEXT,
            file_name TEXT
        )""")

def save_token(token, file_id, file_name):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO file_tokens VALUES (?, ?, ?)", (token, file_id, file_name))

def get_token(token):
    with sqlite3.connect(DB_PATH) as conn:
        res = conn.execute("SELECT file_id, file_name FROM file_tokens WHERE token = ?", (token,)).fetchone()
        return res if res else (None, None)

# === HELPERS ===
def extract_quality(name):
    name = name.lower()
    for q in ["480p","720p","1080p","hdrip","4k","2k"]:
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
    """
    Extract season and episode numbers from file name.
    Matches formats like:
    - S1, S01, Season 1
    - EP1, Ep 01, Episode 01
    """
    name = name.lower()

    # Extract season
    season_match = re.search(r'\b(?:s|season)[\s:_-]*(\d{1,2})\b', name)
    season = season_match.group(1).zfill(2) if season_match else "01"

    # Extract episode
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
        except:
            not_joined.append(ch)
    return not_joined

def media_handler(update: Update, context: CallbackContext):
    msg = update.message
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
    args = context.args
    if not args:
        try:
            buttons = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Main Channel", url="https://t.me/chitogeedits2/1"),
                    InlineKeyboardButton("Second Channel", url="https://t.me/blabla658/1")
                ]
            ])
            context.bot.send_photo(
                chat_id=user.id,
                photo=WELCOME_IMAGE_URL,
                caption="👋 Welcome!\n\nThis bot does not support the direct messages\n\n⬡ Powered by @chitogeedits2",
                reply_markup=buttons
            )
        except Exception as e:
            logging.error(f"Welcome image error: {e}")
        return

    token = args[0]
    file_id, file_name = get_token(token)
    if not file_id:
        update.message.reply_text("❌ File not available.")
        return

    not_joined = get_unsubscribed_channels(context.bot, user.id)
    if not_joined:
        buttons = [[InlineKeyboardButton(f"Join The Channel")]]
        buttons.append([InlineKeyboardButton("Try Again", callback_data=f"retry:{token}")])
        context.bot.send_message(
            chat_id=user.id,
            text="🔒 Please join the required channels to unlock the file:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    try:
        sent = context.bot.send_document(
            chat_id=user.id,
            document=file_id,
            caption=file_name,
            parse_mode=ParseMode.HTML
        )
        context.bot.send_message(chat_id=user.id, text="⏳ Auto-deleting this file in 10 minutes.")
        context.job_queue.run_once(delete_sent_file, 600, context={"chat_id": user.id, "message_id": sent.message_id})
    except Exception as e:
        update.message.reply_text("❌ Could not send file.")
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
        buttons = [[InlineKeyboardButton(f"Join The Channel")]]
        buttons.append([InlineKeyboardButton("Try Again", callback_data=f"retry:{token}")])
        query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
        query.answer("❗ Still not joined required channels.")
        return

    try:
        sent = context.bot.send_document(
            chat_id=user.id,
            document=file_id,
            caption=file_name,
            parse_mode=ParseMode.HTML
        )
        context.bot.send_message(chat_id=user.id, text="⏳ Auto-deleting this file in 10 minutes.")
        context.job_queue.run_once(delete_sent_file, 600, context={"chat_id": user.id, "message_id": sent.message_id})
        context.bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)
        query.answer("✅ File sent to your DM.")
    except Exception as e:
        logging.error(f"Retry send failed: {e}")
        query.answer("❌ Could not send file.")

# === REPOST ===
def repost_handler(update: Update, context: CallbackContext):
    msg = update.channel_post
    if msg.chat.id != SOURCE_CHANNEL_ID:
        return
    post_link = f"https://t.me/c/{str(SOURCE_CHANNEL_ID)[4:]}/{msg.message_id}"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Download", url=post_link)]])
    try:
        if msg.photo:
            context.bot.send_photo(chat_id=TARGET_CHANNEL_ID, photo=msg.photo[-1].file_id, caption=msg.caption or "", reply_markup=keyboard, parse_mode=ParseMode.HTML)
        elif msg.video:
            context.bot.send_video(chat_id=TARGET_CHANNEL_ID, video=msg.video.file_id, caption=msg.caption or "", reply_markup=keyboard, parse_mode=ParseMode.HTML)
        elif msg.document:
            context.bot.send_document(chat_id=TARGET_CHANNEL_ID, document=msg.document.file_id, caption=msg.caption or "", reply_markup=keyboard, parse_mode=ParseMode.HTML)
        elif msg.text:
            context.bot.send_message(chat_id=TARGET_CHANNEL_ID, text=msg.text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"Repost failed: {e}")

# === RUN ===
def run_bots():
    file_updater = Updater(FILE_BOT_TOKEN, use_context=True)
    repost_updater = Updater(REPOST_BOT_TOKEN, use_context=True)

    file_dp = file_updater.dispatcher
    file_dp.add_handler(CommandHandler("start", start))
    file_dp.add_handler(CommandHandler("postfile", postfile))
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
