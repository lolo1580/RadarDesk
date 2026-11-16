"""Paquet backend RadarDesk.

Le dossier backend est ajouté au chemin d'import pour que les modules internes
(adsb_models, adsb_utils, stats_store, …) restent importables aussi bien via
``python server.py`` que via ``import backend.server`` dans les tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
