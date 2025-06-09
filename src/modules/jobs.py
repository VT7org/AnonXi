#  Copyright (c) 2025 AshokShau
#  Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
#  Part of the TgMusicBot project. All rights reserved where applicable.

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
                self.bot.logger.warning(
                    f"An error occurred while getting vc users: {vc_users.message}"
                )
                return

            if len(vc_users) > 1:
                return
            played_time = await call.played_time(chat_id)
            if isinstance(played_time, types.Error):
                self.bot.logger.warning(
                    f"An error occurred while getting played time: {played_time.message}"
                )
                return

            if played_time < 15:
                return
            _chat_id = await db.get_chat_id_by_channel(chat_id) or chat_id
            reply = await self.bot.sendTextMessage(
                _chat_id, "⚠️ No active listeners detected. ⏹️ Leaving voice chat..."
            )
            if isinstance(reply, types.Error):
                self.bot.logger.warning(f"Error sending message: {reply}")
            await call.end(chat_id)

    async def end_inactive_calls(self):
        if self.bot is None or self.bot.me is None:
            return
        if not await db.get_auto_end(self.bot.me.id):
            return

        active_chats = chat_cache.get_active_chats()
        if not active_chats:
            self.bot.logger.debug("No active chats found.")
            return

        start_time = datetime.now()
        start_monotonic = time.monotonic()
        self.bot.logger.debug(
            f"🔄 Started end_inactive_calls at {start_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        try:
            self.bot.logger.debug(f"Checking {len(active_chats)} active chats...")
            tasks = [self._end_inactive_calls(chat_id) for chat_id in active_chats]
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            self.bot.logger.error(
                f"❗ Exception in end_inactive_calls: {e}", exc_info=True
            )
        finally:
            end_time = datetime.now()
            duration = time.monotonic() - start_monotonic
            self.bot.logger.debug(
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
                    self.bot.logger.debug(
                        f"[{client_name}] Skipping private chat: {chat.id}"
                    )
                    continue
                chats_to_leave.append(chat.id)
            self.bot.logger.debug(
                f"[{client_name}] Found {len(chats_to_leave)} chats to leave."
            )

            for chat_id in chats_to_leave:
                is_active = chat_cache.is_active(chat_id)
                if is_active:
                    continue
                try:
                    await ub.leave_chat(chat_id)
                    self.bot.logger.debug(f"[{client_name}] Left chat {chat_id}")
                    await asyncio.sleep(0.5)
                except errors.FloodWait as e:
                    wait_time = e.value
                    self.bot.logger.warning(
                        f"[{client_name}] FloodWait for {wait_time}s on chat {chat_id}"
                    )
                    if wait_time > 100:
                        self.bot.logger.warning(
                            f"[{client_name}] Skipping due to long wait time."
                        )
                        continue
                    await asyncio.sleep(wait_time)
                except errors.RPCError as e:
                    self.bot.logger.warning(
                        f"[{client_name}] Failed to leave chat {chat_id}: {e}"
                    )
                    continue
                except Exception as e:
                    self.bot.logger.error(
                        f"[{client_name}] Error leaving chat {chat_id}: {e}"
                    )
                    continue

            self.bot.logger.info(f"[{client_name}] Leaving all chats completed.")

    async def handle_participant_update(self, chat_id: int, participants: list[GroupCallParticipant]):
        """Handle video chat participant updates from pytgcalls."""
        async with _concurrency_limiter:
            if not self.is_valid_supergroup(chat_id):
                self.bot.logger.debug("Ignoring participant update for non-supergroup chat %s", chat_id)
                return

            self.bot.logger.debug("Video chat participants update in %s: %s participants", chat_id, len(participants))
            current_participants = {p.user_id for p in participants}
            previous_participants = video_chat_participants_cache.get(chat_id, set())
            new_participants = current_participants - previous_participants

            # Update cache with current participants
            video_chat_participants_cache[chat_id] = current_participants

            # Process new participants
            for user_id in new_participants:
                # Check cooldown to prevent duplicate messages (5-second cooldown)
                current_time = time.time()
                cooldown_key = f"{chat_id}:{user_id}"
                if current_time - join_message_cooldown.get(cooldown_key, 0) < 5:
                    self.bot.logger.debug("Skipping join message for %s in %s due to cooldown", user_id, chat_id)
                    continue
                join_message_cooldown[cooldown_key] = current_time

                # Fetch user information
                user_info = await self.bot.getUser(user_id)
                if isinstance(user_info, types.Error):
                    self.bot.logger.warning("Failed to get user info for %s: %s", user_id, user_info.message)
                    user_name = "Unknown"
                else:
                    user_name = user_info.first_name + (f" {user_info.last_name}" if user_info.last_name else "") or "Unknown"

                # Determine user role
                role = "User"
                member_status = await self.bot.getChatMember(chat_id, user_id)
                if isinstance(member_status, types.Error):
                    self.bot.logger.warning("Failed to get chat member status for %s in %s: %s", user_id, chat_id, member_status.message)
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

                # Prepare formatted message
                formatted_message = (
                    "#Jᴏɪɴᴇᴅ-VɪᴅᴇᴏCʜᴀᴛ\n"
                    f"Nᴀᴍᴇ : {user_name}\n"
                    f"ɪᴅ : {user_id}\n"
                    f"Aᴄᴛɪᴏɴ : {role}"
                )

                # Send message and schedule deletion
                sent_message = await self.bot.sendTextMessage(chat_id, formatted_message)
                if isinstance(sent_message, types.Error):
                    self.bot.logger.warning("Failed to send video chat join message in %s: %s", chat_id, sent_message.message)
                    continue

                # Schedule message deletion after 3 seconds
                async def delete_message():
                    await asyncio.sleep(3)
                    delete_result = await self.bot.deleteMessages(chat_id, [sent_message.id], revoke=True)
                    if isinstance(delete_result, types.Error):
                        self.bot.logger.warning("Failed to delete join message %s in %s: %s", sent_message.id, chat_id, delete_result.message)

                self.bot.loop.create_task(delete_message())

    def is_valid_supergroup(self, chat_id: int) -> bool:
        """
        Check if a chat ID is for a supergroup.
        """
        return str(chat_id).startswith("-100")

    def setup_pytgcalls_handlers(self, pytgcalls_client):
        """Set up pytgcalls event handlers for participant updates."""
        @pytgcalls_client.on_participant_change
        async def on_participant_change(pytgcalls, chat_id: int, participants: list[GroupCallParticipant]):
            await self.handle_participant_update(chat_id, participants)

        # Clear participant cache when call ends
        @pytgcalls_client.on_stream_end
        async def on_stream_end(pytgcalls, chat_id: int):
            video_chat_participants_cache.pop(chat_id, None)
            self.bot.logger.debug("Cleared participant cache for chat %s on stream end", chat_id)

    async def start_scheduler(self):
        self.scheduler.add_job(
            self.end_inactive_calls,
            CronTrigger(minute="*/1"),
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(self.leave_all, CronTrigger(hour=0, minute=0))
        self.scheduler.start()
        self.bot.logger.info("Scheduler started.")

    async def stop_scheduler(self):
        self.scheduler.shutdown(wait=True)
        await asyncio.sleep(1)
        self.bot.logger.info("Scheduler stopped.")
