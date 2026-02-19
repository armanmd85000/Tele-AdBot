import os
import logging
import asyncio
from telethon import TelegramClient, events
from PyToday import config
from PyToday.queue_manager import queue_manager

logger = logging.getLogger("UserbotHandlers")

async def download_file(message):
    """Downloads a file from a message and returns its content as a list of strings."""
    try:
        path = await message.download_media()
        if not path:
            return []

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        os.remove(path) # Clean up
        return content.splitlines()
    except Exception as e:
        logger.error(f"Failed to download/read file: {e}")
        return []

def register_handlers(client: TelegramClient):
    """Registers all Userbot event handlers."""

    @client.on(events.NewMessage(pattern=r'^\.leech', outgoing=True))
    async def start_leech_handler(event):
        """
        Command: .leech
        Usage: Reply to a .txt file with .leech to start processing links.
        """
        reply = await event.get_reply_message()
        if not reply or not reply.file:
            await event.edit("❌ Please reply to a `.txt` file containing links.")
            return

        if not reply.file.name.endswith('.txt'):
            await event.edit("❌ The file must be a `.txt` file.")
            return

        await event.edit("📥 **Processing file...**")

        lines = await download_file(reply)
        if not lines:
            await event.edit("❌ Failed to read file or file is empty.")
            return

        # extract links
        links = []
        for line in lines:
            line = line.strip()
            # Simple validation: starts with http
            if line.startswith("http"):
                links.append(line)

        if not links:
            await event.edit("❌ No valid links found in the file.")
            return

        # Add to queue
        count = queue_manager.add_links(links)
        await event.edit(f"✅ **Added {count} links to the queue!**\nStarting processing...")

        # Start processing
        # We need the target chat ID.
        target_chat = config.TARGET_GROUP_ID
        if not target_chat:
            await event.respond("❌ TARGET_GROUP_ID is not configured in settings!")
            return

        # Convert to int if string
        try:
            target_chat_id = int(target_chat)
        except ValueError:
             await event.respond(f"❌ Invalid TARGET_GROUP_ID: {target_chat}")
             return

        # Resume if paused
        if queue_manager.paused:
            queue_manager.resume(client, target_chat_id)
        else:
            await queue_manager.process_queue(client, target_chat_id)


    @client.on(events.NewMessage(pattern=r'^\.addtasks', outgoing=True))
    async def add_tasks_handler(event):
        """
        Command: .addtasks link1 link2 ...
        Usage: Add links manually to the queue.
        """
        text = event.text.split(maxsplit=1)
        if len(text) < 2:
            await event.edit("❌ Usage: `.addtasks <link1> <link2>...`")
            return

        links = text[1].split()
        valid_links = [l for l in links if l.startswith("http")]

        if not valid_links:
            await event.edit("❌ No valid links found.")
            return

        count = queue_manager.add_links(valid_links)
        await event.edit(f"✅ **Added {count} links to the queue!**")

        # Trigger processing if running
        target_chat = config.TARGET_GROUP_ID
        if target_chat and not queue_manager.paused:
             try:
                 target_chat_id = int(target_chat)
                 await queue_manager.process_queue(client, target_chat_id)
             except ValueError:
                 pass


    @client.on(events.NewMessage(pattern=r'^\.progress', outgoing=True))
    async def progress_handler(event):
        """
        Command: .progress (formerly .status)
        Usage: Show detailed status of queue and slots.
        """
        status = queue_manager.get_status()
        await event.edit(status)

    @client.on(events.NewMessage(pattern=r'^\.status', outgoing=True))
    async def legacy_status_handler(event):
        """Alias for .progress"""
        status = queue_manager.get_status()
        await event.edit(status)


    @client.on(events.NewMessage(pattern=r'^\.stop$', outgoing=True))
    async def stop_handler(event):
        """
        Command: .stop
        Usage: Pause the queue processing.
        """
        queue_manager.pause()
        await event.edit("⏸️ **Queue Paused!**\nNo new links will be sent.")


    @client.on(events.NewMessage(pattern=r'^\.resume', outgoing=True))
    async def resume_handler(event):
        """
        Command: .resume (or .start)
        Usage: Resume the queue processing.
        """
        target_chat = config.TARGET_GROUP_ID
        if not target_chat:
            await event.edit("❌ TARGET_GROUP_ID not configured.")
            return

        try:
            target_chat_id = int(target_chat)
            queue_manager.resume(client, target_chat_id)
            await event.edit("▶️ **Queue Resumed!**\nProcessing started...")
        except ValueError:
             await event.edit(f"❌ Invalid TARGET_GROUP_ID: {target_chat}")


    @client.on(events.NewMessage(pattern=r'^\.stopall', outgoing=True))
    async def stop_all_handler(event):
        """
        Command: .stopall
        Usage: Clear queue and stop everything.
        """
        queue_manager.stop_all()
        await event.edit("🛑 **Stopped All Processing!**\nQueue cleared and slots reset.")

    logger.info("Userbot command handlers registered.")


def register_observer(client: TelegramClient):
    """Registers the observer that listens to Leech Bots."""

    target_chat = config.TARGET_GROUP_ID
    if not target_chat:
        logger.warning("TARGET_GROUP_ID not set. Observer will not run.")
        return

    try:
        target_chat_id = int(target_chat)
    except ValueError:
        logger.error(f"Invalid TARGET_GROUP_ID: {target_chat}")
        return

    leech_bots = config.LEECH_BOT_USERNAMES
    if not leech_bots:
        logger.warning("LEECH_BOT_USERNAMES not set. Observer will not run.")
        return

    # Normalize usernames (ensure they start with @ and lower case for comparison)
    normalized_bots = []
    for bot in leech_bots:
        bot = bot.strip()
        if not bot.startswith("@"):
            bot = "@" + bot
        normalized_bots.append(bot.lower())

    # Shared variable to store my_info
    my_info = {"data": None}

    logger.info(f"Observer started for group {target_chat_id} monitoring bots: {normalized_bots}")

    @client.on(events.NewMessage(chats=target_chat_id))
    async def leech_bot_observer(event):
        # Lazy load my_info
        if my_info["data"] is None:
             try:
                 my_info["data"] = await client.get_me()
             except Exception as e:
                 logger.error(f"Failed to fetch self info: {e}")
                 return

        me = my_info["data"]
        if not me:
            logger.warning("Could not get 'me' info, skipping observer check.")
            return

        sender = await event.get_sender()
        if not sender or not hasattr(sender, 'username') or not sender.username:
            # logger.info("Sender has no username, ignoring.")
            return

        sender_username = f"@{sender.username}".lower()

        # Check if sender is one of our leech bots
        try:
            bot_index = normalized_bots.index(sender_username)
        except ValueError:
            # logger.info(f"Sender {sender_username} is not a leech bot.")
            return # Not one of the monitored bots

        # Check content for success/failure triggers
        text = event.text or ""

        # --- Check for Status Messages ---
        if "Powered By Downloader Zone" in text or "Page:" in text:
             # This is a status message. Check if my task is listed.

             # Identify if my task is here
             is_my_task_present = False

             # Check username or first name
             if me.username:
                 target_str = f"@{me.username}".lower()
                 if target_str in text.lower():
                     is_my_task_present = True

             if not is_my_task_present and me.first_name:
                 if me.first_name in text:
                     is_my_task_present = True

             if is_my_task_present:
                 logger.info(f"Detected Status Message: My task is running on Bot {bot_index+1}!")
                 if queue_manager.slots[bot_index]:
                     queue_manager.slots[bot_index]['status'] = 'verified_processing'
                 return
             else:
                 # My task is NOT on this page. Check for buttons.
                 # Only if we *expect* to be running on this bot.
                 if queue_manager.slots[bot_index] and queue_manager.slots[bot_index].get('status') == 'processing':
                     # We are supposed to be running here.
                     # Try to click '>>' (Next Page) button if available.
                     try:
                         # We need to find the button with data to page change
                         # Usually buttons have callback_data. Telethon helps with click(text=...)
                         # Or we iterate rows.
                         # Based on user image, arrows are << and >>.
                         buttons = await event.get_buttons()
                         if buttons:
                             for row in buttons:
                                 for button in row:
                                     # Check for typical next page symbols
                                     if ">>" in button.text or "Next" in button.text or "›" in button.text:
                                         logger.info(f"Clicking NEXT Page on Bot {bot_index+1} to find task...")
                                         await button.click()
                                         # Don't click too fast
                                         await asyncio.sleep(2)
                                         return
                     except Exception as e:
                         logger.error(f"Failed to click button: {e}")

                 return


        # KEY CHECK: Ensure the task belongs to ME (for completion/failure)
        is_my_task = False

        if me.username and f"@{me.username}".lower() in text.lower():
            is_my_task = True

        if not is_my_task and me.first_name:
             if me.first_name in text:
                 is_my_task = True

        if not is_my_task:
            return

        # Failure triggers
        failure_keywords = [
            "Download Stopped",
            "Dead Torrent",
            "Task Size → 0B",
            "Due To → Timeout",
            "The response status is not successful"
        ]

        is_success = False
        is_failure = False

        # Check failure first (more specific)
        if any(k in text for k in failure_keywords):
            is_failure = True
        elif "Action Performed" in text and "File(s) have been sent" in text:
             # Strong success signal
             is_success = True
        elif "Task By" in text and "Total Files" in text:
             # Another success pattern from example
             is_success = True

        if is_success:
            logger.info(f"Detected SUCCESS from Bot {bot_index+1} ({sender_username}) for ME")
            await queue_manager.mark_completed(bot_index, True, client, target_chat_id)
        elif is_failure:
            logger.info(f"Detected FAILURE from Bot {bot_index+1} ({sender_username}) for ME")
            await queue_manager.mark_completed(bot_index, False, client, target_chat_id)

    logger.info("Userbot observer registered.")
