"""
Chat Page RPC Handlers
======================

RPC endpoints for CHAT.* namespace.
Handles chat session management (distinct from LLM streaming in llm_service/).

These handlers manage:
- Chat session CRUD (create, list, delete, rename)
- Message history loading
- Chat mode switching
- Task management for chat context

Dependencies (Existing Services - DO NOT MODIFY):
- firebase_realtime_chat.py: FirebaseRealtimeChat singleton
- firebase_providers.py: FirebaseManagement singleton
- redis_client.py: Redis cache
- ws_hub.py: WebSocket broadcasting
"""

import logging
from typing import Any, Dict, List, Optional

from app.cache.unified_cache_manager import get_firebase_cache_manager
from app.firebase_providers import get_firebase_management
from app.redis_client import get_redis

logger = logging.getLogger("chat.handlers")

# Cache TTLs
TTL_SESSIONS_LIST = 60  # 1 minute for session list
TTL_HISTORY = 120  # 2 minutes for message history
TTL_TASKS = 300  # 5 minutes for task list


# ============================================
# SINGLETON
# ============================================

_chat_handlers: Optional["ChatHandlers"] = None


def get_chat_handlers() -> "ChatHandlers":
    """Get singleton instance of chat handlers."""
    global _chat_handlers
    if _chat_handlers is None:
        _chat_handlers = ChatHandlers()
    return _chat_handlers


# ============================================
# CHAT HANDLERS CLASS
# ============================================

class ChatHandlers:
    """
    RPC handlers for CHAT namespace.

    Handles chat session management and integrates with
    FirebaseRealtimeChat for real-time messaging.
    """

    def __init__(self):
        self._cache_manager = get_firebase_cache_manager()
        self._firebase = get_firebase_management()
        self._redis = get_redis()

    # ──────────────────────────────────────────
    # SESSION MANAGEMENT
    # ──────────────────────────────────────────

    async def list_sessions(
        self,
        uid: str,
        company_id: str,
        space_code: str,
        mode: str = "chats",
    ) -> Dict[str, Any]:
        """
        CHAT.sessions_list - Fetch all chat sessions for user/company.

        This replaces ChatState.load_all_chat_titles().

        Args:
            uid: User ID
            company_id: Company ID (contact_space_id)
            space_code: Firebase space code (usually same as company_id)
            mode: Firebase mode ('chats' for user chats)

        Returns:
            {"success": True, "sessions": [...], "total": int}
        """
        # 1. Check cache
        cached = await self._cache_manager.get_cached_data(
            user_id=uid,
            company_id=company_id,
            data_type="chat:sessions"
        )
        if cached:
            logger.info(f"[CHAT] Cache hit for sessions list")
            sessions_data = cached.get("data", cached) if isinstance(cached, dict) else cached
            return {"success": True, "sessions": sessions_data, "total": len(sessions_data), "from_cache": True}

        # 2. Fetch from Firebase Realtime
        try:
            from app.firebase_providers import get_firebase_realtime

            realtime_service = get_firebase_realtime()
            threads = realtime_service.get_all_threads(
                space_code=space_code,
                mode=mode
            )

            if not threads:
                return {"success": True, "sessions": [], "total": 0}

            # 3. Transform to session list
            sessions = []
            for thread_key, thread_data in threads.items():
                thread_name = thread_data.get("thread_name", thread_key)

                # Skip "New chat" placeholder threads
                if thread_name == "New chat" or thread_name.strip() == "New chat":
                    continue

                sessions.append({
                    "id": thread_key,
                    "name": thread_name,
                    "mode": mode,
                    "chat_mode": thread_data.get("chat_mode", "general_chat"),
                    "thread_key": thread_data.get("thread_key", thread_key),
                    "last_activity": thread_data.get("last_activity", ""),
                    "message_count": thread_data.get("message_count", 0),
                })

            # 4. Sort by last_activity (newest first)
            sessions.sort(key=lambda x: x.get("last_activity", ""), reverse=True)

            # 5. Cache result
            await self._cache_manager.set_cached_data(
                user_id=uid,
                company_id=company_id,
                data_type="chat:sessions",
                data=sessions,
                ttl_seconds=TTL_SESSIONS_LIST
            )

            logger.info(f"[CHAT] Loaded {len(sessions)} sessions for uid={uid}")
            return {"success": True, "sessions": sessions, "total": len(sessions)}

        except Exception as e:
            logger.error(f"[CHAT] Error loading sessions: {e}")
            return {"success": False, "error": str(e), "sessions": [], "total": 0}

    async def create_session(
        self,
        uid: str,
        company_id: str,
        space_code: str,
        chat_mode: str = "general_chat",
        thread_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        CHAT.session_create - Create a new chat session.

        Args:
            uid: User ID
            company_id: Company ID
            space_code: Firebase space code
            chat_mode: Chat mode (general_chat, onboarding_chat, etc.)
            thread_name: Optional initial name (will be auto-generated if not provided)

        Returns:
            {"success": True, "session": {...}}
        """
        try:
            from app.firebase_providers import get_firebase_realtime
            import uuid
            from datetime import datetime, timezone

            # Generate thread key
            thread_key = f"{int(datetime.now().timestamp())}_{space_code}_{uuid.uuid4().hex[:8]}"

            # Default name if not provided
            if not thread_name:
                thread_name = f"Chat {datetime.now().strftime('%H:%M')}"

            realtime_service = get_firebase_realtime()
            result = realtime_service.create_chat(
                user_id=uid,
                space_code=space_code,
                thread_name=thread_name,
                mode="chats",
                chat_mode=chat_mode,
                thread_key=thread_key
            )

            if not result or not result.get("success"):
                error_msg = result.get("error", "Failed to create chat session") if result else "Failed to create chat session"
                return {"success": False, "error": error_msg}

            # Use thread_key from result (it may have been generated by Firebase)
            actual_thread_key = result.get("thread_key", thread_key)

            session = {
                "id": actual_thread_key,
                "name": thread_name,
                "mode": "chats",
                "chat_mode": chat_mode,
                "thread_key": actual_thread_key,
                "last_activity": result.get("last_activity", datetime.now(timezone.utc).isoformat()),
                "message_count": 0,
            }

            # Invalidate sessions cache
            await self._cache_manager.invalidate_cache(
                user_id=uid,
                company_id=company_id,
                data_type="chat:sessions"
            )

            logger.info(f"[CHAT] Created session: {thread_key}")
            return {"success": True, "session": session}

        except Exception as e:
            logger.error(f"[CHAT] Error creating session: {e}")
            return {"success": False, "error": str(e)}

    async def delete_session(
        self,
        uid: str,
        company_id: str,
        space_code: str,
        thread_key: str,
        mode: str = "chats",
    ) -> Dict[str, Any]:
        """
        CHAT.session_delete - Delete a chat session.

        Args:
            uid: User ID
            company_id: Company ID
            space_code: Firebase space code
            thread_key: Thread key to delete
            mode: Firebase mode

        Returns:
            {"success": True}
        """
        try:
            from app.firebase_providers import get_firebase_realtime

            realtime_service = get_firebase_realtime()
            success = realtime_service.delete_chat(
                space_code=space_code,
                thread_key=thread_key,
                mode=mode
            )

            if not success:
                return {"success": False, "error": "Failed to delete chat session"}

            # Invalidate sessions cache
            await self._cache_manager.invalidate_cache(
                user_id=uid,
                company_id=company_id,
                data_type="chat:sessions"
            )

            logger.info(f"[CHAT] Deleted session: {thread_key}")
            return {"success": True, "thread_key": thread_key}

        except Exception as e:
            logger.error(f"[CHAT] Error deleting session: {e}")
            return {"success": False, "error": str(e)}

    async def rename_session(
        self,
        uid: str,
        company_id: str,
        space_code: str,
        thread_key: str,
        new_name: str,
        mode: str = "chats",
    ) -> Dict[str, Any]:
        """
        CHAT.session_rename - Rename a chat session.

        Args:
            uid: User ID
            company_id: Company ID
            space_code: Firebase space code
            thread_key: Thread key to rename
            new_name: New thread name
            mode: Firebase mode

        Returns:
            {"success": True, "new_name": str}
        """
        try:
            from app.firebase_providers import get_firebase_realtime

            realtime_service = get_firebase_realtime()
            success = realtime_service.update_thread_name(
                space_code=space_code,
                thread_key=thread_key,
                new_name=new_name,
                mode=mode
            )

            if not success:
                return {"success": False, "error": "Failed to rename chat session"}

            # Invalidate sessions cache
            await self._cache_manager.invalidate_cache(
                user_id=uid,
                company_id=company_id,
                data_type="chat:sessions"
            )

            logger.info(f"[CHAT] Renamed session {thread_key} to '{new_name}'")
            return {"success": True, "thread_key": thread_key, "new_name": new_name}

        except Exception as e:
            logger.error(f"[CHAT] Error renaming session: {e}")
            return {"success": False, "error": str(e)}

    async def auto_name_session(
        self,
        uid: str,
        company_id: str,
        space_code: str,
        thread_key: str,
        first_message: str,
        mode: str = "chats",
    ) -> Dict[str, Any]:
        """
        CHAT.session_auto_name - Auto-generate a name for a chat session based on first message.

        This is called after the first message is sent to a new/virgin chat session.
        The name is generated from the first message content.

        Args:
            uid: User ID
            company_id: Company ID
            space_code: Firebase space code
            thread_key: Thread key to rename
            first_message: The first message content to generate name from
            mode: Firebase mode

        Returns:
            {"success": True, "new_name": str, "thread_key": str}
        """
        try:
            # Generate name from first message
            generated_name = self._generate_chat_name(first_message)

            # Update the session name
            result = await self.rename_session(
                uid=uid,
                company_id=company_id,
                space_code=space_code,
                thread_key=thread_key,
                new_name=generated_name,
                mode=mode
            )

            if result.get("success"):
                logger.info(f"[CHAT] Auto-named session {thread_key} to '{generated_name}'")
                return {
                    "success": True,
                    "thread_key": thread_key,
                    "new_name": generated_name,
                    "generated_from": "first_message"
                }
            else:
                return result

        except Exception as e:
            logger.error(f"[CHAT] Error auto-naming session: {e}")
            return {"success": False, "error": str(e)}

    def _generate_chat_name(self, first_message: str, max_length: int = 50) -> str:
        """
        Generate a chat name from the first message.

        Uses a heuristic approach:
        1. Clean and truncate the message
        2. Remove common prefixes (Bonjour, Hello, etc.)
        3. Capitalize first letter

        Can be enhanced later with LLM-based naming.

        Args:
            first_message: The first user message
            max_length: Maximum name length

        Returns:
            Generated chat name
        """
        if not first_message or not first_message.strip():
            from datetime import datetime
            return f"Chat {datetime.now().strftime('%d/%m %H:%M')}"

        # Clean the message
        name = first_message.strip()

        # Remove common greetings at the start
        greetings = [
            "bonjour", "bonsoir", "salut", "hello", "hi", "hey",
            "guten tag", "hallo", "s'il vous plaît", "please",
            "j'aimerais", "je voudrais", "i would like", "i want to",
            "peux-tu", "pouvez-vous", "can you", "could you",
        ]
        name_lower = name.lower()
        for greeting in greetings:
            if name_lower.startswith(greeting):
                # Remove greeting and any following punctuation/space
                name = name[len(greeting):].lstrip(" ,.:!?")
                break

        # Truncate at sentence boundary if possible
        for delimiter in [". ", "? ", "! ", "\n"]:
            if delimiter in name:
                name = name.split(delimiter)[0]
                break

        # Truncate to max length
        if len(name) > max_length:
            # Try to break at word boundary
            name = name[:max_length]
            last_space = name.rfind(" ")
            if last_space > max_length * 0.6:  # Don't truncate too much
                name = name[:last_space]
            name = name.rstrip() + "..."

        # Capitalize first letter
        if name:
            name = name[0].upper() + name[1:] if len(name) > 1 else name.upper()

        # Fallback if empty after processing
        if not name or len(name) < 3:
            from datetime import datetime
            return f"Chat {datetime.now().strftime('%d/%m %H:%M')}"

        return name

    # ──────────────────────────────────────────
    # MESSAGE HISTORY
    # ──────────────────────────────────────────

    async def load_history(
        self,
        uid: str,
        company_id: str,
        space_code: str,
        thread_key: str,
        mode: str = "chats",
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        CHAT.history_load - Load message history for a chat session.

        Args:
            uid: User ID
            company_id: Company ID
            space_code: Firebase space code
            thread_key: Thread key to load
            mode: Firebase mode
            limit: Max messages to load

        Returns:
            {"success": True, "messages": [...], "total": int}
        """
        # 1. Check cache
        cached = await self._cache_manager.get_cached_data(
            user_id=uid,
            company_id=company_id,
            data_type="chat:history",
            sub_type=thread_key
        )
        if cached:
            logger.info(f"[CHAT] Cache hit for history: {thread_key}")
            messages_data = cached.get("data", cached) if isinstance(cached, dict) else cached
            return {"success": True, "messages": messages_data, "total": len(messages_data), "from_cache": True}

        # 2. Fetch from Firebase Realtime
        try:
            from app.firebase_providers import get_firebase_realtime

            realtime_service = get_firebase_realtime()
            messages = realtime_service.get_thread_messages(
                space_code=space_code,
                thread_key=thread_key,
                mode=mode,
                limit=limit
            )

            if messages is None:
                messages = []

            # 3. Transform messages to standard format
            formatted_messages = self._transform_messages(messages)

            # 4. Cache result
            await self._cache_manager.set_cached_data(
                user_id=uid,
                company_id=company_id,
                data_type="chat:history",
                sub_type=thread_key,
                data=formatted_messages,
                ttl_seconds=TTL_HISTORY
            )

            logger.info(f"[CHAT] Loaded {len(formatted_messages)} messages for {thread_key}")
            return {"success": True, "messages": formatted_messages, "total": len(formatted_messages)}

        except Exception as e:
            logger.error(f"[CHAT] Error loading history: {e}")
            return {"success": False, "error": str(e), "messages": [], "total": 0}

    def _transform_messages(self, raw_messages: List[Dict]) -> List[Dict[str, Any]]:
        """
        Transform raw Firebase messages to standard format.

        Migrated from ChatState message handling logic.
        Handles message types based on message_type field:
        - MESSAGE: Bot response, content is JSON: {"message": {"argumentText": "..."}}
        - MESSAGE_PINNOKIO: User message, content is plain text
        - CARD / CARD_CLICKED_PINNOKIO: Interactive cards
        - CMMD: Command messages
        """
        import json

        transformed = []
        for msg in raw_messages:
            message_type = msg.get("message_type", "")
            sender_id = msg.get("sender_id", "")

            # Extract content based on message_type (from ChatState logic)
            content = self._extract_message_content(msg, message_type)

            # Skip empty messages
            if not content and message_type not in ["CARD", "CARD_CLICKED_PINNOKIO"]:
                continue

            # Determine role based on message_type (from ChatState logic)
            if message_type == "MESSAGE":
                # Bot/assistant response
                role = "assistant"
            elif message_type == "MESSAGE_PINNOKIO":
                # User message
                role = "user"
            elif message_type in ["CARD", "CARD_CLICKED_PINNOKIO"]:
                # Card interactions - treat as system/assistant
                role = "assistant"
            elif message_type == "CMMD":
                # Command messages - system/assistant
                role = "system"
            else:
                # Fallback: check sender_id
                role = msg.get("role", "")
                if not role:
                    if sender_id == "bot" or sender_id == "assistant":
                        role = "assistant"
                    else:
                        role = "user"

            transformed.append({
                "id": msg.get("message_id", msg.get("id", "")),
                "role": role,
                "content": content,
                "timestamp": msg.get("timestamp", ""),
                "type": message_type or "text",
                "metadata": msg.get("metadata", {}),
            })
        return transformed

    def _extract_message_content(self, msg: Dict, message_type: str = "") -> str:
        """
        Extract the actual text content from various message formats.

        Based on ChatState logic:
        - MESSAGE: content is JSON-string: '{"message": {"argumentText": "..."}}'
        - MESSAGE_PINNOKIO: content is plain text
        - CMMD: content is JSON with command data
        - CARD: content contains card data

        Args:
            msg: Raw message dict from Firebase
            message_type: The message_type field value

        Returns:
            Extracted text content
        """
        import json

        content_raw = msg.get("content", "")

        # Handle based on message_type (from ChatState)
        if message_type == "MESSAGE":
            # Bot response: content is JSON-stringified
            # Format: '{"message": {"argumentText": "actual text"}}'
            try:
                if isinstance(content_raw, str) and content_raw.strip():
                    content_parsed = json.loads(content_raw)
                    # Extract from nested structure
                    if isinstance(content_parsed, dict):
                        message_obj = content_parsed.get("message", {})
                        if isinstance(message_obj, dict):
                            # Primary format: {"message": {"argumentText": "..."}}
                            if "argumentText" in message_obj:
                                return message_obj["argumentText"].strip()
                            # Fallback: {"message": {"text": "..."}}
                            if "text" in message_obj:
                                return message_obj["text"].strip()
                        elif isinstance(message_obj, str):
                            return message_obj.strip()
                        # Direct text in parsed content
                        if "text" in content_parsed:
                            return content_parsed["text"].strip()
                    elif isinstance(content_parsed, str):
                        return content_parsed.strip()
            except json.JSONDecodeError:
                # If JSON parsing fails, return raw content
                logger.warning(f"[CHAT] Failed to parse MESSAGE content as JSON: {content_raw[:100]}")
                return content_raw.strip() if isinstance(content_raw, str) else ""

        elif message_type == "MESSAGE_PINNOKIO":
            # User message: content is plain text
            if isinstance(content_raw, str):
                return content_raw.strip()
            return ""

        elif message_type == "CMMD":
            # Command message: extract action description
            try:
                if isinstance(content_raw, str) and content_raw.strip():
                    content_parsed = json.loads(content_raw)
                    cmmd = content_parsed.get("message", {}).get("cmmd", {})
                    action = cmmd.get("action", "")
                    return f"[Command: {action}]" if action else ""
            except json.JSONDecodeError:
                pass
            return ""

        elif message_type in ["CARD", "CARD_CLICKED_PINNOKIO"]:
            # Card interactions: extract card title or type
            try:
                if isinstance(content_raw, str) and content_raw.strip():
                    content_parsed = json.loads(content_raw)
                    card_type = content_parsed.get("cardType", "")
                    card_params = content_parsed.get("cardParams", {})
                    title = card_params.get("title", "")
                    return f"[Card: {title or card_type}]" if (title or card_type) else "[Interactive Card]"
            except json.JSONDecodeError:
                pass
            return "[Interactive Card]"

        # Fallback: try various formats
        # Direct text field
        if "text" in msg and isinstance(msg["text"], str):
            return msg["text"].strip()

        # Direct content field (non-JSON string)
        if isinstance(content_raw, str) and content_raw.strip():
            # Try JSON parsing as last resort
            try:
                content_parsed = json.loads(content_raw)
                if isinstance(content_parsed, dict):
                    # {"message": {"argumentText": "..."}}
                    if "message" in content_parsed:
                        message_obj = content_parsed["message"]
                        if isinstance(message_obj, dict) and "argumentText" in message_obj:
                            return message_obj["argumentText"].strip()
                    # {"text": "..."}
                    if "text" in content_parsed:
                        return content_parsed["text"].strip()
            except json.JSONDecodeError:
                # Not JSON, return as-is
                return content_raw.strip()

        # Nested message object (direct dict format)
        if "message" in msg:
            message_data = msg["message"]
            if isinstance(message_data, dict):
                if "argumentText" in message_data:
                    return message_data["argumentText"].strip()
                if "text" in message_data:
                    return message_data["text"].strip()
            elif isinstance(message_data, str):
                return message_data.strip()

        # Fallback: return empty string
        logger.warning(f"[CHAT] Unknown message format: type={message_type}, keys={list(msg.keys())}")
        return ""

    # ──────────────────────────────────────────
    # TASK MANAGEMENT (Chat-specific tasks)
    # ──────────────────────────────────────────

    async def list_tasks(
        self,
        uid: str,
        company_id: str,
        mandate_path: str,
    ) -> Dict[str, Any]:
        """
        CHAT.tasks_list - List scheduled tasks for the mandate.

        Args:
            uid: User ID
            company_id: Company ID
            mandate_path: Firebase mandate path

        Returns:
            {"success": True, "tasks": [...], "total": int}
        """
        # 1. Check cache
        cached = await self._cache_manager.get_cached_data(
            user_id=uid,
            company_id=company_id,
            data_type="chat:tasks"
        )
        if cached:
            logger.info(f"[CHAT] Cache hit for tasks")
            tasks_data = cached.get("data", cached) if isinstance(cached, dict) else cached
            return {"success": True, "tasks": tasks_data, "total": len(tasks_data), "from_cache": True}

        # 2. Fetch from Firebase
        try:
            tasks = self._firebase.list_tasks_for_mandate(mandate_path)

            if not tasks:
                return {"success": True, "tasks": [], "total": 0}

            # 3. Transform tasks (handle nested structure from Firebase)
            formatted_tasks = []
            for task in tasks:
                # Extract nested mission data
                mission = task.get("mission", {})
                mission_title = mission.get("title", "") if isinstance(mission, dict) else ""
                mission_description = mission.get("description", "") if isinstance(mission, dict) else ""
                mission_plan = mission.get("plan", "") if isinstance(mission, dict) else ""

                # Extract nested schedule data
                schedule = task.get("schedule", {})
                schedule_frequency = schedule.get("frequency", "") if isinstance(schedule, dict) else ""
                schedule_time = schedule.get("time", "") if isinstance(schedule, dict) else ""
                schedule_timezone = schedule.get("timezone", "") if isinstance(schedule, dict) else ""
                cron_expr = schedule.get("cron_expression", "") if isinstance(schedule, dict) else ""
                next_exec_local = schedule.get("next_execution_local_time", "") if isinstance(schedule, dict) else ""

                # Fallback to flat fields if nested not available
                task_name = mission_title or task.get("task_name", task.get("name", "Untitled Task"))
                task_description = mission_description or task.get("task_description", "")

                # Get status and enabled state
                enabled = task.get("enabled", task.get("is_enabled", True))
                status = task.get("status", "idle")

                # Map execution_plan to chat_mode if available
                execution_plan = task.get("execution_plan", task.get("execution_mode", ""))
                chat_mode = task.get("chat_mode", "general_chat")

                # Compute next_run from schedule or flat field
                next_run = next_exec_local or task.get("next_run", task.get("scheduled_next_execution", ""))

                formatted_tasks.append({
                    "id": task.get("task_id", task.get("id", "")),
                    "name": task_name,
                    "description": task_description,
                    "plan": mission_plan,
                    "chat_mode": chat_mode,
                    "execution_plan": execution_plan,
                    "is_enabled": enabled,
                    "schedule": {
                        "frequency": schedule_frequency,
                        "time": schedule_time,
                        "timezone": schedule_timezone,
                    } if schedule else {},
                    "cron_expression": cron_expr or task.get("cron_expression", ""),
                    "last_run": task.get("last_run", ""),
                    "next_run": next_run,
                    "status": status,
                })

            # 4. Cache result
            await self._cache_manager.set_cached_data(
                user_id=uid,
                company_id=company_id,
                data_type="chat:tasks",
                data=formatted_tasks,
                ttl_seconds=TTL_TASKS
            )

            logger.info(f"[CHAT] Loaded {len(formatted_tasks)} tasks")
            return {"success": True, "tasks": formatted_tasks, "total": len(formatted_tasks)}

        except Exception as e:
            logger.error(f"[CHAT] Error loading tasks: {e}")
            return {"success": False, "error": str(e), "tasks": [], "total": 0}

    async def toggle_task(
        self,
        uid: str,
        company_id: str,
        mandate_path: str,
        task_id: str,
        is_enabled: bool,
    ) -> Dict[str, Any]:
        """
        CHAT.task_toggle - Enable or disable a scheduled task.

        Args:
            uid: User ID
            company_id: Company ID
            mandate_path: Firebase mandate path
            task_id: Task ID
            is_enabled: New enabled state

        Returns:
            {"success": True}
        """
        try:
            success = self._firebase.update_task(
                mandate_path=mandate_path,
                task_id=task_id,
                updates={"is_enabled": is_enabled}
            )

            if not success:
                return {"success": False, "error": "Failed to update task"}

            # Invalidate tasks cache
            await self._cache_manager.invalidate_cache(
                user_id=uid,
                company_id=company_id,
                data_type="chat:tasks"
            )

            logger.info(f"[CHAT] Task {task_id} toggled to {is_enabled}")
            return {"success": True, "task_id": task_id, "is_enabled": is_enabled}

        except Exception as e:
            logger.error(f"[CHAT] Error toggling task: {e}")
            return {"success": False, "error": str(e)}


# ============================================
# EXPORTS
# ============================================

__all__ = [
    "ChatHandlers",
    "get_chat_handlers",
]
