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
        # 1. Check cache (use mode in cache key to separate chats vs active_chats)
        cache_key = f"chat:sessions:{mode}"
        cached = await self._cache_manager.get_cached_data(
            user_id=uid,
            company_id=company_id,
            data_type=cache_key
        )
        if cached:
            logger.info(f"[CHAT] Cache hit for sessions list (mode={mode})")
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

            # 5. Cache result (use mode in cache key)
            await self._cache_manager.set_cached_data(
                user_id=uid,
                company_id=company_id,
                data_type=cache_key,
                data=sessions,
                ttl_seconds=TTL_SESSIONS_LIST
            )

            logger.info(f"[CHAT] Loaded {len(sessions)} sessions for uid={uid}")
            return {"success": True, "sessions": sessions, "total": len(sessions)}

        except Exception as e:
            logger.error(f"[CHAT] Error loading sessions: {e}")
            return {"success": False, "error": str(e), "sessions": [], "total": 0}

    async def list_all_sessions(
        self,
        uid: str,
        company_id: str,
        space_code: str,
    ) -> Dict[str, Any]:
        """
        CHAT.sessions_list_all - Fetch chat sessions from both compartments.

        Loads:
        - mode="chats" for regular user chats (general_chat)
        - mode="active_chats" for job-created chats (apbookeeper, banker, router)

        Returns:
            {"success": True, "sessions": [...], "total": int}
        """
        try:
            # 1. Load regular chats (general_chat mode)
            chats_result = await self.list_sessions(
                uid=uid,
                company_id=company_id,
                space_code=space_code,
                mode="chats"
            )
            chats_sessions = chats_result.get("sessions", [])

            # 2. Load active chats (apbookeeper, banker, router modes)
            active_result = await self.list_sessions(
                uid=uid,
                company_id=company_id,
                space_code=space_code,
                mode="active_chats"
            )
            active_sessions = active_result.get("sessions", [])

            # 3. Filter active_chats to only include specialized modes
            # (exclude onboarding_chat and any general_chat that might be there)
            specialized_modes = {'apbookeeper_chat', 'banker_chat', 'router_chat'}
            active_sessions = [
                s for s in active_sessions
                if s.get("chat_mode") in specialized_modes
            ]

            # 4. Merge and deduplicate by thread_key (prefer active_chats over chats)
            # Build a dict keyed by thread_key, active_chats will override chats if duplicate
            sessions_dict = {s.get("thread_key"): s for s in chats_sessions}
            for s in active_sessions:
                sessions_dict[s.get("thread_key")] = s  # Override if exists

            all_sessions = list(sessions_dict.values())

            # 5. Sort by last_activity
            all_sessions.sort(key=lambda x: x.get("last_activity", ""), reverse=True)

            logger.info(f"[CHAT] Loaded {len(chats_sessions)} from chats, {len(active_sessions)} from active_chats")

            return {
                "success": True,
                "sessions": all_sessions,
                "total": len(all_sessions),
                "sources": {
                    "chats": len(chats_sessions),
                    "active_chats": len(active_sessions),
                }
            }

        except Exception as e:
            logger.error(f"[CHAT] Error loading all sessions: {e}")
            return {"success": False, "error": str(e), "sessions": [], "total": 0}

    async def create_session(
        self,
        uid: str,
        company_id: str,
        space_code: str,
        chat_mode: str = "general_chat",
        thread_name: Optional[str] = None,
        mode: str = "chats",
        thread_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        CHAT.session_create - Create a new chat session.

        Args:
            uid: User ID
            company_id: Company ID
            space_code: Firebase space code
            chat_mode: Chat mode (general_chat, onboarding_chat, etc.)
            thread_name: Optional initial name (will be auto-generated if not provided)
            mode: Storage mode ("chats" or "active_chats")
            thread_key: Optional explicit thread key (e.g., job_id for job-monitoring)

        Returns:
            {"success": True, "session": {...}}
        """
        try:
            from app.firebase_providers import get_firebase_realtime
            import uuid
            from datetime import datetime, timezone

            # Generate thread key if not provided
            if not thread_key:
                thread_key = f"{int(datetime.now().timestamp())}_{space_code}_{uuid.uuid4().hex[:8]}"

            # Default name if not provided
            if not thread_name:
                thread_name = f"Chat {datetime.now().strftime('%H:%M')}"

            realtime_service = get_firebase_realtime()
            result = realtime_service.create_chat(
                user_id=uid,
                space_code=space_code,
                thread_name=thread_name,
                mode=mode,
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

        Cleans up:
        - RTDB: primary channel ({mode}/{thread_key})
        - RTDB: job_chats/{thread_key} (if mode is active_chats)
        - Redis: chat history cache (chat:{uid}:{company_id}:{thread_key}:history)

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

            # Always clean job_chats/{thread_key} (onboarding + job-monitoring chats
            # both write to job_chats even though their session mode differs)
            if realtime_service.delete_chat(
                space_code=space_code,
                thread_key=thread_key,
                mode="job_chats"
            ):
                logger.info(f"[CHAT] Also deleted job_chats/{thread_key}")

            # Clean Redis chat history cache
            try:
                r = get_redis()
                redis_key = f"chat:{uid}:{company_id}:{thread_key}:history"
                r.delete(redis_key)
                logger.debug(f"[CHAT] Redis chat history deleted: {redis_key}")
            except Exception as redis_err:
                logger.warning(f"[CHAT] Failed to delete Redis chat history: {redis_err}")

            # Clean GCS chat files (non-blocking)
            try:
                from app.storage_client import get_storage_client
                storage = get_storage_client()
                gcs_result = storage.delete_path(f"chat_files/{thread_key}/", recursive=True)
                if gcs_result.get("deleted_count", 0) > 0:
                    logger.info(f"[CHAT] GCS files deleted: chat_files/{thread_key}/ count={gcs_result['deleted_count']}")
            except Exception as gcs_err:
                logger.warning(f"[CHAT] GCS cleanup failed (non-blocking): {gcs_err}")

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

    async def auto_name_session_llm(
        self,
        uid: str,
        company_id: str,
        space_code: str,
        thread_key: str,
        first_message: str,
        mode: str = "chats",
    ) -> Dict[str, Any]:
        """
        CHAT.session_auto_name_llm - Auto-generate a name using LLM.

        Uses Claude (Anthropic) to generate a descriptive, concise title
        based on the first message content. Falls back to heuristic if LLM fails.

        Args:
            uid: User ID
            company_id: Company ID
            space_code: Firebase space code
            thread_key: Thread key to rename
            first_message: The first message content to generate name from
            mode: Firebase mode

        Returns:
            {"success": True, "new_name": str, "thread_key": str, "method": "llm"|"heuristic"}
        """
        try:
            # Try LLM-based naming first
            generated_name = await self._generate_chat_name_llm(first_message)
            method = "llm"

            # Fallback to heuristic if LLM failed
            if not generated_name:
                generated_name = self._generate_chat_name(first_message)
                method = "heuristic"

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
                logger.info(f"[CHAT] Auto-named session {thread_key} to '{generated_name}' via {method}")
                return {
                    "success": True,
                    "thread_key": thread_key,
                    "new_name": generated_name,
                    "method": method
                }
            else:
                return result

        except Exception as e:
            logger.error(f"[CHAT] Error in LLM auto-naming: {e}")
            # Fallback to heuristic on any error
            try:
                generated_name = self._generate_chat_name(first_message)
                result = await self.rename_session(
                    uid=uid,
                    company_id=company_id,
                    space_code=space_code,
                    thread_key=thread_key,
                    new_name=generated_name,
                    mode=mode
                )
                if result.get("success"):
                    return {
                        "success": True,
                        "thread_key": thread_key,
                        "new_name": generated_name,
                        "method": "heuristic_fallback"
                    }
                return result
            except Exception as fallback_error:
                logger.error(f"[CHAT] Fallback naming also failed: {fallback_error}")
                return {"success": False, "error": str(fallback_error)}

    async def _generate_chat_name_llm(self, first_message: str, max_length: int = 50) -> Optional[str]:
        """
        Generate a chat name using LLM (Anthropic Claude).

        Uses a tool-based approach to get structured output.

        Args:
            first_message: The first user message
            max_length: Maximum name length

        Returns:
            Generated chat name or None if failed
        """
        import asyncio

        if not first_message or not first_message.strip():
            return None

        try:
            from app.llm.klk_agents import BaseAIAgent, ModelProvider, ModelSize, NEW_MOONSHOT_AIAgent

            # Create a lightweight agent instance for naming with Moonshot provider
            naming_agent = BaseAIAgent()
            moonshot_instance = NEW_MOONSHOT_AIAgent()
            naming_agent.register_provider(ModelProvider.MOONSHOT_AI, moonshot_instance, ModelSize.MEDIUM)
            naming_agent.default_provider = ModelProvider.MOONSHOT_AI

            # Define the tool for structured output
            chat_title_tool = {
                "name": "generate_chat_title",
                "description": "Generates a short, descriptive title for a conversation based on the user's first message",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Short title (max 50 chars), descriptive, in the same language as the user message. Avoid vague words like 'Question', 'Request'. Be specific and informative."
                        },
                    },
                    "required": ["title"]
                }
            }

            # Tool choice to force using the tool
            tool_choice = {'type': 'tool', 'name': 'generate_chat_title'}

            # Simple tool mapping (returns the title directly)
            def extract_title(title: str) -> str:
                return title

            tool_mapping = {
                "generate_chat_title": extract_title
            }

            # Run in thread to avoid blocking
            def generate():
                try:
                    response = naming_agent.process_tool_use(
                        content=f"Generate a title for this conversation based on this first user message: {first_message}",
                        tools=[chat_title_tool],
                        tool_mapping=tool_mapping,
                        tool_choice=tool_choice,
                        size=ModelSize.SMALL  # Use small model for efficiency
                    )
                    return response
                except Exception as e:
                    logger.warning(f"[CHAT] LLM title generation failed: {e}")
                    return None

            title = await asyncio.to_thread(generate)

            # Validate and clean the title
            if title and isinstance(title, str):
                title = title.replace('"', '').replace("'", "").strip()

                # Limit length
                if len(title) > max_length:
                    title = title[:max_length - 3] + "..."

                # Check for empty or generic titles
                if not title or title.lower() in ['nouveau chat', 'new chat', 'chat', 'untitled']:
                    return None

                return title

            return None

        except ImportError as e:
            logger.warning(f"[CHAT] Could not import LLM agent: {e}")
            return None
        except Exception as e:
            logger.warning(f"[CHAT] LLM naming error: {e}")
            return None

    def _generate_chat_name(self, first_message: str, max_length: int = 50) -> str:
        """
        Generate a chat name from the first message (heuristic fallback).

        Uses a heuristic approach:
        1. Clean and truncate the message
        2. Remove common prefixes (Bonjour, Hello, etc.)
        3. Capitalize first letter

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
        chat_mode: str = "",
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
            chat_mode: Chat mode (e.g. "onboarding_chat") — used to load extra data

        Returns:
            {"success": True, "messages": [...], "total": int}
        """
        try:
            # 1. Check cache for RAW messages (keyed by thread_key only - unique across compartments)
            cached = await self._cache_manager.get_cached_data(
                user_id=uid,
                company_id=company_id,
                data_type="chat:history:raw",
                sub_type=thread_key
            )

            raw_messages = None
            from_cache = False
            actual_mode = mode  # Track which mode actually has the data

            if cached:
                logger.info(f"[CHAT] Cache hit for history: {thread_key}")
                raw_messages = cached.get("data", cached) if isinstance(cached, dict) else cached
                from_cache = True

            # 2. Fetch from Firebase Realtime if not cached
            if raw_messages is None:
                from app.firebase_providers import get_firebase_realtime

                realtime_service = get_firebase_realtime()
                raw_messages = realtime_service.get_thread_messages(
                    space_code=space_code,
                    thread_key=thread_key,
                    mode=mode,
                    limit=limit
                )

                # Fallback: if no messages found in requested mode, try the other compartment
                # This handles cases where frontend sends wrong mode (e.g., page refresh without state)
                if not raw_messages:
                    alternate_mode = "active_chats" if mode == "chats" else "chats"
                    logger.info(f"[CHAT] No messages in '{mode}', trying '{alternate_mode}' for {thread_key}")
                    raw_messages = realtime_service.get_thread_messages(
                        space_code=space_code,
                        thread_key=thread_key,
                        mode=alternate_mode,
                        limit=limit
                    )
                    if raw_messages:
                        actual_mode = alternate_mode
                        logger.info(f"[CHAT] Found {len(raw_messages)} messages in '{alternate_mode}' (fallback)")

                if raw_messages is None:
                    raw_messages = []

                # Only cache if we found messages (avoid caching empty results from wrong mode)
                if raw_messages:
                    await self._cache_manager.set_cached_data(
                        user_id=uid,
                        company_id=company_id,
                        data_type="chat:history:raw",
                        sub_type=thread_key,
                        data=raw_messages,
                        ttl_seconds=TTL_HISTORY
                    )

            # 3. Transform messages to standard format (always, even from cache)
            formatted_messages = self._transform_messages(raw_messages)

            # 4. Extract pending card (always recalculate to ensure freshness)
            # Pass thread_key so the card knows which chat it belongs to
            pending_card = self._extract_pending_card(raw_messages, thread_key)

            # 5. Pour les modes job chat, charger aussi job_chats pour :
            #    - Workflow checklist (onboarding)
            #    - Pending cards des workers externes (router, AP, bank)
            workflow_checklist = None
            _JOB_CHAT_MODES = {"onboarding_chat", "router_chat", "apbookeeper_chat", "banker_chat"}
            # Normaliser le chat_mode (certains modes ont un point final parasite)
            normalized_chat_mode = (chat_mode or "").rstrip(".")

            if normalized_chat_mode in _JOB_CHAT_MODES:
                job_messages = None
                try:
                    from app.firebase_providers import get_firebase_realtime
                    realtime_service = get_firebase_realtime()
                    job_messages = realtime_service.get_thread_messages(
                        space_code=space_code,
                        thread_key=thread_key,
                        mode="job_chats",
                        limit=500
                    )
                except Exception as jc_err:
                    logger.warning(f"[CHAT] Error loading job_chats: {jc_err}")

                if job_messages:
                    # 5a. Workflow checklist (onboarding_chat only)
                    if normalized_chat_mode == "onboarding_chat":
                        try:
                            workflow_checklist = self._extract_workflow_checklist(job_messages)
                            if workflow_checklist:
                                logger.info(f"[CHAT] Restored workflow checklist for {thread_key}: {len(workflow_checklist.get('steps', []))} steps")
                        except Exception as wf_err:
                            logger.warning(f"[CHAT] Error extracting workflow checklist: {wf_err}")

                    # 5b. Pending card des workers externes dans job_chats
                    if not pending_card:
                        pending_card = self._extract_pending_card(job_messages, thread_key)
                        if pending_card:
                            logger.info(f"[CHAT] Pending card found in job_chats for {thread_key}: {pending_card.get('cardId', 'unknown')}")

            logger.info(f"[CHAT] Loaded {len(formatted_messages)} messages for {thread_key}, pending_card={pending_card is not None}, workflow_checklist={workflow_checklist is not None}, from_cache={from_cache}")
            return {
                "success": True,
                "messages": formatted_messages,
                "total": len(formatted_messages),
                "pending_card": pending_card,
                "workflow_checklist": workflow_checklist,
                "from_cache": from_cache,
            }

        except Exception as e:
            logger.error(f"[CHAT] Error loading history: {e}")
            return {"success": False, "error": str(e), "messages": [], "total": 0}

    def _extract_workflow_checklist(self, raw_messages: List[Dict]) -> Optional[Dict[str, Any]]:
        """
        Replay CMMD messages to reconstruct the workflow checklist state.

        Scans for SET_WORKFLOW_CHECKLIST (initial definition) then applies
        UPDATE_STEP_STATUS commands in order to get current step states.

        Returns:
            {"totalSteps": int, "steps": [{"id", "name", "status", "message"}]} or None
        """
        import json

        checklist = None  # Will be set by SET_WORKFLOW_CHECKLIST

        for msg in raw_messages:
            if msg.get("message_type") != "CMMD":
                continue

            content_raw = msg.get("content", "")
            try:
                if isinstance(content_raw, str):
                    content = json.loads(content_raw)
                else:
                    content = content_raw
            except (json.JSONDecodeError, TypeError):
                continue

            # content may be the CMMD payload directly or nested in "message"
            action = content.get("action", "")
            params = content.get("params", {})

            if action == "SET_WORKFLOW_CHECKLIST":
                raw_checklist = params.get("checklist", {})
                steps = raw_checklist.get("steps", [])
                checklist = {
                    "totalSteps": raw_checklist.get("total_steps", len(steps)),
                    "steps": [
                        {
                            "id": s.get("id", ""),
                            "name": s.get("name", ""),
                            "status": s.get("status", "pending"),
                            "message": s.get("message"),
                        }
                        for s in steps
                    ],
                }

            elif action == "UPDATE_STEP_STATUS" and checklist is not None:
                step_id = params.get("step_id", "")
                new_status = params.get("status", "")
                new_message = params.get("message")
                for step in checklist["steps"]:
                    if step["id"] == step_id:
                        step["status"] = new_status
                        if new_message is not None:
                            step["message"] = new_message
                        break

        return checklist

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
    # PENDING CARD DETECTION
    # ──────────────────────────────────────────

    def _extract_pending_card(self, raw_messages: List[Dict], thread_key: str) -> Optional[Dict[str, Any]]:
        """
        Extract the last pending interactive card from message history.

        RULES:
        1. Worker LLM cards: status == "pending_approval" (explicit field set by worker)
        2. External worker cards: message_type FOLLOW_CARD/CARD with pinnokio_card_status == "pending_approval"
           OR FOLLOW_CARD/CARD without status but no subsequent CARD_CLICKED_PINNOKIO

        Supported card types (worker LLM + external workers):
        - text_modification_approval, task_creation_approval, approval_card, four_eyes_approval_card
        - klk_router_card, klk_router_approval_card, job_menu_card, bank_list_file_card, bank_file_list_card

        Args:
            raw_messages: Raw messages from Firebase (before transformation)
            thread_key: The chat thread key (required for card responses)

        Returns:
            Card data dict if a pending card is found, None otherwise
        """
        import json

        SUPPORTED_CARD_TYPES = {
            'text_modification_approval',
            'task_creation_approval',
            'approval_card',
            'four_eyes_approval_card',
            'klk_router_card',
            'klk_router_approval_card',
            'job_menu_card',
            'bank_list_file_card',
            'bank_file_list_card',
        }

        # External worker card IDs — source unique: card_transformer.py
        from app.realtime.card_transformer import EXTERNAL_WORKER_CARD_IDS

        last_pending_card: Optional[Dict[str, Any]] = None

        logger.debug(f"[CHAT] Scanning {len(raw_messages)} messages for pending cards")

        for msg in raw_messages:
            # Check if this is a card message
            message_type = msg.get("message_type", "")
            msg_type = msg.get("type", "")
            card_type_field = msg.get("card_type", "")

            is_card_message = (
                message_type in ("CARD", "FOLLOW_CARD") or
                msg_type in ("CARD", "FOLLOW_CARD") or
                bool(card_type_field)
            )

            if not is_card_message:
                continue

            # Check status fields (worker LLM sets "pending_approval", worker external uses pinnokio_card_status)
            card_status = msg.get("status", "")
            pinnokio_status = msg.get("pinnokio_card_status", "")

            # Rule 1: Explicit pending_approval status (worker LLM or enriched external)
            is_pending_by_status = (
                card_status == "pending_approval" or
                pinnokio_status == "pending_approval"
            )

            # Rule 2: External worker card (FOLLOW_CARD) without explicit status
            # → check if no CARD_CLICKED_PINNOKIO after this message
            is_external_follow_card = (
                message_type in ("FOLLOW_CARD", "CARD") and
                not card_status and
                not pinnokio_status
            )

            if not is_pending_by_status and not is_external_follow_card:
                logger.debug(f"[CHAT] Skipping card with status={card_status}")
                continue

            # For external follow cards, verify no CARD_CLICKED_PINNOKIO exists after
            if is_external_follow_card:
                if self._has_card_clicked_after(raw_messages, msg):
                    logger.debug(f"[CHAT] Skipping external card (already clicked)")
                    continue

            try:
                content_raw = msg.get("content", "{}")
                if isinstance(content_raw, str):
                    content = json.loads(content_raw)
                else:
                    content = content_raw

                card_id = None
                card_type = None
                card_params = {}
                widgets = None

                # Format 1: Standard format with cardsV2
                if "cardsV2" in content:
                    cards_v2 = content.get("cardsV2", [])
                    if cards_v2 and len(cards_v2) > 0:
                        card_id = cards_v2[0].get("cardId")
                    card_params = content.get("message", {}).get("cardParams", {})
                    card_type = content.get("message", {}).get("cardType", card_id)

                    # External worker cards: extract widgets from cardsV2 sections
                    if card_id in EXTERNAL_WORKER_CARD_IDS and not card_params:
                        card_body = cards_v2[0].get("card", {}) if cards_v2 else {}
                        widgets = self._extract_widgets_from_cardsv2(card_body)
                        header = card_body.get("header", {})
                        card_params = {
                            "title": header.get("title", "Carte Interactive"),
                            "subtitle": header.get("subtitle", ""),
                        }

                # Format 2: cardParams in message
                elif "message" in content:
                    card_params = content.get("message", {}).get("cardParams", {})
                    card_id = card_params.get("cardId")
                    card_type = content.get("message", {}).get("cardType", card_id)

                # Format 3: Alternative format with card_type at message level
                if not card_type and card_type_field:
                    card_type = card_type_field
                    card_id = card_type
                    if isinstance(content, dict):
                        if "cardParams" in content:
                            card_params = content.get("cardParams", {})
                        elif "message" in content and "cardParams" in content.get("message", {}):
                            card_params = content.get("message", {}).get("cardParams", {})
                        elif "title" in content or "original_text" in content:
                            card_params = content

                # Check if it's a supported card type
                if card_type in SUPPORTED_CARD_TYPES or card_id in SUPPORTED_CARD_TYPES:
                    message_id = msg.get("id") or msg.get("message_id") or msg.get("name")

                    logger.info(
                        f"[CHAT] Found pending card: id={card_id}, type={card_type}, "
                        f"status={card_status or pinnokio_status or 'implicit'}, message_id={message_id}"
                    )

                    last_pending_card = {
                        "cardId": card_id,
                        "cardType": card_type or card_id,
                        "title": card_params.get("title", ""),
                        "subtitle": card_params.get("subtitle"),
                        "text": card_params.get("text"),
                        "params": card_params,
                        "isVisible": True,
                        "threadKey": thread_key,
                        "messageId": message_id,
                        # Specific fields for text_modification_approval
                        "originalText": card_params.get("original_text"),
                        "finalText": card_params.get("final_text"),
                        "operationsSummary": card_params.get("operations_summary"),
                        "contextName": card_params.get("context_name"),
                        # Specific fields for task_creation_approval
                        "taskId": card_params.get("task_id"),
                        "executionPlan": card_params.get("execution_plan"),
                        "missionTitle": card_params.get("mission_title"),
                        "missionDescription": card_params.get("mission_description"),
                    }

                    # Add widgets for external worker cards
                    if widgets:
                        last_pending_card["widgets"] = widgets

            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"[CHAT] Error parsing CARD message: {e}")

        if last_pending_card:
            logger.info(f"[CHAT] Pending card detected: {last_pending_card.get('cardId')}")

        return last_pending_card

    def _has_card_clicked_after(self, raw_messages: List[Dict], card_msg: Dict) -> bool:
        """Check if a CARD_CLICKED_PINNOKIO message exists after the given card message."""
        card_ts = card_msg.get("timestamp", "")

        for msg in raw_messages:
            msg_type = msg.get("message_type", "")
            if msg_type != "CARD_CLICKED_PINNOKIO":
                continue

            msg_ts = msg.get("timestamp", "")
            # Compare timestamps (ISO format strings are lexicographically ordered)
            if msg_ts > card_ts:
                return True

        return False

    def _extract_widgets_from_cardsv2(self, card_body: Dict) -> List[Dict[str, Any]]:
        """Extract normalized widgets from a cardsV2 card body for frontend rendering."""
        from app.realtime.card_transformer import CardTransformer
        return CardTransformer.extract_widgets_from_card_body(card_body)

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


    # ──────────────────────────────────────────
    # ONBOARDING CHAT
    # ──────────────────────────────────────────

    async def start_onboarding_chat(
        self,
        uid: str,
        company_id: str,
        thread_key: str,
    ) -> Dict[str, Any]:
        """
        CHAT.start_onboarding - Start onboarding chat session.

        Triggered after company creation when user lands on /chat/{thread_key}?action=create.
        Enqueues the onboarding job to the worker via LLMGateway.

        ⚠️ ARCHITECTURE: Onboarding chat via Redis Queue
        1. Enqueue start_onboarding_chat job -> Worker creates brain
        2. Worker loads onboarding data and launches LPT job
        3. Worker publishes result via Redis PubSub -> Frontend receives confirmation

        Args:
            uid: User ID
            company_id: Company ID (contact_space_id)
            thread_key: Thread key (job_id from onboarding)

        Returns:
            {"success": True, "thread_key": str, "message": str, "status": "queued"}
        """
        try:
            from app.llm_service.llm_gateway import get_llm_gateway

            logger.info(f"[CHAT] start_onboarding_chat - uid={uid} company={company_id} thread={thread_key}")

            gateway = get_llm_gateway()
            queue_result = await gateway.enqueue_onboarding_chat(
                user_id=uid,
                collection_name=company_id,
                thread_key=thread_key,
                chat_mode="onboarding_chat"
            )

            logger.info(
                f"[CHAT] Onboarding chat enqueued: job_id={queue_result.get('job_id', 'unknown')[:8]}... "
                f"thread={thread_key}"
            )

            return {
                "success": True,
                "thread_key": thread_key,
                "message": "Onboarding chat enqueued",
                "status": "queued",
                "job_id": queue_result.get("job_id"),
            }

        except Exception as e:
            logger.error(f"[CHAT] Error starting onboarding chat: {e}")
            return {"success": False, "error": str(e)}


# ============================================
# EXPORTS
# ============================================

__all__ = [
    "ChatHandlers",
    "get_chat_handlers",
]
