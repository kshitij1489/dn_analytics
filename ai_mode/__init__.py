"""
AI Mode: core logic for the analytics AI assistant.

Entry point: process_chat() — orchestrates intent classification, SQL/chart/general chat.
"""

from ai_mode.orchestrator import process_chat

__all__ = ["process_chat"]
