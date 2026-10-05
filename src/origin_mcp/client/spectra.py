from __future__ import annotations

from typing import Any

from ..spectra.models import Dataset
from ..spectra.rendering import render_spectra
from ..spectra.runtime import RuntimeOptions
from .base import _OriginClientBase


class _SpectraMixin(_OriginClientBase):
    def plot_spectra(self, datasets: list[dict[str, Any]], options: dict[str, Any]):
        """Draw a validated XPS/XRD batch in the existing Origin session."""
        parsed = [Dataset.from_dict(value) for value in datasets]
        return render_spectra(self, parsed, RuntimeOptions.from_dict(options))
