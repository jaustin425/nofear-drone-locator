"""Serialize decoder stdout/stderr suppression across concurrent input workers."""
import threading

DECODE_LOCK = threading.RLock()
