#  Copyright (c) 2025 AshokShau
#  Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
#  Part of the TgMusicBot project. All rights reserved where applicable.

import time
from datetime import datetime

from cachetools import TTLCache
from pytdbot import Client, types

from TgMusic import __version__, StartTime
from TgMusic.core import (
    chat_invite_cache,
    user_status_cache,
    chat_cache,
    config,
    call,
    Filter,
    SupportButton,
)

from TgMusic.core.admins import load_admin_cache
from TgMusic.core.buttons import add_me_markup, HelpMenu, BackHelpMenu
from TgMusic.modules.utils import sec_to_min
from TgMusic.modules.utils.play_helpers import (
    extract_argument,
)


@Client.on_message(filters=Filter.command(["start", "help"]))
async def start_cmd(c: Client, message: types.Message):
    """
    Welcome users and provide bot information with interactive help menu.
    """
    chat_id = message.chat_id
    bot_name = c.me.first_name
    mention = await message.mention()

    if chat_id < 0:  # Group chat
        welcome_text = (
            f"🎵 <b>Hello {mention}!</b>\n\n"
            f"<b>{bot_name}</b> is now active in this chat!\n"
            "──────────────────\n"
            "• High quality audio streaming\n"
            "• Multi-platform support (YouTube, Spotify, Jio Saavn , Apple Music & More)\n"
            "• Advanced playback controls\n\n"
            f"💬 <a href='{config.SUPPORT_GROUP}'>Support Chat</a> | "
        )

        reply = await message.reply_text(
            text=welcome_text,
            disable_web_page_preview=True,
            reply_markup=SupportButton,
        )
    else:  # Private chat
        welcome_text = (
            f"✨ <b>Welcome {mention}!</b>\n\n"
            f"<b>{bot_name}</b> - Premium Music Experience\n"
            f"<code>Version: v{__version__}</code>\n\n"
            "🎧 <b>Features:</b>\n"
            "• Lossless audio quality\n"
            "• Instant song searches\n"
            "• Multiple Platforms Playlist support\n"
            "• 24/7 playback with Zero Downtime\n\n"
            "🔍 <i>Tap the button below to explore all commands</i>"
        )

        bot_username = c.me.usernames.editable_username
        reply = await message.reply_text(
            text=welcome_text, reply_markup=add_me_markup(bot_username)
        )

    if isinstance(reply, types.Error):
        c.logger.warning(f"⚠️ Failed to send welcome message: {reply.message}")

    return None


@Client.on_message(filters=Filter.command("privacy"))
async def privacy_handler(c: Client, message: types.Message):
    """
    Handle the /privacy command to display privacy policy.
    """
    bot_name = c.me.first_name
    text = f"""
    <u><b>Privacy Policy for {bot_name}:</b></u>

<b>1. Data Storage:</b>
- {bot_name} does not store any personal data on the user's device.
- We do not collect or store any data about your device or personal browsing activity.

<b>2. What We Collect:</b>
- We only collect your Telegram <b>user ID</b> and <b>chat ID</b> to provide the music streaming and interaction functionalities of the bot.
- No personal data such as your name, phone number, or location is collected.

<b>3. Data Usage:</b>
- The collected data (Telegram UserID, ChatID) is used strictly to provide the music streaming and interaction functionalities of the bot.
- We do not use this data for any marketing or commercial purposes.

<b>4. Data Sharing:</b>
- We do not share any of your personal or chat data with any third parties, organizations, or individuals.
- No sensitive data is sold, rented, or traded to any outside entities.

<b>5. Data Security:</b>
- We take reasonable security measures to protect the data we collect. This includes standard practices like encryption and safe storage.
- However, we cannot guarantee the absolute security of your data, as no online service is 100% secure.

<b>6. Cookies and Tracking:</b>
- {bot_name} does not use cookies or similar tracking technologies to collect personal information or track your behavior.

<b>7. Third-Party Services:</b>
- {bot_name} does not integrate with any third-party services that collect or process your personal information, aside from Telegram's own infrastructure.

<b>8. Your Rights:</b>
- You have the right to request the deletion of your data. Since we only store your Telegram ID and chat ID temporarily to function properly, these can be removed upon request.
- You may also revoke access to the bot at any time by removing or blocking it from your chats.

<b>9. Changes to the Privacy Policy:</b>
- We may update this privacy policy from time to time. Any changes will be communicated through updates within the bot.

<b>10. Contact Us:</b>
If you have any questions or concerns about our privacy policy, feel free to contact us at <a href="https://t.me/BillaCore">Support Group</a>

──────────────────
<b>Note:</b> This privacy policy is in place to help you understand how your data is handled and to ensure that your experience with {bot_name} is safe and respectful.
    """

    reply = await message.reply_text(text)
    if isinstance(reply, types.Error):
        c.logger.warning(f"Error sending privacy policy message:{reply.message}")
    return


rate_limit_cache = TTLCache(maxsize=100, ttl=180)


@Client.on_message(filters=Filter.command(["reload"]))
async def reload_cmd(c: Client, message: types.Message) -> None:
    """Handle the /reload command to reload the bot."""
    user_id = message.from_id
    chat_id = message.chat_id
    if chat_id > 0:
        reply = await message.reply_text(
            "🚫 This command can only be used in SuperGroups only."
        )
        if isinstance(reply, types.Error):
            c.logger.warning(f"Error sending message: {reply} for chat {chat_id}")
        return None

    if user_id in rate_limit_cache:
        last_used_time = rate_limit_cache[user_id]
        time_remaining = 180 - (datetime.now() - last_used_time).total_seconds()
        reply = await message.reply_text(
            f"🚫 You can use this command again in ({sec_to_min(time_remaining)} Min)"
        )
        if isinstance(reply, types.Error):
            c.logger.warning(f"Error sending message: {reply} for chat {chat_id}")
        return None

    rate_limit_cache[user_id] = datetime.now()
    reply = await message.reply_text("🔄 Reloading...")
    if isinstance(reply, types.Error):
        c.logger.warning(f"Error sending message: {reply} for chat {chat_id}")
        return None

    ub = await call.get_client(chat_id)
    if isinstance(ub, types.Error):
        await reply.edit_text(ub.message)
        return None

    chat_invite_cache.pop(chat_id, None)
    user_key = f"{chat_id}:{ub.me.id}"
    user_status_cache.pop(user_key, None)

    if not chat_cache.is_active(chat_id):
        chat_cache.clear_chat(chat_id)

    load_admins, _ = await load_admin_cache(c, chat_id, True)
    ub_stats = await call.check_user_status(chat_id)
    if isinstance(ub_stats, types.Error):
        ub_stats = ub_stats.message

    loaded = "✅" if load_admins else "❌"
    text = (
        f"<b>Assistant Status:</b> {ub_stats.getType()}\n"
        f"<b>Admins Loaded:</b> {loaded}\n"
        f"<b>» Reloaded by:</b> {await message.mention()}"
    )

    reply = await reply.edit_text(text)
    if isinstance(reply, types.Error):
        c.logger.warning(f"Error sending message: {reply} for chat {chat_id}")
    return None


@Client.on_message(filters=Filter.command("ping"))
async def ping_cmd(client: Client, message: types.Message) -> None:
    """
    Handle the /ping command to check bot performance metrics.
    """
    start_time = time.monotonic()
    reply_msg = await message.reply_text("🏓 Pinging...")
    latency = (time.monotonic() - start_time) * 1000  # ms

    response = await call.stats_call(message.chat_id if message.chat_id < 0 else 1)
    if isinstance(response, types.Error):
        call_ping = response.message
        cpu_usage = "Unavailable"
    else:
        call_ping, cpu_usage = response
    call_ping_info = f"{call_ping:.2f} ms"
    cpu_info = f"{cpu_usage:.2f}%"
    uptime = datetime.now() - StartTime
    uptime_str = str(uptime).split(".")[0]

    response = (
        "📊 <b>System Performance Metrics</b>\n\n"
        f"⏱️ <b>Bot Latency:</b> <code>{latency:.2f} ms</code>\n"
        f"🕒 <b>Uptime:</b> <code>{uptime_str}</code>\n"
        f"🧠 <b>CPU Usage:</b> <code>{cpu_info}</code>\n"
        f"📞 <b>NTgCalls Ping:</b> <code>{call_ping_info}</code>\n"
    )
    done = await reply_msg.edit_text(response, disable_web_page_preview=True)
    if isinstance(done, types.Error):
        client.logger.warning(f"Error sending message: {done}")
    return None


@Client.on_message(filters=Filter.command("song"))
async def song_cmd(c: Client, message: types.Message):
    """Handle the /song command."""
    args = extract_argument(message.text)
    reply = await message.reply_text(
        f"🎶 USE: <code>@axMultiDLbot {args or 'song name'}</code>"
    )
    if isinstance(reply, types.Error):
        c.logger.warning(f"Error sending message: {reply}")

    return


@Client.on_updateNewCallbackQuery(filters=Filter.regex(r"help_\w+"))
async def callback_query_help(c: Client, message: types.UpdateNewCallbackQuery) -> None:
    """Handle help menu navigation with rich text formatting."""
    data = message.payload.data.decode()
    # Main help menu
    if data == "help_all":
        user = await c.getUser(message.sender_user_id)
        if isinstance(user, types.Error):
            c.logger.warning(f"⚠️ User fetch error: {user.message}")
            await message.answer(text="❌ Failed to load profile", show_alert=True)
            return None

        await message.answer(text="📚 Loading Help Menu...")
        welcome_text = (
            f"👋 <b>Hello {user.first_name}!</b>\n\n"
            f"🎵 Welcome to <b>{c.me.first_name}</b> Music Bot\n"
            f"<code>Version: v{__version__}</code>\n\n"
            "✨ A high-performance music bot with support for:\n"
            "• YouTube • Spotify • Apple Music • JioSaavn\n\n"
            "🔍 <i>Select a help category below:</i>"
        )
        await message.edit_message_text(text=welcome_text, reply_markup=HelpMenu)
        return None

    # Help category definitions
    help_categories = {
        "help_user": {
            "title": "🎧 User Commands",
            "content": (
                "<b>Basic Controls:</b>\n"
                "/play [song] - Play audio in VC\n"
                "/vplay [video] - Play video in VC\n"
                "/search - Find tracks to play\n"
                "/lyrics - Get song lyrics\n\n"
                "<b>Utilities:</b>\n"
                "/start - Bot introduction\n"
                "/privacy - View privacy policy\n"
                "/lang - Change language"
            ),
            "markup": BackHelpMenu,
        },
        "help_admin": {
            "title": "⚙️ Admin Commands",
            "content": (
                "<b>Playback Controls:</b>\n"
                "/skip - Skip current track\n"
                "/pause - Pause playback\n"
                "/resume - Continue playback\n"
                "/seek [sec] - Jump to position\n"
                "/volume [1-200] - Adjust volume\n\n"
                "<b>Queue Management:</b>\n"
                "/queue - Show playback queue\n"
                "/remove [x] - Remove track #x\n"
                "/clear - Empty the queue\n"
                "/loop [0-10] - Set repeat mode"
            ),
            "markup": BackHelpMenu,
        },
        "help_owner": {
            "title": "🔐 Owner Commands",
            "content": (
                "<b>Authorization:</b>\n"
                "/auth [reply] - Grant admin rights\n"
                "/unauth [reply] - Revoke admin\n"
                "/authlist - Show authorized users\n\n"
                "<b>Settings:</b>\n"
                "/buttons - Toggle control buttons\n"
                "/thumb - Toggle thumbnails\n"
                "/autoend - Auto-leave empty VCs\n"
                "/channelplay - Link to channel"
            ),
            "markup": BackHelpMenu,
        },
        "help_devs": {
            "title": "🛠 Developer Tools",
            "content": (
                "<b>System:</b>\n"
                "/stats - Usage statistics\n"
                "/logger - Toggle debug logs\n"
                "/broadcast - Send announcements\n\n"
                "<b>Maintenance:</b>\n"
                "/activevc - Active voice chats\n"
                "/clearallassistants - Reset assistants"
            ),
            "markup": BackHelpMenu,
        },
    }

    if category := help_categories.get(data):
        await message.answer(text=f"📖 {category['title']}")
        formatted_text = (
            f"<b>{category['title']}</b>\n\n"
            f"{category['content']}\n\n"
            "🔙 <i>Use buttons to navigate</i>"
        )
        await message.edit_message_text(
            text=formatted_text, reply_markup=category["markup"]
        )
        return None

    await message.answer(text="⚠️ Unknown command category")
    return None
