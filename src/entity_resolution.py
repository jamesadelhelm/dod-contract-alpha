"""
Entity resolution: maps raw awardee names to public tickers via ticker_map.yaml.
Includes fuzzy matching for common variations.
"""

from __future__ import annotations
import re
import yaml
from pathlib import Path
from typing import Optional, Tuple
from config import TICKER_MAP_PATH


def _load_ticker_map(path: Path = TICKER_MAP_PATH) -> dict:
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    # Normalize all keys to lowercase stripped
    return {k.lower().strip(): v for k, v in raw.items()}


_TICKER_MAP: dict = {}


def _get_map() -> dict:
    global _TICKER_MAP
    if not _TICKER_MAP:
        _TICKER_MAP = _load_ticker_map()
    return _TICKER_MAP


def _normalize(name: str) -> str:
    """Normalize a company name for lookup."""
    name = name.lower().strip()
    # Remove common suffixes
    suffixes = [
        r",\s*(inc\.?|llc\.?|corp\.?|ltd\.?|co\.?|incorporated|corporation|limited|l\.l\.c\.?)$",
        r"\s+(inc\.?|llc\.?|corp\.?|ltd\.?|co\.?)$",
    ]
    for suffix in suffixes:
        name = re.sub(suffix, "", name, flags=re.IGNORECASE).strip()
    return name.strip()


def _candidate_keys(name: str) -> list[str]:
    """Generate lookup candidates from a company name."""
    candidates = []
    candidates.append(name.lower().strip())
    normalized = _normalize(name)
    candidates.append(normalized)
    # Drop trailing state abbreviations like ", Groton, Connecticut" artifacts
    if "," in name:
        base = name.split(",")[0].strip().lower()
        candidates.append(base)
        candidates.append(_normalize(base))
    return list(dict.fromkeys(candidates))  # dedup preserving order


def resolve_ticker(awardee_name: str) -> Tuple[Optional[str], Optional[str], float]:
    """
    Resolve an awardee name to (ticker, parent_company, confidence).
    Returns (None, None, 0.0) if no match found.
    """
    tmap = _get_map()
    candidates = _candidate_keys(awardee_name)

    for candidate in candidates:
        if candidate in tmap:
            entry = tmap[candidate]
            ticker = entry.get("ticker")
            parent = entry.get("parent")
            confidence = float(entry.get("confidence", 0.8))
            if ticker == "null" or ticker is None:
                # Private company — return parent for reporting
                return None, parent, confidence
            return ticker, parent, confidence

    # Fuzzy: check if any map key is a substring of the awardee or vice versa
    norm_awardee = _normalize(awardee_name).lower()
    best_match = None
    best_conf = 0.0
    best_entry = None

    for key, entry in tmap.items():
        if len(key) < 4:
            continue
        # substring match (key appears in awardee)
        if key in norm_awardee or norm_awardee in key:
            sim = len(key) / max(len(key), len(norm_awardee))
            if sim > best_conf:
                best_conf = sim
                best_match = key
                best_entry = entry

    if best_entry and best_conf >= 0.5:
        ticker = best_entry.get("ticker")
        parent = best_entry.get("parent")
        confidence = float(best_entry.get("confidence", 0.6)) * best_conf
        if ticker == "null" or ticker is None:
            return None, parent, confidence
        return ticker, parent, confidence

    # ── EDGAR fallback ────────────────────────────────────────────────────────
    # If ticker_map has no entry, try the live SEC company index.
    # Results are cached locally so subsequent runs are instant.
    try:
        from src.edgar_company_lookup import lookup_ticker_from_edgar
        ticker, company, confidence = lookup_ticker_from_edgar(awardee_name)
        if ticker:
            return ticker, company, confidence
    except Exception:
        pass

    return None, None, 0.0


def reload_ticker_map():
    """Force reload of the ticker map (e.g. after edits)."""
    global _TICKER_MAP
    _TICKER_MAP = _load_ticker_map()
