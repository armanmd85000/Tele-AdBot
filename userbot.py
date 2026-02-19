import logging
import asyncio
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession
from PyToday import config
from PyToday.userbot_handlers import register_handlers, register_observer

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("Userbot")

# Telethon Logger
logging.getLogger("telethon").setLevel(logging.WARNING)

async def main():
    """
    Main function to start the Userbot.
    """
    # Check for required configuration
    if not config.API_ID or not config.API_HASH:
        logger.error("API_ID or API_HASH not found in environment variables.")
        print("ERROR: Please set API_ID and API_HASH in your environment variables or .env file.")
        print("You can get API_ID and API_HASH from https://my.telegram.org")
        return

    try:
        session = None
        if config.SESSION_STRING:
            logger.info("Using provided SESSION_STRING...")
            session = StringSession(config.SESSION_STRING)
        else:
            logger.info("No SESSION_STRING provided. Starting interactive login...")
            session = StringSession() # Start fresh

        client = TelegramClient(
            session,
            int(config.API_ID),
            config.API_HASH
        )

        await client.start()

        # If we generated a new session string (interactive login), print it!
        if not config.SESSION_STRING:
            new_string = client.session.save()
            print("\n" + "="*50)
            print("✅ LOGIN SUCCESSFUL! SAVE THIS STRING FOR HEROKU:")
            print("="*50)
            print(new_string)
            print("="*50 + "\n")

        me = await client.get_me()
        logger.info(f"✅ Userbot started as: {me.first_name} (@{me.username})")
        print(f"Userbot started as: {me.first_name} (@{me.username})")

        # Register handlers
        register_handlers(client)
        register_observer(client)

        # Keep the client running
        await client.run_until_disconnected()

    except Exception as e:
        logger.error(f"Failed to start Userbot: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Userbot stopped by user.")
