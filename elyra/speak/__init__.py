"""Speak transport — owns glass delivery of assistant rows.

Public API: SpeakTransport, SpeakDelivery, normalize_speak_user_id.
tools/builtin/social.speak is a thin wrapper; do not write glass elsewhere.
"""

from elyra.speak.transport import (
    SpeakDelivery,
    SpeakTransport,
    normalize_speak_user_id,
)

__all__ = [
    "SpeakDelivery",
    "SpeakTransport",
    "normalize_speak_user_id",
]
