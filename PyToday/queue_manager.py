import asyncio
import logging
from collections import deque
from typing import List, Dict, Optional, Any
from PyToday import config

logger = logging.getLogger("QueueManager")

class QueueManager:
    def __init__(self):
        self.queue = deque()
        # Slots: 0-4 (corresponding to bots 1-5)
        # Value is None if free, or a dict { 'link': str, 'retries': int } if busy
        self.slots: Dict[int, Optional[Dict[str, Any]]] = {i: None for i in range(5)}
        self.max_retries = 3

    async def _log_to_channel(self, client, text: str):
        if not config.LOGS_CHANNEL_ID:
            return
        try:
            channel_id = int(config.LOGS_CHANNEL_ID)
            await client.send_message(channel_id, text)
        except Exception as e:
            logger.error(f"Failed to send log message: {e}")

    def add_links(self, links: List[str]):
        """Adds a list of links to the queue."""
        count = 0
        for link in links:
            if link.strip():
                self.queue.append({'link': link.strip(), 'retries': 0})
                count += 1
        logger.info(f"Added {count} links to queue. Total pending: {len(self.queue)}")
        return count

    def get_status(self) -> str:
        """Returns a string describing the current status."""
        status = f"**📊 Queue Status:**\n"
        status += f"Pending Links: `{len(self.queue)}`\n\n"
        status += "**🤖 Active Slots:**\n"
        for i in range(5):
            slot_data = self.slots[i]
            bot_name = f"Bot {i+1}"
            if slot_data:
                link_preview = slot_data['link'][:30] + "..." if len(slot_data['link']) > 30 else slot_data['link']
                status += f"• **{bot_name}**: 🔴 Busy (Retry: {slot_data['retries']})\n"
                status += f"  `{link_preview}`\n"
            else:
                status += f"• **{bot_name}**: 🟢 Free\n"
        return status

    async def process_queue(self, client, target_chat_id):
        """
        Checks for free slots and assigns pending links to them.
        This function should be called whenever a slot becomes free or new links are added.
        """
        for i in range(5):
            if self.slots[i] is None: # Slot is free
                if self.queue:
                    # Get next link
                    task = self.queue.popleft()
                    link = task['link']

                    # Assign to slot
                    self.slots[i] = task

                    # Send command
                    command = f"/leech{i+1} {link}"
                    try:
                        logger.info(f"Assigning task to Bot {i+1}: {link}")
                        await client.send_message(target_chat_id, command)
                        await self._log_to_channel(client, f"🚀 **Started Task on Bot {i+1}:**\n`{link}`")
                    except Exception as e:
                        logger.error(f"Failed to send command for Bot {i+1}: {e}")
                        # If sending fails, put back in queue or retry immediately?
                        # For now, let's treat as a failure of the slot and retry logic will handle it
                        # But wait, if send fails, we should probably mark slot as free and retry later?
                        # Let's put it back at the front of the queue
                        self.queue.appendleft(task)
                        self.slots[i] = None # Free the slot
                else:
                    # Queue empty, nothing to do for this slot
                    pass

    async def mark_completed(self, slot_id: int, success: bool, client, target_chat_id):
        """
        Called when a bot finishes a task.
        slot_id: 0-4
        success: True (completed) / False (failed)
        """
        if slot_id not in self.slots:
            logger.error(f"Invalid slot ID {slot_id}")
            return

        task = self.slots[slot_id]
        if not task:
            logger.warning(f"Slot {slot_id} marked completed but was empty/free. Ignoring.")
            return

        if success:
            logger.info(f"Slot {slot_id} (Bot {slot_id+1}) completed task successfully.")
            await self._log_to_channel(client, f"✅ **Task Completed on Bot {slot_id+1}:**\n`{task['link']}`")
            self.slots[slot_id] = None # Free the slot
            # Trigger processing of queue to fill the now-free slot
            await self.process_queue(client, target_chat_id)
        else:
            logger.warning(f"Slot {slot_id} (Bot {slot_id+1}) failed task.")
            # Retry logic
            if task['retries'] < self.max_retries:
                task['retries'] += 1
                logger.info(f"Retrying task (Attempt {task['retries']}/{self.max_retries}) on same bot...")
                await self._log_to_channel(client, f"⚠️ **Task Failed on Bot {slot_id+1} (Retry {task['retries']}/{self.max_retries}):**\n`{task['link']}`")

                # Retry immediately on the SAME slot
                link = task['link']
                command = f"/leech{slot_id+1} {link}"
                try:
                    await client.send_message(target_chat_id, command)
                    # Slot remains busy with same task
                except Exception as e:
                    logger.error(f"Failed to retry command for Bot {slot_id+1}: {e}")
                    # If retry send fails, put back in queue
                    self.queue.appendleft(task)
                    self.slots[slot_id] = None
                    await self.process_queue(client, target_chat_id)
            else:
                logger.error(f"Task failed after {self.max_retries} retries. Dropping link: {task['link']}")
                await self._log_to_channel(client, f"❌ **Task Failed PERMANENTLY on Bot {slot_id+1}:**\n`{task['link']}`")
                self.slots[slot_id] = None # Free the slot, give up on this link
                await self.process_queue(client, target_chat_id)

# Global instance
queue_manager = QueueManager()
