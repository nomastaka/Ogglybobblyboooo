import os
import time
import json
import logging
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

API_BASE = "https://costum-boomber-api.vercel.app/api"
API_HEALTH = "https://costum-boomber-api.vercel.app/api/health"

# --- LOGGING SETUP ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- IN-MEMORY DATABASE (For Render, use a persistent DB in production) ---
# Format: { user_id: {"accepted": bool, "verified": bool} }
USER_DB = {}

# --- CONVERSATION STATES ---
ASK_NUMBER, ASK_MESSAGE, ASK_COUNT = range(3)

# --- HELPER FUNCTIONS ---
async def send_log(context: ContextTypes.DEFAULT_TYPE, text: str):
    """Sends a message to the Admin Logs Channel."""
    try:
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send log: {e}")

async def check_channel_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Checks if the user has joined the required channel."""
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status in ['left', 'kicked', 'restricted']:
            return False
        return True
    except Exception as e:
        logger.error(f"Channel check error: {e}")
        return False

def get_disclaimer_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ I Accept & Agree", callback_data="accept_disclaimer")],
        [InlineKeyboardButton("❌ I Decline", callback_data="decline_disclaimer")]
    ])

def get_join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}")],
        [InlineKeyboardButton("✅ I Have Joined", callback_data="check_join")]
    ])

# --- ONBOARDING HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    user_id = user.id
    
    if user_id not in USER_DB:
        USER_DB[user_id] = {"accepted": False, "verified": False}
        
    # Log new user
    await send_log(context, f"👤 <b>NEW USER INTERACTION</b>\nID: <code>{user_id}</code>\nUsername: @{user.username or 'None'}")
    
    # 1. Check Channel
    is_member = await check_channel_membership(update, context)
    if not is_member:
        USER_DB[user_id]["verified"] = False
        await update.message.reply_text(
            "🔒 <b>Access Denied</b>\n\nYou must join our official channel to use this bot.",
            parse_mode="HTML",
            reply_markup=get_join_keyboard()
        )
        return ConversationHandler.END
        
    USER_DB[user_id]["verified"] = True
    
    # 2. Check Disclaimer
    if not USER_DB[user_id]["accepted"]:
        await update.message.reply_text(
            "⚠️ <b>IMPORTANT DISCLAIMER</b>\n\n"
            "This bot is for <b>educational and testing purposes only</b>. "
            "You agree not to use this tool to harass, threaten, or spam individuals. "
            "You confirm you have consent to test the target number. "
            "All requests are logged.\n\n"
            "By clicking 'I Accept', you agree to these terms.",
            parse_mode="HTML",
            reply_markup=get_disclaimer_keyboard()
        )
        return ConversationHandler.END
        
    # 3. User is fully verified
    await update.message.reply_text(
        "✅ <b>Welcome back!</b>\nUse /send to start a new request, or /health to check API status.",
        parse_mode="HTML"
    )
    return ConversationHandler.END

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == "check_join":
        is_member = await check_channel_membership(update, context)
        if is_member:
            USER_DB[user_id]["verified"] = True
            if USER_DB[user_id]["accepted"]:
                await query.edit_message_text("✅ Channel verified! You can now use the bot. Use /send to begin.")
            else:
                await query.edit_message_text(
                    "⚠️ Please accept the disclaimer to continue.",
                    reply_markup=get_disclaimer_keyboard()
                )
        else:
            await query.edit_message_text("❌ You have not joined the channel yet.", reply_markup=get_join_keyboard())
            
    elif query.data == "accept_disclaimer":
        USER_DB[user_id]["accepted"] = True
        await query.edit_message_text("✅ Disclaimer accepted! You can now use the bot. Use /send to begin.")
        await send_log(context, f"✅ <b>DISCLAIMER ACCEPTED</b>\nID: <code>{user_id}</code>")
        
    elif query.data == "decline_disclaimer":
        await query.edit_message_text("❌ You declined the disclaimer. You cannot use this bot.")
        await send_log(context, f"🚫 <b>DISCLAIMER DECLINED</b>\nID: <code>{user_id}</code>")

# --- MAIN BOT FLOW ---
async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    
    # Guard check
    if user_id not in USER_DB or not USER_DB[user_id]["accepted"] or not USER_DB[user_id]["verified"]:
        await update.message.reply_text("❌ You must /start and verify first.")
        return ConversationHandler.END

    await update.message.reply_text("📱 Please enter the 10-digit target phone number:")
    return ASK_NUMBER

async def receive_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    number = update.message.text.strip()
    if not number.isdigit() or len(number) != 10:
        await update.message.reply_text("❌ Invalid number. Please enter exactly 10 digits:")
        return ASK_NUMBER
    
    context.user_data['target_number'] = number
    await update.message.reply_text("💬 Enter the message you want to send:")
    return ASK_MESSAGE

async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message.text
    if not msg:
        await update.message.reply_text("❌ Message cannot be empty. Try again:")
        return ASK_MESSAGE
    
    context.user_data['target_msg'] = msg
    await update.message.reply_text("🔢 How many times? (Min: 1, Max: 10):")
    return ASK_COUNT

async def receive_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        count = int(update.message.text.strip())
        if count < 1 or count > 10:
            await update.message.reply_text("❌ Count must be between 1 and 10. Try again:")
            return ASK_COUNT
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number between 1 and 10.")
        return ASK_COUNT

    number = context.user_data.get('target_number')
    msg = context.user_data.get('target_msg')
    user_id = update.effective_user.id

    await update.message.reply_text("⏳ Sending request...")

    # Log to Admin Channel BEFORE request
    await send_log(context, f"🚀 <b>NEW ATTACK INITIATED</b>\nUser ID: <code>{user_id}</code>\nTarget: <code>{number}</code>\nMessage: {msg}\nCount: {count}")

    # Fire API
    url = f"{API_BASE}?count={count}&msg={msg}&number={number}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url)
            data = resp.json()
            
            if resp.status_code == 200 and data.get("ok"):
                reply = (
                    f"✅ <b>Success!</b>\n"
                    f"Sent: {data.get('sent')}\n"
                    f"Failed: {data.get('failed')}\n"
                    f"Time: {data.get('elapsedSec')}s\n"
                    f"Devices Used: {data.get('devicesAvailable')}"
                )
                await update.message.reply_text(reply, parse_mode="HTML")
                await send_log(context, f"✅ <b>SUCCESS</b>\nSent: {data.get('sent')} | Failed: {data.get('failed')}")
            else:
                await update.message.reply_text("❌ API returned an error. Please try again later.")
                await send_log(context, f"❌ <b>API ERROR</b>\nStatus: {resp.status_code}\nBody: {resp.text[:100]}")
        except Exception as e:
            await update.message.reply_text("❌ Network Error. Please try again.")
            await send_log(context, f"❌ <b>NETWORK ERROR</b>\n{str(e)[:100]}")
            
    return ConversationHandler.END

# --- HEALTH & ADMIN COMMANDS ---
async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📡 Pinging API...")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(API_HEALTH, timeout=10.0)
            data = resp.json()
            if data.get("ok"):
                config = data.get("config", {})
                text = (
                    f"**📡 SYSTEM STATUS**\n"
                    f"Status: 🟢 OPERATIONAL\n"
                    f"Cache Age: {data.get('cacheAgeSec')}s\n"
                    f"Cached Devices: {data.get('cachedDevices')}\n"
                    f"DB Nodes: {data.get('dbs')}\n"
                    f"Config: Retry:{config.get('default_retry')} | Shotgun:{config.get('default_shotgun')} | Workers:{config.get('max_workers')}"
                )
                await update.message.reply_text(text, parse_mode="Markdown")
            else:
                await update.message.reply_text("🔴 API is down or returned invalid data.")
        except Exception:
            await update.message.reply_text("🔴 Failed to reach API.")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
        
    message = " ".join(context.args)
    success = 0
    for uid in USER_DB.keys():
        try:
            await context.bot.send_message(chat_id=uid, text=message)
            success += 1
        except:
            pass
    await update.message.reply_text(f"✅ Broadcast sent to {success} users.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

# --- MAIN APPLICATION ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Main conversation flow
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("send", send_command)],
        states={
            ASK_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_number)],
            ASK_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_message)],
            ASK_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_count)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(conv_handler)

    logger.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
