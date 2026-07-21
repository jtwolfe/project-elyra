"""Speak transport — owns glass delivery of assistant rows.

Public API: SpeakTransport, SpeakDelivery.
tools/builtin/social.speak is a thin wrapper; do not write glass elsewhere.
"""

from elyra.speak.transport import SpeakDelivery, SpeakTransport

__all__ = [
    "SpeakDelivery",
    "SpeakTransport",
]
