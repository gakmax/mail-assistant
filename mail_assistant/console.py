"""Console output that survives a redirected pipe.

Windows hands a redirected stdout the ANSI codepage, not UTF-8, so every
Korean print() in the diagnostic scripts raises UnicodeEncodeError the first
time someone runs `MailAssistantTools.exe diagnose x@y.com > log.txt` -- and
that redirect is exactly what you ask for when something has gone wrong.
"""
import sys


def use_utf8():
    """Idempotent, and a no-op wherever the streams cannot be reconfigured."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
