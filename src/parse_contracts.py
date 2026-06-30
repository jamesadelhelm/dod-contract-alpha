"""
Contract parsing module.
- parse_from_json: load from sample_contracts.json (or any list of dicts)
- parse_from_dod_html: scrape live DoD contracts page
- enrich_contract: add entity resolution + sector classification
"""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import List, Optional
import sys
import os

from src.models import Contract, ContractType, Sector
from src.entity_resolution import resolve_ticker
from src.classify_sector import classify_sector
from config import SAMPLE_CONTRACTS_PATH, DOD_CONTRACTS_URL


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_contract_type(text: str) -> ContractType:
    t = text.lower()
    if "modification" in t:
        return ContractType.MODIFICATION
    if "option" in t and "exercis" in t:
        return ContractType.OPTION_EXERCISE
    if "idiq" in t or "indefinite-delivery" in t or "indefinite delivery" in t:
        return ContractType.IDIQ
    if "delivery order" in t:
        return ContractType.DELIVERY_ORDER
    if "task order" in t:
        return ContractType.TASK_ORDER
    if "sole source" in t or "sole-source" in t:
        return ContractType.SOLE_SOURCE
    if "firm-fixed-price" in t or "cost-plus" in t or "cost plus" in t or "new award" in t:
        return ContractType.NEW_AWARD
    return ContractType.UNKNOWN


def _detect_is_idiq(text: str, ct: ContractType) -> bool:
    t = text.lower()
    return ct == ContractType.IDIQ or "idiq" in t or "indefinite-delivery" in t


def _detect_sole_source(text: str, ct: ContractType) -> bool:
    return ct == ContractType.SOLE_SOURCE or "sole-source" in text.lower() or "sole source" in text.lower()


def _detect_competitive(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ["competitive", "full and open competition", "solicitation"])


def _detect_pricing_type(text: str) -> Optional[str]:
    t = text.lower()
    if "firm-fixed-price" in t or "firm fixed price" in t or " ffp" in t:
        return "Fixed-Price"
    if "cost-plus" in t or "cost plus" in t or "cpff" in t or "cpaf" in t or "cpif" in t:
        return "Cost-Plus"
    if "time-and-materials" in t or "time and materials" in t or " t&m" in t:
        return "T&M"
    return None


# ── Parse from JSON file ──────────────────────────────────────────────────────

def parse_from_json(path: Path = SAMPLE_CONTRACTS_PATH) -> List[Contract]:
    with open(path, "r") as f:
        raw_list = json.load(f)
    contracts = []
    for raw in raw_list:
        ct = _parse_contract_type(raw.get("contract_type", ""))
        desc = raw.get("description", "")
        c = Contract(
            awardee_name=raw.get("awardee_name", "Unknown"),
            contract_value=float(raw.get("contract_value", 0)),
            funded_amount=raw.get("funded_amount"),
            contract_type=ct,
            agency=raw.get("agency"),
            branch=raw.get("branch"),
            description=desc,
            location=raw.get("location"),
            completion_date=raw.get("completion_date"),
            award_date=raw.get("award_date"),
            is_sole_source=raw.get("is_sole_source", _detect_sole_source(desc, ct)),
            is_competitive=raw.get("is_competitive", _detect_competitive(desc)),
            is_idiq=raw.get("is_idiq", _detect_is_idiq(desc, ct)),
            keywords=raw.get("keywords", []),
            raw_text=desc,
            pricing_type=raw.get("pricing_type") or _detect_pricing_type(desc),
        )
        contracts.append(c)
    return contracts


# ── Parse from live DoD HTML ──────────────────────────────────────────────────

def parse_from_dod_html(url: str = DOD_CONTRACTS_URL, max_contracts: int = 50) -> List[Contract]:
    """
    Scrape the DoD daily contracts page.
    Returns a list of partially parsed Contract objects.
    Requires: requests, beautifulsoup4
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ImportError("Install beautifulsoup4: pip install beautifulsoup4")

    try:
        from curl_cffi import requests as cf_requests
        resp = cf_requests.get(url, impersonate="chrome120", timeout=15)
        resp.raise_for_status()
    except ImportError:
        try:
            import requests
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            raise ConnectionError(f"Could not fetch DoD contracts page: {e}")
    except Exception as e:
        raise ConnectionError(f"Could not fetch DoD contracts page: {e}")

    soup = BeautifulSoup(resp.text, "html.parser")
    contracts = []

    # DoD contract page structure: paragraphs with contract text blocks
    # Each contract paragraph is typically separated by double-line breaks
    # and contains company name at start, dollar amount, agency at end.
    
    # Find main content area
    content = soup.find("div", class_="body-copy") or soup.find("div", id="main-content") or soup.body
    if not content:
        return []

    text = content.get_text(separator="\n")
    # Split on blank lines to get individual contract blurbs
    blocks = re.split(r"\n{2,}", text.strip())

    dollar_re = re.compile(r"\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion))?", re.IGNORECASE)
    date_re = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b", re.IGNORECASE)

    for block in blocks[:max_contracts]:
        block = block.strip()
        if len(block) < 80:
            continue

        # Extract dollar amount
        amounts = dollar_re.findall(block)
        contract_value = 0.0
        if amounts:
            raw_amt = amounts[0].replace("$", "").replace(",", "").strip()
            mult = 1.0
            if "billion" in raw_amt.lower():
                mult = 1000.0
                raw_amt = re.sub(r"billion", "", raw_amt, flags=re.IGNORECASE).strip()
            elif "million" in raw_amt.lower():
                raw_amt = re.sub(r"million", "", raw_amt, flags=re.IGNORECASE).strip()
            try:
                contract_value = float(raw_amt) * mult
            except ValueError:
                contract_value = 0.0

        # Extract awardee (first sentence or line usually has company name)
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        awardee = lines[0] if lines else "Unknown"
        # Trim long awardee lines to the first comma segment
        if "," in awardee:
            awardee = awardee.split(",")[0].strip()

        # Extract date
        dates = date_re.findall(block)
        award_date = dates[-1] if dates else None

        # Detect agency from trailing line
        agency = None
        for line in reversed(lines):
            if any(w in line.lower() for w in ["army", "navy", "air force", "marines", "disa", "dia", "dha", "mda", "dtra", "pentagon"]):
                agency = line
                break

        ct = _parse_contract_type(block)
        c = Contract(
            awardee_name=awardee,
            contract_value=contract_value,
            contract_type=ct,
            agency=agency,
            description=block[:500],
            award_date=award_date,
            is_sole_source=_detect_sole_source(block, ct),
            is_competitive=_detect_competitive(block),
            is_idiq=_detect_is_idiq(block, ct),
            raw_text=block,
        )
        contracts.append(c)

    return contracts


# ── Enrich contracts ──────────────────────────────────────────────────────────

def enrich_contracts(contracts: List[Contract]) -> List[Contract]:
    """
    Add ticker mapping and sector classification to each contract.
    """
    enriched = []
    for c in contracts:
        ticker, parent, confidence = resolve_ticker(c.awardee_name)
        c.ticker = ticker
        c.parent_company = parent
        c.ticker_confidence = confidence
        c.sector = classify_sector(c)
        enriched.append(c)
    return enriched


def load_and_enrich(source: str = "mock", days_back: int = 7) -> List[Contract]:
    """
    Main entry: load contracts from 'mock', 'live' (defense.gov), or 'usaspending'.
    source='usaspending': fetch from USAspending.gov API (best structured data)
    source='live'       : scrape defense.gov HTML (good for same-day announcements)
    source='mock'       : use sample_contracts.json (offline / testing)
    """
    if source == "usaspending":
        try:
            from src.fetch_usaspending import load_from_usaspending
            raw_dicts = load_from_usaspending(days_back=days_back)
            raw_contracts = parse_from_json_list(raw_dicts)
        except Exception as e:
            print(f"[parse] USAspending failed ({e}), falling back to mock")
            raw_contracts = parse_from_json()
    elif source == "live":
        raw_contracts = parse_from_dod_html()
    else:
        raw_contracts = parse_from_json()
    return enrich_contracts(raw_contracts)


def parse_from_json_list(raw_list: list) -> List[Contract]:
    """Parse a list of dicts (same format as sample_contracts.json)."""
    contracts = []
    for raw in raw_list:
        ct = _parse_contract_type(raw.get("contract_type", ""))
        desc = raw.get("description", "")
        c = Contract(
            awardee_name=raw.get("awardee_name", "Unknown"),
            contract_value=float(raw.get("contract_value", 0)),
            funded_amount=raw.get("funded_amount"),
            contract_type=ct,
            agency=raw.get("agency"),
            branch=raw.get("branch"),
            description=desc,
            location=raw.get("location"),
            completion_date=raw.get("completion_date"),
            award_date=raw.get("award_date"),
            is_sole_source=raw.get("is_sole_source", _detect_sole_source(desc, ct)),
            is_competitive=raw.get("is_competitive", _detect_competitive(desc)),
            is_idiq=raw.get("is_idiq", _detect_is_idiq(desc, ct)),
            keywords=raw.get("keywords", []),
            raw_text=desc,
            pricing_type=raw.get("pricing_type") or _detect_pricing_type(desc),
        )
        contracts.append(c)
    return contracts
