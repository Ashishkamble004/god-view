"""Incidents management module (`godview.sovereign.incidents`)."""

from __future__ import annotations

from godview.sovereign.incidents.manager import IncidentManager
from godview.sovereign.incidents.models import IncidentDoc, IncidentOutcome

__all__ = ["IncidentDoc", "IncidentManager", "IncidentOutcome"]
