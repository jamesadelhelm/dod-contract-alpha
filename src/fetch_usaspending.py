"""
USAspending.gov API client.

Endpoints used:
  POST /api/v2/search/spending_by_award/     — search DoD contract awards
  GET  /api/v2/awards/{award_id}/            — award detail
  POST /api/v2/federal_obligations/          — obligations over time

No API key required. Rate limit: ~10 req/s.

Usage:
  from src.fetch_usaspending import fetch_recent_dod_awards, fetch_award_detail
  awards = fetch_recent_dod_awards(days_back=7)
"""

from __future__ import annotations
import json
import time
import datetime
from typing import List, Dict, Optional
import requests
from config import SAMPLE_CONTRACTS_PATH

BASE_URL = "https://api.usaspending.gov/api/v2"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "dod-contract-research-agent/1.0 (non-commercial research)",
}
REQUEST_DELAY = 0.15  # seconds between requests


def _post(endpoint: str, payload: dict, timeout: int = 20) -> Optional[dict]:
    url = f"{BASE_URL}{endpoint}"
    try:
        time.sleep(REQUEST_DELAY)
        r = requests.post(url, json=payload, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        body = ""
        try:
            body = e.response.json()
        except Exception:
            body = e.response.text[:300]
        print(f"[USAspending] HTTP {e.response.status_code} on {endpoint}: {body}")
        return None
    except Exception as e:
        print(f"[USAspending] Error on {endpoint}: {e}")
        return None


def _get(endpoint: str, params: dict = None, timeout: int = 20) -> Optional[dict]:
    url = f"{BASE_URL}{endpoint}"
    try:
        time.sleep(REQUEST_DELAY)
        r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[USAspending] Error on {endpoint}: {e}")
        return None


def _dod_fiscal_year_start() -> datetime.date:
    """Return Oct 1 of the current DoD fiscal year."""
    today = datetime.date.today()
    if today.month >= 10:
        return datetime.date(today.year, 10, 1)
    else:
        return datetime.date(today.year - 1, 10, 1)


def fetch_recent_dod_awards(
    days_back: int = 7,
    limit: int = 100,
    min_award_amount: float = 5_000_000,
    max_pages: int = 10,
    use_fiscal_year: bool = True,
) -> List[Dict]:
    """
    Fetch DoD contract awards from USAspending.gov.

    use_fiscal_year=True (default): fetches all awards in the current DoD fiscal
    year (Oct 1 to today). Recommended — USAspending has a 30-90 day lag on new
    data, so 'last N days' filters often return nothing.

    use_fiscal_year=False: uses days_back to set the lookback window.
    Returns list of raw award dicts sorted by Award Amount descending.
    """
    end_date = datetime.date.today()
    if use_fiscal_year:
        start_date = _dod_fiscal_year_start()
    else:
        start_date = end_date - datetime.timedelta(days=days_back)

    base_payload = {
        "filters": {
            "agencies": [{
                "type": "awarding",
                "tier": "toptier",
                "name": "Department of Defense"
            }],
            "time_period": [{
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
            }],
            "award_type_codes": ["A", "B", "C", "D"],  # procurement contracts only
        },
        "fields": [
            "Award ID",
            "Recipient Name",
            "recipient_id",
            "Award Amount",
            "Description",
            "Awarding Agency",
            "Awarding Sub Agency",
            "Period of Performance Start Date",
            "Period of Performance Current End Date",
            "Place of Performance City Code",
            "Place of Performance State Code",
            "Contract Award Type",
            "Type of Contract Pricing",
            "generated_internal_id",
            "Last Modified Date",
            "Base Obligation Date",
            "Funding Agency",
        ],
        "limit": limit,
        "sort": "Award Amount",
        "order": "desc",
    }

    all_results: List[Dict] = []
    seen_ids: set = set()

    for page in range(1, max_pages + 1):
        payload = {**base_payload, "page": page}
        data = _post("/search/spending_by_award/", payload)
        if not data:
            break

        page_results = data.get("results", [])
        if not page_results:
            break

        for r in page_results:
            rid = r.get("generated_internal_id") or r.get("Award ID")
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                all_results.append(r)

        # No more pages if this page returned fewer results than requested
        if len(page_results) < limit:
            break

        # Stop early only if the MAXIMUM on this page is below the threshold
        # (i.e., all remaining pages would also be below — results are sorted desc)
        # Do NOT stop based on min-on-page: USAspending mixes $0 records into results
        # even when sorted descending, which would falsely terminate pagination.
        page_max = max((float(r.get("Award Amount") or 0) for r in page_results), default=0)
        if page_max < min_award_amount:
            break

    # Filter by minimum amount
    if min_award_amount > 0:
        all_results = [r for r in all_results
                       if float(r.get("Award Amount") or 0) >= min_award_amount]

    print(f"[USAspending] Fetched {len(all_results)} awards (days_back={days_back})")
    return all_results


def fetch_award_detail(award_id: str) -> Optional[Dict]:
    """
    Fetch full detail for a specific award including funded/obligated amounts.
    award_id: the 'generated_internal_id' from search results
    """
    data = _get(f"/awards/{award_id}/")
    return data


def fetch_recipient_awards(
    recipient_name: str,
    limit: int = 10,
    fiscal_year: int = None,
) -> List[Dict]:
    """
    Fetch all DoD contracts for a specific recipient (company name).
    Useful for building company-level contract history.
    """
    fy = fiscal_year or datetime.date.today().year
    payload = {
        "filters": {
            "agencies": [{"type": "awarding", "tier": "toptier", "name": "Department of Defense"}],
            "recipient_search_text": [recipient_name],
            "time_period": [{"start_date": f"{fy-1}-10-01", "end_date": f"{fy}-09-30"}],
            "award_type_codes": ["A", "B", "C", "D"],
        },
        "fields": [
            "Award ID", "Award Amount", "Description",
            "Awarding Agency", "Period of Performance Current End Date",
            "Contract Award Type", "generated_internal_id",
        ],
        "limit": limit,
        "sort": "Award Amount",
        "order": "desc",
        "page": 1,
    }
    data = _post("/search/spending_by_award/", payload)
    return data.get("results", []) if data else []


def fetch_agency_obligations(
    agency_code: str = "097",  # DoD = 097
    fiscal_year: int = None,
) -> Optional[Dict]:
    """
    Fetch total obligations for DoD by fiscal year.
    Useful for macro budget trend context.
    """
    fy = fiscal_year or datetime.date.today().year
    data = _get(f"/agency/{agency_code}/obligations_by_award_category/",
                params={"fiscal_year": fy})
    return data


def usaspending_awards_to_contracts(raw_awards: List[Dict]) -> List[Dict]:
    """
    Normalize USAspending award dicts to the same format as sample_contracts.json
    so parse_contracts.py can ingest them without changes.
    """
    normalized = []
    for a in raw_awards:
        # Map contract type codes
        ct_code = a.get("Contract Award Type", "")
        ct_map = {
            "A": "New Award",
            "B": "New Award",
            "C": "Modification",
            "D": "Modification",
            "IDC": "IDIQ",
        }
        contract_type = ct_map.get(ct_code, "New Award")

        desc = a.get("Description", "") or ""
        is_idiq = "IDC" in ct_code or "idiq" in desc.lower() or "indefinite" in desc.lower()
        is_sole = "sole source" in desc.lower() or "sole-source" in desc.lower()
        is_comp = "full and open" in desc.lower() or "competitive" in desc.lower()

        normalized.append({
            "awardee_name": a.get("Recipient Name", "Unknown"),
            "contract_value": float(a.get("Award Amount", 0)) / 1_000_000,  # → millions
            "funded_amount": float(a.get("Award Amount", 0)) / 1_000_000,
            "contract_type": contract_type,
            "agency": a.get("Awarding Agency", ""),
            "branch": a.get("Awarding Sub Agency", ""),
            "description": desc[:1000],
            "location": f"{a.get('Place of Performance City Code','')}, {a.get('Place of Performance State Code','')}".strip(", "),
            "completion_date": a.get("Period of Performance Current End Date"),
            "award_date": a.get("Base Obligation Date") or a.get("Last Modified Date"),
            "is_sole_source": is_sole,
            "is_competitive": is_comp,
            "is_idiq": is_idiq,
            "keywords": [],
            "_usaspending_id": a.get("generated_internal_id", ""),
        })
    return normalized


def load_from_usaspending(days_back: int = 30, min_amount_millions: float = 5.0) -> List[Dict]:
    """
    Main entry: fetch from USAspending and return normalized contract dicts.
    Defaults to fiscal-year mode (Oct 1 to today) to work around USAspending's
    30-90 day data lag. Falls back to mock data if network unavailable.
    """
    fy_start = _dod_fiscal_year_start()
    today = datetime.date.today()
    print(f"[USAspending] Fetching FY contracts ({fy_start} → {today}, min ${min_amount_millions:.0f}M)")
    try:
        raw = fetch_recent_dod_awards(
            days_back=days_back,
            limit=100,
            min_award_amount=min_amount_millions * 1_000_000,
            use_fiscal_year=True,
        )
        if raw:
            normalized = usaspending_awards_to_contracts(raw)
            print(f"[USAspending] Normalized {len(normalized)} contracts")
            return normalized
        else:
            # Zero results almost always means a network or lag issue, not
            # a genuinely empty dataset. Failing loudly prevents a report
            # generated from stale mock data from being mistaken for live output.
            print()
            print("=" * 60)
            print("  ⚠️  WARNING: USAspending returned 0 results.")
            print("  This usually indicates a network issue, API outage,")
            print("  or USAspending data lag. The report below is based on")
            print("  MOCK (sample) data, NOT live contract awards.")
            print("  Re-run when connectivity is confirmed before acting on output.")
            print("=" * 60)
            print()
            return _load_mock_fallback()
    except Exception as e:
        print()
        print("=" * 60)
        print(f"  ⚠️  WARNING: USAspending fetch failed: {e}")
        print("  Report is based on MOCK (sample) data, NOT live contract awards.")
        print("  Do not act on this output until live data is confirmed.")
        print("=" * 60)
        print()
        return _load_mock_fallback()


def _load_mock_fallback() -> List[Dict]:
    with open(SAMPLE_CONTRACTS_PATH) as f:
        return json.load(f)
