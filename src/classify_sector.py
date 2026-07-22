"""
Classifies contracts into sectors based on description keywords and awardee context.
"""

from __future__ import annotations
import re
from src.models import Sector, Contract
from config import SECTOR_KEYWORDS


def _kw_hit(kw: str, text: str) -> bool:
    """Word-boundary keyword match — prevents 'engine' from matching 'engineering'."""
    pattern = r"\b" + re.escape(kw.lower()) + r"\b"
    return bool(re.search(pattern, text))


def classify_sector(contract: Contract) -> Sector:
    """
    Score each sector by keyword hits in description + keywords list.
    Returns the highest-scoring sector.
    """
    search_text = " ".join([
        contract.description.lower(),
        " ".join(contract.keywords).lower(),
        (contract.awardee_name or "").lower(),
        (contract.agency or "").lower(),
    ])

    scores: dict[str, int] = {}
    for sector_name, keywords in SECTOR_KEYWORDS.items():
        hits = sum(1 for kw in keywords if _kw_hit(kw, search_text))
        if hits:
            scores[sector_name] = hits

    if not scores:
        # Try awardee-based heuristics. Uses the same word-boundary matching as
        # the primary keyword pass above — plain substring checks here would
        # misclassify e.g. "Township Solutions Inc" as Shipbuilding ("ship" is
        # a substring of "township") or "Cybernetics Analytics LLC" as
        # Cybersecurity ("cyber" is a substring of "cybernetics").
        awardee = contract.awardee_name.lower()
        if any(_kw_hit(w, awardee) for w in ["health", "medical", "pharma", "hospital", "clinical"]):
            return Sector.MILITARY_HEALTHCARE
        if any(_kw_hit(w, awardee) for w in ["ship", "marine", "naval"]):
            return Sector.SHIPBUILDING
        if any(_kw_hit(w, awardee) for w in ["cyber", "security solutions"]):
            return Sector.CYBERSECURITY
        return Sector.UNCLEAR

    best = max(scores, key=lambda k: scores[k])

    # Map display name to Sector enum
    _MAP = {
        "Shipbuilding": Sector.SHIPBUILDING,
        "Aerospace": Sector.AEROSPACE,
        "Space": Sector.SPACE,
        "Cybersecurity": Sector.CYBERSECURITY,
        "AI / Data / Software": Sector.AI_DATA_SOFTWARE,
        "Cloud / IT Services": Sector.CLOUD_IT_SERVICES,
        "Military Healthcare": Sector.MILITARY_HEALTHCARE,
        "Pharmaceutical / Biotech": Sector.PHARMACEUTICAL_BIOTECH,
        "Medical Devices": Sector.MEDICAL_DEVICES,
        "Logistics": Sector.LOGISTICS,
        "Energy / Nuclear": Sector.ENERGY_NUCLEAR,
        "Infrastructure / Construction": Sector.INFRASTRUCTURE_CONSTRUCTION,
        "Traditional Defense Prime": Sector.TRADITIONAL_DEFENSE_PRIME,
        "Industrial Components": Sector.INDUSTRIAL_COMPONENTS,
        "Consulting / Services": Sector.CONSULTING_SERVICES,  # was missing — fell through to UNCLEAR
    }
    return _MAP.get(best, Sector.UNCLEAR)
