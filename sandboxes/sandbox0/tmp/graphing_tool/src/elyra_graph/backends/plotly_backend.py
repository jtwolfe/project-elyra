"""Plotly backend stub (phase 2)."""
from ..errors import GraphingError

class PlotlyBackend:
    name = "plotly"
    def render(self, request):
        raise GraphingError(
            "E_BACKEND",
            "plotly backend not implemented yet",
            hint="use backend='mpl'",
        )
