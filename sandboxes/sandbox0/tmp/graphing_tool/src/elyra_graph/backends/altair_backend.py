"""Altair backend stub (phase 2)."""
from ..errors import GraphingError

class AltairBackend:
    name = "altair"
    def render(self, request):
        raise GraphingError(
            "E_BACKEND",
            "altair backend not implemented yet",
            hint="use backend='mpl'",
        )
