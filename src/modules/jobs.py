# Copyright (c) 2025 AshokShau
# Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
# Part of the TgMusicBot project. All rights reserved where applicable.

import asyncio
import time
from datetime import datetime
from collections import defaultdict

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pyrogram import Client as PyroClient
from pyrogram import errors
from pytdbot import Client, types
from pytgcalls.types import GroupCallParticipant

from src import db
from src.config import AUTO_LEAVE
from src.helpers import call
from src.helpers import chat_cache
from src.logger import LOGGER

_concurrency_limiter = asyncio.Semaphore(10)
# Cache to track video chat participants per chat
video_chat_participants_cache = defaultdict(set)
# Cache to track recent join messages to prevent duplicates
join_message_cooldown = defaultdict(float)

class InactiveCallManager:
    def __init__(self, bot: Client):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(
            timezone="Asia/Kolkata", event_loop=self.bot.loop
        )

    async def _end_inactive_calls(self, chat_id: int):
        async with _concurrency_limiter:
            vc_users = await call.vc_users(chat_id)
            if isinstance(vc_users, types.Error):
                LOGGER.warning(f"An error occurred while getting vc users: {vc_users.message}")
                return

            if len(vc_users) > 1:
                return
            played_time = await call.played_time(chat_id)
            if isinstance(played_time, types.Error):
                LOGGER.warning(f"An error occurred while getting played time: {played_time.message}")
                return

            if played_time < 15:
                return
            _chat_id = await db.get_chat_id_by_channel(chat_id) or chat_id
            reply = await self.bot.sendTextMessage(
                _chat_id, "⚠️ No active listeners detected. ⏹️ Leaving voice chat..."
            )
            if isinstance(reply, types.Error):
                LOGGER.warning(f"Error sending message: {reply}")
            await call.end(chat_id)

    async def end_inactive_calls(self):
        if self.bot is None or self.bot.me is None:
            return
        if not await db.get_auto_end(self.bot.me.id):
            return

        active_chats = chat_cache.get_active_chats()
        if not active_chats:
            LOGGER.debug("No active chats found.")
            return

        start_time = datetime.now()
        start_monotonic = time.monotonic()
        LOGGER.debug(
            f"🔄 Started end_inactive_calls at {start_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        try:
            LOGGER.debug(f"Checking {len(active_chats)} active chats...")
            tasks = [self._end_inactive_calls(chat_id) for chat_id in active_chats]
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            LOGGER.error(f"❗ Exception in end_inactive_calls: {e}", exc_info=True)
        finally:
            end_time = datetime.now()
            duration = time.monotonic() - start_monotonic
            LOGGER.debug(
                f"✅ Finished end_inactive_calls at {end_time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(Duration: {duration:.2f}s)"
            )

    async def leave_all(self):
        if not AUTO_LEAVE:
            return

        for client_name, call_instance in call.calls.items():
            ub: PyroClient = call_instance.mtproto_client
            chats_to_leave = []
            async for dialog in ub.get_dialogs():
                chat = getattr(dialog, "chat", None)
                if not chat:
                    continue
                if chat.id > 0:
                    LOGGER.debug(f"[{client_name}] Skipping private chat: {chat.id}")
                    continue
                chats_to_leave.append(chat.id)
            LOGGER.debug(f"[{client_name}] Found {len(chats_to_leave)} chats to leave.")

            for chat_id in chats_to_leave:
                is_active = chat_cache.is_active(chat_id)
                if is_active:
                    continue
                try:
                    await ub.leave_chat(chat_id)
                    LOGGER.debug(f"[{client_name}] Left chat {chat_id}")
                    await asyncio.sleep(0.5)
                except errors.FloodWait as e:
                    wait_time = e.value
                    LOGGER.warning(
                        f"[{client_name}] FloodWait for {wait_time}s on chat {chat_id}"
                    )
                    if wait_time > 100:
                        LOGGER.warning(f"[{client_name}] Skipping due to long wait time.")
                        continue
                    await asyncio.sleep(wait_time)
                except errors.RPCError as e:
                    LOGGER.warning(f"[{client_name}] Failed to leave chat {chat_id}: {e}")
                    continue
                except Exception as e:
                    LOGGER.error(f"[{client_name}] Error leaving chat {chat_id}: {e}")
                    continue

            LOGGER.info(f"[{client_name}] Leaving all chats completed.")

    async def handle_participant_update(self, chat_id: int, participants: list[GroupCallParticipant]):
        """Handle video chat participant updates from pytgcalls."""
        async with _concurrency_limiter:
            if not self.is_valid_supergroup(chat_id):
                LOGGER.debug(f"Ignoring participant update for non-supergroup chat {chat_id}")
                return

            # Check bot's permissions
            bot_status = await self.bot.getChatMember(chat_id, self.bot.options["my_id"])
            if isinstance(bot_status, types.Error):
                LOGGER.warning(f"Failed to get bot status in {chat_id}: {bot_status.message}")
                return
            if bot_status.status["@type"] not in ["chatMemberStatusAdministrator", "chatMemberStatusCreator"]:
                LOGGER.warning(f"Bot lacks admin permissions in {chat_id}: {bot_status.status['@type']}")
                return

            LOGGER.debug(f"Video chat participants update in {chat_id}: {len(participants)} participants")
            current_participants = {p.user_id for p in participants if p.user_id}
            previous_participants = video_chat_participants_cache.get(chat_id, set())
            new_participants = current_participants - previous_participants
            LOGGER.debug(
                f"Chat {chat_id}: Current participants: {current_participants}, "
                f"Previous participants: {previous_participants}, "
                f"New participants: {new_participants}"
            )

            # Update cache with current participants
            video_chat_participants_cache[chat_id] = current_participants

            # Process new participants
            for user_id in new_participants:
                LOGGER.debug(f"Processing new participant {user_id} in chat {chat_id}")
                current_time = time.time()
                cooldown_key = f"{chat_id}:{user_id}"
                if current_time - join_message_cooldown.get(cooldown_key, 0) < 2:
                    LOGGER.debug(f"Skipping join message for {user_id} in {chat_id} due to cooldown")
                    continue
                join_message_cooldown[cooldown_key] = current_time

                # Fetch user information
                user_info = await self.bot.getUser(user_id)
                user_name = "Unknown"
                if isinstance(user_info, types.User):
                    user_name = user_info.first_name + (f" {user_info.last_name}" if user_info.last_name else "") or "Unknown"
                else:
                    LOGGER.warning(f"Failed to get user info for {user_id}: {user_info.message}")

                # Determine user role
                role = "User"
                member_status = await self.bot.getChatMember(chat_id, user_id)
                if isinstance(member_status, types.Error):
                    LOGGER.warning(f"Failed to get chat member status for {user_id} in {chat_id}: {member_status.message}")
                    role = "Ignored"
                else:
                    status_type = member_status.status["@type"]
                    if status_type == "chatMemberStatusCreator":
                        role = "Owner"
                    elif status_type == "chatMemberStatusAdministrator":
                        role = "Admin"
                    elif status_type == "chatMemberStatusMember":
                        role = "User"
                    elif user_id == self.bot.options["my_id"] or (hasattr(user_info, "type") and user_info.type["@type"] == "userTypeBot"):
                        role = "Bot"
                    else:
                        role = "Ignored"

                if role == "Ignored":
                    LOGGER.debug(f"Ignoring participant {user_id} with role {role}")
                    continue

                # Prepare formatted message
                formatted_message = (
                    "#Jᴏɪɴᴇᴅ-VɪᴅᴇᴏCʜᴀᴛ\n"
                    f"Nᴀᴍᴇ: {user_name}\n"
                    f"ɪᴅ: {user_id}\n"
                    f"Aᴄᴛɪᴏɴ: {role}"
                )

                # Send message and schedule deletion
                try:
                    sent_message = await self.bot.sendTextMessage(chat_id, formatted_message)
                    if isinstance(sent_message, types.Error):
                        LOGGER.warning(f"Failed to send video chat join message in {chat_id}: {sent_message.message}")
                        continue

                    async def delete_message():
                        await asyncio.sleep(5)  # Increased to 5 seconds for testing
                        delete_result = await self.bot.deleteMessages(chat_id, [sent_message.id], revoke=True)
                        if isinstance(delete_result, types.Error):
                            LOGGER.warning(f"Failed to delete join message {sent_message.id} in {chat_id}: {delete_result.message}")
                        else:
                            LOGGER.debug(f"Deleted join message {sent_message.id} in {chat_id}")

                    self.bot.loop.create_task(delete_message())
                except Exception as e:
                    LOGGER.error(f"Error sending/deleting join message for {user_id} in {chat_id}: {e}", exc_info=True)

    def is_valid_supergroup(self, chat_id: int) -> bool:
        """Check if a chat ID is for a supergroup."""
        is_supergroup = str(chat_id).startswith("-100")
        LOGGER.debug(f"Chat {chat_id} is_supergroup: {is_supergroup}")
        return is_supergroup

    def setup_pytgcalls_handlers(self, pytgcalls_client):
        """Set up pytgcalls event handlers for participant updates."""
        @pytgcalls_client.on_participant_change
        async def on_participant_change(pytgcalls, chat_id: int, participants: list[GroupCallParticipant]):
            LOGGER.debug(f"Participant change event triggered for chat {chat_id}")
            await self.handle_participant_update(chat_id, participants)

        @pytgcalls_client.on_stream_end
        async def on_stream_end(pytgcalls, chat_id: int):
            video_chat_participants_cache.pop(chat_id, None)
            LOGGER.debug(f"Cleared participant cache for chat {chat_id} on stream end")

    async def start_scheduler(self):
        self.scheduler.add_job(
            self.end_inactive_calls,
            CronTrigger(minute="*/1"),
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(self.leave_all, CronTrigger(hour=0, minute=0))
        self.scheduler.start()
        LOGGER.info("Scheduler started.")

    async def stop_scheduler(self):
        self.scheduler.shutdown(wait=True)
        await asyncio.sleep(1)
        LOGGER.info("Scheduler stopped.")
