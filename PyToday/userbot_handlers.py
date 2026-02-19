import os
import logging
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

        await queue_manager.process_queue(client, target_chat_id)


    @client.on(events.NewMessage(pattern=r'^\.status', outgoing=True))
    async def status_handler(event):
        """
        Command: .status
        Usage: Show current queue status.
        """
        status = queue_manager.get_status()
        await event.edit(status)


    @client.on(events.NewMessage(pattern=r'^\.clear', outgoing=True))
    async def clear_queue_handler(event):
        """
        Command: .clear
        Usage: Clear the queue.
        """
        queue_manager.queue.clear()
        await event.edit("🗑️ **Queue cleared!**")

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

    logger.info(f"Observer started for group {target_chat_id} monitoring bots: {normalized_bots}")

    @client.on(events.NewMessage(chats=target_chat_id))
    async def leech_bot_observer(event):
        sender = await event.get_sender()
        if not sender or not hasattr(sender, 'username') or not sender.username:
            return

        sender_username = f"@{sender.username}".lower()

        # Check if sender is one of our leech bots
        try:
            bot_index = normalized_bots.index(sender_username)
        except ValueError:
            return # Not one of the monitored bots

        # Check content for success/failure triggers
        text = event.text or ""

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
             # "Mercy.2026...mkv \n ... Out Mode -> #Leech ... Total Files -> 1 ... Task By ..."
             is_success = True

        if is_success:
            logger.info(f"Detected SUCCESS from Bot {bot_index+1} ({sender_username})")
            await queue_manager.mark_completed(bot_index, True, client, target_chat_id)
        elif is_failure:
            logger.info(f"Detected FAILURE from Bot {bot_index+1} ({sender_username})")
            await queue_manager.mark_completed(bot_index, False, client, target_chat_id)

    logger.info("Userbot observer registered.")
