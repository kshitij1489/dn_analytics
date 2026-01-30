"""
AI Mode entry point for the API layer.

Core logic lives in the ai_mode package. This module re-exports process_chat
so existing imports (e.g. from src.api.routers.ai) continue to work.
"""

from ai_mode import process_chat

__all__ = ["process_chat"]
