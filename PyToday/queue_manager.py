import asyncio
import logging
import time
from collections import deque
from typing import List, Dict, Optional, Any
from PyToday import config

logger = logging.getLogger("QueueManager")

# Retry Delays (in seconds)
# Attempt 1: 0 (Immediate) -> Fail -> Wait 10m (600s) -> Retry (Attempt 2)
# Attempt 2: Fail -> Wait 10m (600s) -> Retry (Attempt 3)
# Attempt 3: Fail -> Wait 30m (1800s) -> Retry (Attempt 4)
# Attempt 4: Fail -> Wait 10m (600s) -> Retry (Attempt 5)
# Attempt 5: Fail -> Wait 10m (600s) -> Retry (Attempt 6)
# Attempt 6: Fail -> Permanent Failure

RETRY_DELAYS = {
    0: 0,       # Initial attempt
    1: 600,     # After 1st fail (10m)
    2: 600,     # After 2nd fail (10m)
    3: 1800,    # After 3rd fail (30m)
    4: 600,     # After 4th fail (10m)
    5: 600,     # After 5th fail (10m)
}
MAX_RETRIES = 6

class QueueManager:
    def __init__(self):
        self.queue = deque()
        # Slots: 0-4 (corresponding to bots 1-5)
        # Value is None if free, or a dict { 'link': str, 'retries': int, 'status': str, 'last_attempt': float }
        self.slots: Dict[int, Optional[Dict[str, Any]]] = {i: None for i in range(5)}
        self.paused = False
        self.processing_task: Optional[asyncio.Task] = None

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
                # Store task metadata: link, retries (0), status (pending)
                self.queue.append({'link': link.strip(), 'retries': 0, 'status': 'pending'})
                count += 1
        logger.info(f"Added {count} links to queue. Total pending: {len(self.queue)}")
        return count

    def get_status(self) -> str:
        """Returns a string describing the current status."""
        status = f"**📊 Queue Status:**\n"
        status += f"Pending Links: `{len(self.queue)}`\n"
        status += f"State: `{'⏸️ PAUSED' if self.paused else '▶️ RUNNING'}`\n\n"
        status += "**🤖 Active Slots:**\n"

        for i in range(5):
            slot_data = self.slots[i]
            bot_name = f"Bot {i+1}"
            if slot_data:
                link_preview = slot_data['link'][:30] + "..." if len(slot_data['link']) > 30 else slot_data['link']
                retry_count = slot_data['retries']
                current_status = slot_data.get('status', 'unknown')

                # Check if waiting for retry
                if current_status == 'waiting_retry':
                    next_attempt = slot_data.get('next_attempt_time', 0)
                    wait_time = max(0, int(next_attempt - time.time()))
                    status += f"• **{bot_name}**: ⏳ Retry in {wait_time}s (Att: {retry_count}/{MAX_RETRIES})\n"
                else:
                    status += f"• **{bot_name}**: 🔄 {current_status.title()} (Att: {retry_count}/{MAX_RETRIES})\n"

                status += f"  `{link_preview}`\n"
            else:
                status += f"• **{bot_name}**: 🟢 Free\n"
        return status

    async def process_queue(self, client, target_chat_id):
        """
        Main loop to process queue.
        Checks for free slots and assigns pending links to them.
        Also handles retries for waiting slots.
        """
        if self.paused:
            logger.info("Queue processing is PAUSED.")
            return

        # Check existing slots for retries
        for i in range(5):
            slot = self.slots[i]
            if slot and slot.get('status') == 'waiting_retry':
                next_time = slot.get('next_attempt_time', 0)
                if time.time() >= next_time:
                    # Time to retry!
                    logger.info(f"Retrying task on Slot {i} (Attempt {slot['retries']})...")
                    slot['status'] = 'processing'
                    link = slot['link']
                    command = f"/leech{i+1} {link}"
                    try:
                        await client.send_message(target_chat_id, command)
                        await self._log_to_channel(client, f"🔄 **Retrying Task on Bot {i+1} (Attempt {slot['retries']}):**\n`{link}`")
                    except Exception as e:
                        logger.error(f"Failed to retry command for Bot {i+1}: {e}")
                        # If send fails, what do we do?
                        # Count as another fail immediately?
                        # Or just wait a bit and try again without incrementing retries?
                        # Let's wait 60s and try again (stay in waiting_retry)
                        slot['next_attempt_time'] = time.time() + 60

        # Fill free slots
        for i in range(5):
            if self.paused: break

            if self.slots[i] is None: # Slot is free
                if self.queue:
                    # Get next link
                    task = self.queue.popleft()
                    link = task['link']

                    # Initialize slot
                    task['retries'] = 1 # 1st attempt
                    task['status'] = 'processing'
                    task['last_attempt'] = time.time()

                    self.slots[i] = task

                    # Send command
                    command = f"/leech{i+1} {link}"
                    try:
                        logger.info(f"Assigning task to Bot {i+1}: {link}")
                        await client.send_message(target_chat_id, command)
                        await self._log_to_channel(client, f"🚀 **Started Task on Bot {i+1}:**\n`{link}`")
                    except Exception as e:
                        logger.error(f"Failed to send command for Bot {i+1}: {e}")
                        # Send failed. Put back in queue?
                        self.queue.appendleft(task)
                        self.slots[i] = None # Free the slot

    async def mark_completed(self, slot_id: int, success: bool, client, target_chat_id):
        """
        Called when a bot finishes a task.
        """
        if slot_id not in self.slots:
            return

        task = self.slots[slot_id]
        if not task:
            return

        if success:
            logger.info(f"Slot {slot_id} completed task successfully.")
            await self._log_to_channel(client, f"✅ **Task Completed on Bot {slot_id+1}:**\n`{task['link']}`")
            self.slots[slot_id] = None # Free the slot
            await self.process_queue(client, target_chat_id)
        else:
            # Task Failed
            current_retries = task['retries']

            if current_retries < MAX_RETRIES:
                # Schedule Retry
                delay = RETRY_DELAYS.get(current_retries, 600)
                next_attempt = time.time() + delay

                task['retries'] += 1
                task['status'] = 'waiting_retry'
                task['next_attempt_time'] = next_attempt

                logger.warning(f"Slot {slot_id} failed. Retrying in {delay}s (Attempt {task['retries']}).")
                await self._log_to_channel(client, f"⚠️ **Task Failed on Bot {slot_id+1}.**\nWaiting {delay}s for Retry {task['retries']}/{MAX_RETRIES}...")

                # We do NOT free the slot. It holds the slot while waiting.
                # However, we need a background task to check for retries?
                # YES. process_queue needs to be called periodically or we need a scheduler.
                # Since we don't have a scheduler loop running, we can use asyncio.sleep?
                # But that blocks.
                # Better: When we schedule a retry, we spawn a background task to wake up process_queue.
                asyncio.create_task(self._wait_and_trigger(delay, client, target_chat_id))

            else:
                # Max Retries Exceeded
                logger.error(f"Task failed after {MAX_RETRIES} retries. Dropping link: {task['link']}")
                await self._log_to_channel(client, f"❌ **Task Failed PERMANENTLY on Bot {slot_id+1}:**\n`{task['link']}`\n(Max retries reached)")
                self.slots[slot_id] = None # Free the slot
                await self.process_queue(client, target_chat_id)

    async def _wait_and_trigger(self, delay, client, target_chat_id):
        """Helper to wait for retry delay and then trigger queue processing."""
        await asyncio.sleep(delay)
        await self.process_queue(client, target_chat_id)

    def pause(self):
        self.paused = True
        logger.info("Queue paused.")

    def resume(self, client, target_chat_id):
        self.paused = False
        logger.info("Queue resumed.")
        # Trigger processing immediately
        return self.process_queue(client, target_chat_id)

    def stop_all(self):
        self.paused = True
        self.queue.clear()
        # Clear active slots? Or let them finish?
        # User said "stop whole process". Usually means clear everything.
        for i in range(5):
            self.slots[i] = None
        logger.info("Stopped all processing and cleared queue.")

# Global instance
queue_manager = QueueManager()
