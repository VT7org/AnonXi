# Copyright (c) 2025 AshokShau
# Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
# Part of the TgMusicBot project. All rights reserved where applicable.

from pytdbot import Client, types
from html import escape
import re

from TgMusic.core import Filter, control_buttons, chat_cache, db, call
from TgMusic.core.admins import is_admin, load_admin_cache
from .play import _get_platform_url, play_music
from .progress_handler import _handle_play_c_data
from .utils.play_helpers import edit_text
from ..core import DownloaderWrapper


def _sanitize_text(text: str) -> str:
    """Sanitize text to prevent Telegram entity parsing issues."""
    if not text:
        return ""
    # Escape HTML characters
    text = escape(text)
    # Remove control characters
    text = re.sub(r"[\x00-\x1F\x7F]", "", text)
    # Truncate to Telegram message length limit
    return text[:4096]


@Client.on_updateNewCallbackQuery(filters=Filter.regex(r"(c)?play_\w+"))
async def callback_query(c: Client, message: types.UpdateNewCallbackQuery) -> None:
    """Handle all playback control callback queries (skip, stop, pause, resume)."""
    data = message.payload.data.decode()
    user_id = message.sender_user_id

    # Retrieve message and user info with error handling
    get_msg = await message.getMessage()
    if isinstance(get_msg, types.Error):
        c.logger.warning(f"Failed to get message: {get_msg.message}")
        return None

    user = await c.getUser(user_id)
    if isinstance(user, types.Error):
        c.logger.warning(f"Failed to get user info: {user.message}")
        return None

    await load_admin_cache(c, message.chat_id)
    user_name = _sanitize_text(user.first_name)

    def requires_admin(action: str) -> bool:
        """Check if action requires admin privileges."""
        return action in {
            "play_skip",
            "play_stop",
            "play_pause",
            "play_resume",
            "play_close",
        }

    def requires_active_chat(action: str) -> bool:
        """Check if action requires an active playback session."""
        return action in {
            "play_skip",
            "play_stop",
            "play_pause",
            "play_resume",
            "play_timer",
        }

    async def send_response(
        msg: str, alert: bool = False, delete: bool = False, reply_markup=None
    ) -> None:
        """Helper function to send standardized responses."""
        msg = _sanitize_text(msg)
        if alert:
            await message.answer(msg, show_alert=True)
        else:
            edit_func = (
                message.edit_message_caption
                if get_msg.caption
                else message.edit_message_text
            )
            try:
                await edit_func(msg, reply_markup=reply_markup)
            except Exception as e:
                c.logger.error(f"Failed to edit message: {e}\nText: {msg}")
                await edit_func(f"Error: {msg}")  # Fallback to plain text

        if delete:
            _del_result = await c.deleteMessages(
                message.chat_id, [message.message_id], revoke=True
            )
            if isinstance(_del_result, types.Error):
                c.logger.warning(f"Message deletion failed: {_del_result.message}")

    # Check admin permissions if required
    if requires_admin(data) and not await is_admin(message.chat_id, user_id):
        await message.answer(
            "⛔ Administrator privileges required for this action.", show_alert=True
        )
        return None

    chat_id = message.chat_id
    if requires_active_chat(data) and not chat_cache.is_active(chat_id):
        return await send_response(
            "⏹️ No active playback session in this chat.", alert=True
        )

    # Handle different control actions
    if data == "play_skip":
        result = await call.play_next(chat_id)
        if isinstance(result, types.Error):
            return await send_response(
                f"⚠️ Playback error\nDetails: {_sanitize_text(result.message)}",
                alert=True,
            )
        return await send_response("⏭️ Track skipped successfully", delete=True)

    if data == "play_stop":
        result = await call.end(chat_id)
        if isinstance(result, types.Error):
            return await send_response(
                f"⚠️ Failed to stop playback\n{_sanitize_text(result.message)}", alert=True
            )
        return await send_response(
            f"<b>⏹ Playback Stopped</b>\n└ Requested by: {user_name}"
        )

    if data == "play_pause":
        result = await call.pause(chat_id)
        if isinstance(result, types.Error):
            return await send_response(
                f"⚠️ Pause failed\n{_sanitize_text(result.message)}",
                alert=True,
            )
        markup = (
            control_buttons("pause") if await db.get_buttons_status(chat_id) else None
        )
        return await send_response(
            f"<b>⏸ Playback Paused</b>\n└ Requested by: {user_name}",
            reply_markup=markup,
        )

    if data == "play_resume":
        result = await call.resume(chat_id)
        if isinstance(result, types.Error):
            return await send_response(f"⚠️ Resume failed\n{_sanitize_text(result.message)}", alert=True)
        markup = (
            control_buttons("resume") if await db.get_buttons_status(chat_id) else None
        )
        return await send_response(
            f"<b>▶ Playback Resumed</b>\n└ Requested by: {user_name}",
            reply_markup=markup,
        )

    if data == "play_close":
        delete_result = await c.deleteMessages(
            chat_id, [message.message_id], revoke=True
        )
        if isinstance(delete_result, types.Error):
            await message.answer(
                f"⚠️ Interface closure failed\n{_sanitize_text(delete_result.message)}", show_alert=True
            )
            return None
        await message.answer("✅ Interface closed successfully", show_alert=True)
        return None

    if data.startswith("play_c_"):
        return await _handle_play_c_data(data, message, chat_id, user_id, user_name, c)

    # Handle music playback requests
    try:
        _, platform, song_id = data.split("_", 2)
    except ValueError:
        c.logger.error(f"Malformed callback data received: {data}")
        return await send_response("⚠️ Invalid request format", alert=True)

    await message.answer(f"🔍 Preparing playback for {user_name}", show_alert=True)
    reply_text = f"🔍 Searching...\nRequested by: {user_name}"
    reply = await message.edit_message_text(reply_text)
    if isinstance(reply, types.Error):
        c.logger.warning(f"Message edit failed: {reply.message}\nText: {reply_text}")
        return None

    url = _get_platform_url(platform, song_id)
    if not url:
        c.logger.error(f"Unsupported platform: {platform} | Data: {data}")
        await edit_text(reply, text=f"⚠️ Unsupported platform: {platform}")
        return None

    song = await DownloaderWrapper(url).get_info()
    if song:
        if isinstance(song, types.Error):
            await edit_text(reply, text=f"⚠️ Retrieval error\n{_sanitize_text(song.message)}")
            return None

        return await play_music(c, reply, song, user_name)

    await edit_text(reply, text="⚠️ Requested content not found")
    return None
