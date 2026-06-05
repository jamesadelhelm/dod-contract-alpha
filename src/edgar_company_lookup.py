"""
Automatic ticker resolution via SEC EDGAR company index.

Downloads company_tickers.json from the SEC (all ~10k+ US-listed public companies),
caches it locally (refreshed weekly), then fuzzy-matches awardee names against it.

Every result — hit or miss — is cached in data/resolved_cache.json so the same
awardee name is never looked up twice across runs.

Scoring approach:
  c_recall  (0.5 weight) — fraction of company-name tokens found in the awardee.
            Handles subsidiary names: "T-Mobile Secure Federal Operations" still
            contains "t-mobile", covering all of the short public-company name.
  jaccard   (0.3 weight) — balanced token overlap.
  seq_ratio (0.2 weight) — character-level SequenceMatcher, tiebreaker.

Minimum confidence to accept a match: 0.72 (tunable via MIN_EDGAR_CONFIDENCE).
"""

from __future__ import annotations

import difflib
import json
import re
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

import requests

# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
INDEX_PATH = _ROOT / "data" / "edgar_company_index.json"
RESOLVE_CACHE_PATH = _ROOT / "data" / "resolved_cache.json"

# ── Config ────────────────────────────────────────────────────────────────────
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
HEADERS = {
    "User-Agent": "dod-contract-research-agent/1.0 (non-commercial; contact james.robert.adelhelm@gmail.com)",
    "Accept-Encoding": "gzip, deflate",
}
CACHE_TTL_DAYS = 7
MIN_EDGAR_CONFIDENCE = 0.72
# Company compact-name must be at least this many chars to avoid matching "ge", "3m" too broadly
MIN_COMPANY_CHARS = 4
# If the top two candidates are within this margin and both below 0.85, flag as ambiguous
AMBIGUITY_MARGIN = 0.10

# ── Normalization ─────────────────────────────────────────────────────────────
_LEGAL_RE = re.compile(
    r"\b("
    r"incorporated|corporation|limited|company|group|holdings|enterprises|"
    r"inc|llc|corp|ltd|co|lp|plc|ag|sa|nv|bv|pbc|pty|gmbh"
    r")\.?\b",
    re.IGNORECASE,
)

# Filler words stripped in "compact" normalization (beyond legal suffixes).
# Keep geographic/directional words (north, american, etc.) — they help distinguish
# e.g. "North American Construction" from "S.J. Amoroso Construction".
_NOISE = {"the", "a", "an", "of", "and", "for", "us", "usa"}

# Generic industry tokens that alone don't identify any specific company.
# If the ONLY common tokens between awardee and candidate are all in this set,
# the match is too ambiguous to trust.
_GENERIC_TOKENS = {
    "construction", "engineering", "technologies", "technology", "services",
    "solutions", "systems", "industries", "resources", "energy", "healthcare",
    "communications", "consulting", "defense", "security", "management",
    "international", "national", "global", "federal", "government",
    "environmental", "technical", "scientific", "associates", "research",
    "analytics", "networks", "partners", "contractors", "enterprises",
    "infrastructure", "industrial", "logistics", "operations", "support",
}


def _normalize(name: str) -> str:
    """Lowercase, remove punctuation, strip legal suffixes, collapse whitespace."""
    name = name.lower()
    name = re.sub(r"[^\w\s]", " ", name)
    name = _LEGAL_RE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _compact(name: str) -> str:
    """Normalize then strip noise words.  Used for the actual matching."""
    tokens = _normalize(name).split()
    tokens = [t for t in tokens if t not in _NOISE and len(t) > 1]
    return " ".join(tokens)


# ── EDGAR index ───────────────────────────────────────────────────────────────

def _is_stale(path: Path) -> bool:
    if not path.exists():
        return True
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age > timedelta(days=CACHE_TTL_DAYS)


def _download_and_build_index() -> list[dict]:
    resp = requests.get(EDGAR_TICKERS_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    raw = resp.json()

    entries = []
    for item in raw.values():
        ticker = (item.get("ticker") or "").strip().upper()
        title  = (item.get("title")  or "").strip()
        if not ticker or not title:
            continue
        norm    = _normalize(title)
        compact = _compact(title)
        if len(compact) < MIN_COMPANY_CHARS:
            continue
        entries.append({"ticker": ticker, "title": title, "norm": norm, "compact": compact})

    return entries


def _load_index() -> list[dict]:
    if _is_stale(INDEX_PATH):
        print("[EDGAR] Refreshing SEC company index...", flush=True)
        entries = _download_and_build_index()
        INDEX_PATH.write_text(json.dumps(entries, separators=(",", ":")))
        print(f"[EDGAR] Indexed {len(entries)} public companies → {INDEX_PATH.name}")
    else:
        entries = json.loads(INDEX_PATH.read_text())
    return entries


_INDEX: Optional[list[dict]] = None


def _get_index() -> list[dict]:
    global _INDEX
    if _INDEX is None:
        _INDEX = _load_index()
    return _INDEX


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score(awardee_compact: str, company: dict) -> float:
    """
    Score how well an awardee name matches a public company entry.

    We weight c_recall (company tokens found in awardee) most heavily because
    subsidiary/division names often contain the parent brand plus extra words.
    E.g. "Tetra Tech EC Inc" contains all tokens of "Tetra Tech Inc".
    """
    c_compact = company["compact"]
    a_tokens = set(awardee_compact.split())
    c_tokens = set(c_compact.split())

    if not a_tokens or not c_tokens:
        return 0.0

    common = a_tokens & c_tokens
    if not common:
        return 0.0

    # Require at least one common token of meaningful length
    if max(len(t) for t in common) < 3:
        return 0.0

    # If every common token is a generic industry word (e.g. only "construction"
    # links "S.J. Amoroso" to "North American Construction Group"), reject.
    if common <= _GENERIC_TOKENS:
        return 0.0

    c_recall = len(common) / len(c_tokens)          # company coverage in awardee
    jaccard  = len(common) / len(a_tokens | c_tokens)
    seq      = difflib.SequenceMatcher(None, awardee_compact, c_compact).ratio()

    return 0.5 * c_recall + 0.3 * jaccard + 0.2 * seq


# ── Resolution cache ──────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if RESOLVE_CACHE_PATH.exists():
        try:
            return json.loads(RESOLVE_CACHE_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    RESOLVE_CACHE_PATH.write_text(json.dumps(cache, indent=2))


# ── Public API ────────────────────────────────────────────────────────────────

def lookup_ticker_from_edgar(
    awardee_name: str,
    min_confidence: float = MIN_EDGAR_CONFIDENCE,
) -> Tuple[Optional[str], Optional[str], float]:
    """
    Resolve an awardee company name to a public ticker via the SEC EDGAR index.

    Returns (ticker, company_title, confidence) or (None, None, 0.0).
    Results are cached so repeated lookups are instant.
    """
    cache = _load_cache()
    key   = awardee_name.lower().strip()

    if key in cache:
        entry = cache[key]
        return entry.get("ticker"), entry.get("company"), float(entry.get("confidence", 0.0))

    a_compact = _compact(awardee_name)
    if not a_compact or len(a_compact) < 3:
        _write_miss(cache, key, 0.0)
        return None, None, 0.0

    index = _get_index()

    # Score every candidate; only bother with non-zero scores
    scored: list[tuple[float, dict]] = []
    for company in index:
        s = _score(a_compact, company)
        if s > 0:
            scored.append((s, company))

    if not scored:
        _write_miss(cache, key, 0.0)
        return None, None, 0.0

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]

    # Reject ambiguous matches (two close candidates both below high-confidence threshold)
    if len(scored) > 1:
        runner_up = scored[1][0]
        if best_score < 0.85 and (best_score - runner_up) < AMBIGUITY_MARGIN:
            _write_miss(cache, key, best_score)
            return None, None, 0.0

    if best_score < min_confidence:
        _write_miss(cache, key, best_score)
        return None, None, 0.0

    ticker  = best["ticker"]
    title   = best["title"]
    print(f"[EDGAR] {awardee_name!r} → {ticker} ({title})  conf={best_score:.2f}")

    cache[key] = {"ticker": ticker, "company": title, "confidence": best_score, "source": "edgar"}
    _save_cache(cache)
    return ticker, title, best_score


def _write_miss(cache: dict, key: str, score: float) -> None:
    cache[key] = {"ticker": None, "company": None, "confidence": score}
    _save_cache(cache)


def clear_resolve_cache() -> None:
    """Remove all cached lookups (e.g. to force re-resolution after a ticker-map update)."""
    if RESOLVE_CACHE_PATH.exists():
        RESOLVE_CACHE_PATH.unlink()
        print(f"[EDGAR] Resolve cache cleared.")


def refresh_index() -> None:
    """Force a fresh download of the SEC company index regardless of cache age."""
    global _INDEX
    _INDEX = None
    if INDEX_PATH.exists():
        INDEX_PATH.unlink()
    _get_index()
