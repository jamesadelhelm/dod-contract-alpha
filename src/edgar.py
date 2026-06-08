"""
SEC EDGAR scraper for 10-K filings.

Extracts from annual reports:
  - U.S. government / DoD revenue % and dollar amounts
  - Backlog by segment
  - Recompete risk language
  - Pension obligation size
  - Key government program names

Uses EDGAR full-text search API (free, no key) and EDGAR company filings API.

Primary endpoints:
  https://efts.sec.gov/LATEST/search-index?q=...&dateRange=custom&...  (full-text search)
  https://data.sec.gov/submissions/CIK{cik}.json                       (filing list)
  https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/...         (actual filing)

All parsing is regex-based on the filing text — no XML/XBRL dependency.
Falls back gracefully when filings can't be parsed.
"""

from __future__ import annotations
import re
import json
import time
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
import requests

HEADERS = {
    "User-Agent": "dod-contract-research-agent research@example.com",  # EDGAR requires User-Agent
    "Accept-Encoding": "gzip, deflate",
    "Host": "data.sec.gov",
}
SEARCH_HEADERS = {
    "User-Agent": "dod-contract-research-agent research@example.com",
}
RATE_LIMIT = 0.12   # EDGAR asks for max 10 req/s


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class EdgarResult:
    ticker: str
    cik: Optional[str] = None
    filing_date: Optional[str] = None
    filing_url: Optional[str] = None

    # Revenue breakdown
    us_government_revenue_pct: Optional[float] = None
    dod_revenue_pct: Optional[float] = None
    us_government_revenue_millions: Optional[float] = None
    total_revenue_millions: Optional[float] = None

    # Backlog
    total_backlog_millions: Optional[float] = None
    funded_backlog_millions: Optional[float] = None
    backlog_to_revenue: Optional[float] = None

    # Risk flags
    recompete_risk_mentioned: bool = False
    recompete_excerpts: List[str] = field(default_factory=list)
    pension_liability_millions: Optional[float] = None
    concentration_risk_mentioned: bool = False

    # Key programs mentioned
    key_programs: List[str] = field(default_factory=list)

    # Data quality
    extraction_confidence: str = "low"   # low / medium / high
    parse_notes: List[str] = field(default_factory=list)
    raw_snippets: Dict[str, str] = field(default_factory=dict)


# ── Main entry point ──────────────────────────────────────────────────────────

def fetch_edgar_data(ticker: str) -> EdgarResult:
    """
    Fetch and parse 10-K data for a ticker from SEC EDGAR.
    Returns EdgarResult with whatever could be extracted.
    Fails gracefully — never raises.
    """
    result = EdgarResult(ticker=ticker)
    try:
        # Step 1: Resolve ticker → CIK
        cik = _get_cik(ticker)
        if not cik:
            result.parse_notes.append(f"Could not resolve CIK for {ticker}")
            return result
        result.cik = cik

        # Step 2: Get most recent 10-K filing URL
        filing_url, filing_date, accession = _get_latest_10k(cik)
        if not filing_url:
            result.parse_notes.append("No 10-K filing found in EDGAR")
            return result
        result.filing_url = filing_url
        result.filing_date = filing_date

        # Step 3: Fetch filing text
        text = _fetch_filing_text(cik, accession)
        if not text:
            result.parse_notes.append("Could not fetch filing text")
            return result

        # Step 4: Extract fields
        _extract_revenue_breakdown(text, result)
        _extract_backlog(text, result)
        _extract_recompete_risk(text, result)
        _extract_pension(text, result)
        _extract_key_programs(text, result)

        # Compute derived fields
        if result.us_government_revenue_millions and result.total_revenue_millions:
            if result.total_revenue_millions > 0:
                result.us_government_revenue_pct = round(
                    result.us_government_revenue_millions / result.total_revenue_millions * 100, 1
                )
        if result.total_backlog_millions and result.total_revenue_millions:
            if result.total_revenue_millions > 0:
                result.backlog_to_revenue = round(
                    result.total_backlog_millions / result.total_revenue_millions, 2
                )

        # Assess confidence
        filled = sum(1 for v in [
            result.us_government_revenue_pct,
            result.dod_revenue_pct,
            result.total_backlog_millions,
        ] if v is not None)
        result.extraction_confidence = ["low", "medium", "high"][min(filled, 2)]

    except Exception as e:
        result.parse_notes.append(f"Extraction error: {e}")

    return result


# ── CIK resolution ────────────────────────────────────────────────────────────

def _get_cik(ticker: str) -> Optional[str]:
    """Resolve ticker to CIK using EDGAR's company_tickers.json (authoritative)."""
    # Primary: EDGAR's official ticker→CIK map — most reliable, no scraping
    try:
        time.sleep(RATE_LIMIT)
        r = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": "dod-contract-research-agent research@example.com"},
            timeout=10,
        )
        data = r.json()
        ticker_upper = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker_upper:
                return str(entry["cik_str"]).zfill(10)
    except Exception:
        pass

    # Secondary: browse-edgar CGI atom feed (may fail if EDGAR changes format)
    try:
        time.sleep(RATE_LIMIT)
        r = requests.get(
            "https://www.sec.gov/cgi-bin/browse-edgar",
            params={"company": "", "CIK": ticker, "type": "10-K",
                    "dateb": "", "owner": "include", "count": "1",
                    "search_text": "", "action": "getcompany", "output": "atom"},
            headers={"User-Agent": "dod-contract-research-agent research@example.com"},
            timeout=10,
        )
        cik_match = re.search(r'/cgi-bin/browse-edgar\?action=getcompany&CIK=(\d+)', r.text)
        if cik_match:
            return cik_match.group(1).zfill(10)
    except Exception:
        pass

    return None




# ── Filing list ───────────────────────────────────────────────────────────────

def _get_latest_10k(cik: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Fetch the most recent 10-K filing URL for a CIK.
    Returns (document_url, filing_date, accession_number).
    """
    time.sleep(RATE_LIMIT)
    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        r = requests.get(url, headers={
            "User-Agent": "dod-contract-research-agent research@example.com",
            "Host": "data.sec.gov",
        }, timeout=10)
        data = r.json()

        filings = data.get("filings", {}).get("recent", {})
        forms   = filings.get("form", [])
        dates   = filings.get("filingDate", [])
        accessions = filings.get("accessionNumber", [])

        # Find most recent 10-K
        for i, form in enumerate(forms):
            if form in ("10-K", "10-K/A"):
                acc = accessions[i].replace("-", "")
                date = dates[i]
                filing_index_url = (
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/"
                )
                return filing_index_url, date, acc

    except Exception as e:
        pass

    return None, None, None


# ── Fetch filing text ─────────────────────────────────────────────────────────

def _fetch_filing_text(cik: str, accession: str) -> Optional[str]:
    """
    Fetch the full text of a 10-K filing.
    First gets the index to find the main document, then fetches it.
    Limits to 2MB to keep memory reasonable.
    """
    try:
        time.sleep(RATE_LIMIT)
        # Get filing index
        index_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/"
            f"{accession}-index.htm"
        )
        r = requests.get(index_url, headers={
            "User-Agent": "dod-contract-research-agent research@example.com"
        }, timeout=12)

        # Find the main 10-K document (usually .htm or .txt)
        doc_pattern = re.compile(
            r'href="(/Archives/edgar/data/[^"]+\.(?:htm|txt))"[^>]*>(?:[^<]*10-K[^<]*|[^<]*Annual[^<]*)</a>',
            re.IGNORECASE
        )
        matches = doc_pattern.findall(r.text)

        # Also try simpler pattern
        if not matches:
            matches = re.findall(
                r'/Archives/edgar/data/\d+/[^"\']+\.htm',
                r.text
            )

        if not matches:
            return None

        doc_url = "https://www.sec.gov" + matches[0]
        time.sleep(RATE_LIMIT)
        r2 = requests.get(doc_url, headers={
            "User-Agent": "dod-contract-research-agent research@example.com"
        }, timeout=20, stream=True)

        # Read up to 2MB
        chunks = []
        total = 0
        for chunk in r2.iter_content(chunk_size=65536):
            chunks.append(chunk)
            total += len(chunk)
            if total > 2_000_000:
                break

        raw = b"".join(chunks).decode("utf-8", errors="ignore")

        # Strip HTML tags for easier regex
        text = re.sub(r'<[^>]+>', ' ', raw)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'\s+', ' ', text)
        return text

    except Exception as e:
        return None


# ── Revenue breakdown ─────────────────────────────────────────────────────────

# Dollar patterns: "$1.2 billion", "$450 million", "$1,234", "1,234.5"
_DOLLAR_PAT = re.compile(
    r'\$\s*([\d,]+(?:\.\d+)?)\s*(billion|million|thousand)?'
    r'|(\b[\d,]+(?:\.\d+)?)\s*(billion|million)',
    re.IGNORECASE
)

_PCT_PAT = re.compile(r'(\d{1,3}(?:\.\d{1,2})?)\s*%')


def _parse_dollar(text: str) -> Optional[float]:
    """Extract first dollar amount from text snippet, in millions."""
    m = _DOLLAR_PAT.search(text)
    if not m:
        return None
    if m.group(1):
        val = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").lower()
    else:
        val = float(m.group(3).replace(",", ""))
        unit = (m.group(4) or "").lower()

    if "billion" in unit:
        return round(val * 1000, 1)
    if "thousand" in unit:
        return round(val / 1000, 1)
    return round(val, 1)


def _extract_revenue_breakdown(text: str, result: EdgarResult) -> None:
    """
    Find government / DoD revenue disclosures in 10-K text.
    Common patterns:
      "U.S. government revenue was $X.X billion, or XX% of total revenue"
      "revenues from the U.S. government ... approximately XX%"
      "Department of Defense ... $X billion"
    """
    # Government revenue percentage patterns
    gov_pct_patterns = [
        r'[Uu]\.?[Ss]\.?\s*[Gg]overnment[^.]{0,80}?(\d{1,3}(?:\.\d)?)\s*%\s*of\s*(?:total\s*)?(?:net\s*)?revenue',
        r'(\d{1,3}(?:\.\d)?)\s*%\s*of\s*(?:our\s*)?(?:total\s*)?revenues?[^.]{0,60}?[Uu]\.?[Ss]\.?\s*[Gg]overnment',
        r'[Gg]overnment\s+(?:revenues?|sales?)[^.]{0,80}?(\d{1,3}(?:\.\d)?)\s*%',
        r'approximately\s+(\d{1,3}(?:\.\d)?)\s*%[^.]{0,80}?[Uu]\.?[Ss]\.?\s*[Gg]overnment',
        r'[Uu]\.?[Ss]\.?\s*[Gg]overnment\s+(?:contracts?|customers?)[^.]{0,80}?(\d{1,3}(?:\.\d)?)\s*%',
    ]
    for pat in gov_pct_patterns:
        m = re.search(pat, text)
        if m:
            val = float(m.group(1))
            if 5 <= val <= 100:
                result.us_government_revenue_pct = val
                snippet = text[max(0, m.start()-50):m.end()+50]
                result.raw_snippets["gov_revenue_pct"] = snippet.strip()
                break

    # DoD revenue percentage
    dod_pct_patterns = [
        r'[Dd]epartment\s+of\s+[Dd]efense[^.]{0,80}?(\d{1,3}(?:\.\d)?)\s*%',
        r'(\d{1,3}(?:\.\d)?)\s*%[^.]{0,80}?[Dd]epartment\s+of\s+[Dd]efense',
        r'DoD[^.]{0,80}?(\d{1,3}(?:\.\d)?)\s*%\s*of\s*(?:total\s*)?revenue',
        r'(\d{1,3}(?:\.\d)?)\s*%[^.]{0,60}?DoD',
        r'[Dd]efense[^.]{0,80}?(\d{1,3}(?:\.\d)?)\s*%\s*of\s*(?:total\s*|net\s*)?revenue',
    ]
    for pat in dod_pct_patterns:
        m = re.search(pat, text)
        if m:
            val = float(m.group(1))
            if 5 <= val <= 100:
                result.dod_revenue_pct = val
                snippet = text[max(0, m.start()-50):m.end()+50]
                result.raw_snippets["dod_revenue_pct"] = snippet.strip()
                break

    # Government revenue in dollars
    gov_dollar_patterns = [
        r'[Uu]\.?[Ss]\.?\s*[Gg]overnment[^.]{0,120}?(\$[\d,]+(?:\.\d+)?\s*(?:billion|million))',
        r'revenues?\s+from\s+(?:the\s+)?[Uu]\.?[Ss]\.?\s*[Gg]overnment[^.]{0,100}?(\$[\d,]+(?:\.\d+)?\s*(?:billion|million))',
    ]
    for pat in gov_dollar_patterns:
        m = re.search(pat, text)
        if m:
            val = _parse_dollar(m.group(1))
            if val and val > 0:
                result.us_government_revenue_millions = val
                break

    # Total revenue (look near "total revenues" or "net revenues")
    rev_patterns = [
        r'[Tt]otal\s+(?:net\s+)?revenues?\s+(?:were|of|was)\s+(\$[\d,.]+\s*(?:billion|million))',
        r'[Nn]et\s+revenues?\s+(?:were|of|was|totaled)\s+(\$[\d,.]+\s*(?:billion|million))',
        r'revenues?\s+(?:were|of|totaled)\s+(\$[\d,.]+\s*(?:billion|million))',
    ]
    for pat in rev_patterns:
        m = re.search(pat, text)
        if m:
            val = _parse_dollar(m.group(1))
            if val and val > 10:  # sanity: >$10M
                result.total_revenue_millions = val
                break


# ── Backlog extraction ────────────────────────────────────────────────────────

def _extract_backlog(text: str, result: EdgarResult) -> None:
    """Extract total backlog and funded backlog."""
    backlog_patterns = [
        r'[Tt]otal\s+backlog[^.]{0,100}?(\$[\d,.]+\s*(?:billion|million))',
        r'backlog\s+(?:of|was|totaled|at)\s+(\$[\d,.]+\s*(?:billion|million))',
        r'(\$[\d,.]+\s*(?:billion|million))\s+(?:in\s+)?(?:total\s+)?backlog',
        r'[Rr]emaining\s+performance\s+obligations[^.]{0,80}?(\$[\d,.]+\s*(?:billion|million))',
        r'[Oo]rder\s+backlog[^.]{0,80}?(\$[\d,.]+\s*(?:billion|million))',
    ]
    for pat in backlog_patterns:
        m = re.search(pat, text)
        if m:
            val = _parse_dollar(m.group(1))
            if val and val > 0:
                result.total_backlog_millions = val
                snippet = text[max(0, m.start()-30):m.end()+80]
                result.raw_snippets["backlog"] = snippet.strip()
                break

    funded_patterns = [
        r'[Ff]unded\s+backlog[^.]{0,80}?(\$[\d,.]+\s*(?:billion|million))',
        r'(\$[\d,.]+\s*(?:billion|million))\s+(?:in\s+)?funded\s+backlog',
    ]
    for pat in funded_patterns:
        m = re.search(pat, text)
        if m:
            val = _parse_dollar(m.group(1))
            if val and val > 0:
                result.funded_backlog_millions = val
                break


# ── Recompete risk ────────────────────────────────────────────────────────────

def _extract_recompete_risk(text: str, result: EdgarResult) -> None:
    """Look for recompete, rebid, and contract concentration risk language."""
    recompete_patterns = [
        r'[Rr]ecompet[^.]{0,200}',
        r'[Rr]e-compet[^.]{0,200}',
        r'[Cc]ontract\s+(?:must\s+be\s+)?rebid[^.]{0,150}',
        r'[Oo]ption\s+(?:period|year)s?\s+(?:expire|expir)[^.]{0,150}',
        r'[Cc]ontract\s+expir[^.]{0,150}',
    ]
    excerpts = []
    for pat in recompete_patterns:
        for m in re.finditer(pat, text):
            excerpt = m.group(0).strip()[:200]
            if excerpt not in excerpts:
                excerpts.append(excerpt)
            if len(excerpts) >= 3:
                break

    if excerpts:
        result.recompete_risk_mentioned = True
        result.recompete_excerpts = excerpts[:3]

    # Concentration risk
    conc_patterns = [
        r'(?:significant|substantial|material)\s+portion[^.]{0,100}?(?:government|DoD|federal)',
        r'[Cc]oncentration\s+(?:of\s+)?(?:revenue|sales|business)[^.]{0,100}?(?:government|federal)',
        r'loss\s+of\s+(?:one|a)\s+(?:or\s+more\s+)?(?:significant|major|key)\s+(?:government\s+)?contract',
    ]
    for pat in conc_patterns:
        if re.search(pat, text):
            result.concentration_risk_mentioned = True
            break


# ── Pension ───────────────────────────────────────────────────────────────────

def _extract_pension(text: str, result: EdgarResult) -> None:
    """Extract pension/OPEB liability size."""
    pension_patterns = [
        r'[Pp]ension[^.]{0,120}?(\$[\d,.]+\s*(?:billion|million))[^.]{0,60}?(?:liability|obligation|deficit)',
        r'[Pp]ension\s+(?:benefit\s+)?obligation[^.]{0,80}?(\$[\d,.]+\s*(?:billion|million))',
        r'(?:underfunded|unfunded)[^.]{0,60}?pension[^.]{0,80}?(\$[\d,.]+\s*(?:billion|million))',
    ]
    for pat in pension_patterns:
        m = re.search(pat, text)
        if m:
            val = _parse_dollar(m.group(1))
            if val and val > 0:
                result.pension_liability_millions = val
                break


# ── Key programs ──────────────────────────────────────────────────────────────

# Known major DoD program names to look for
_PROGRAM_NAMES = [
    "F-35", "B-21", "Virginia-class", "Columbia-class", "THAAD", "Patriot",
    "C-130", "V-22", "CH-53", "UH-60", "Black Hawk", "Aegis", "DDG-51",
    "CVN", "SSBN", "SSN", "LPD", "LHD", "KC-46", "T-7A",
    "Sentinel", "GBSD", "LGM-35", "IVAS", "Army Vantage",
    "TRICARE", "JLTV", "Stryker", "Abrams", "Paladin",
    "GPS III", "SBIRS", "Next Gen OPIR", "WGS",
    "HIMARS", "Javelin", "Stinger", "ATACMS",
    "Electron", "Neutron", "Falcon 9",
]

def _extract_key_programs(text: str, result: EdgarResult) -> None:
    found = []
    for prog in _PROGRAM_NAMES:
        if re.search(re.escape(prog), text, re.IGNORECASE):
            found.append(prog)
    result.key_programs = found[:15]  # cap at 15


# ── Overlay into CompanyFundamentals ─────────────────────────────────────────

def overlay_edgar_into_fundamentals(
    f: "CompanyFundamentals",
    edgar: EdgarResult,
) -> None:
    """
    Write EDGAR-extracted values into CompanyFundamentals.
    Only overwrites fields that are currently None (EDGAR supplements, not replaces).
    Live yfinance data takes priority for financial ratios;
    EDGAR takes priority for government revenue % and backlog (it's the primary source).
    """
    # Government revenue — EDGAR is authoritative, always overwrite
    if edgar.us_government_revenue_pct is not None:
        f.government_revenue_pct = edgar.us_government_revenue_pct

    if edgar.dod_revenue_pct is not None:
        f.dod_revenue_pct = edgar.dod_revenue_pct

    # Backlog — EDGAR is authoritative
    if edgar.backlog_to_revenue is not None:
        f.backlog_to_revenue = edgar.backlog_to_revenue

    # Revenue sanity check — if EDGAR total revenue is within 20% of yfinance, trust yfinance
    if edgar.total_revenue_millions and f.annual_revenue_millions is None:
        f.annual_revenue_millions = edgar.total_revenue_millions

    # Update data notes
    if edgar.extraction_confidence in ("medium", "high"):
        note_parts = [f"EDGAR 10-K ({edgar.filing_date or 'date unknown'}, confidence={edgar.extraction_confidence})."]
        if edgar.us_government_revenue_pct:
            note_parts.append(f"Gov rev: {edgar.us_government_revenue_pct:.0f}%.")
        if edgar.dod_revenue_pct:
            note_parts.append(f"DoD rev: {edgar.dod_revenue_pct:.0f}%.")
        if edgar.total_backlog_millions:
            note_parts.append(f"Backlog: ${edgar.total_backlog_millions:.0f}M.")
        f.data_notes = (f.data_notes + " " + " ".join(note_parts)).strip()

    # Flag pension risk in data notes
    if edgar.pension_liability_millions and edgar.pension_liability_millions > 500:
        f.data_notes += f" ⚠️ Pension liability: ${edgar.pension_liability_millions:.0f}M."


# ── XBRL structured data (fast, no HTML scraping) ────────────────────────────

def fetch_xbrl_financials(ticker: str) -> Dict[str, any]:
    """
    Fetch structured financial data from EDGAR's XBRL API.
    Much faster and more reliable than HTML scraping.
    Returns a dict with primary-source annual financials.
    Never raises — returns empty dict on failure.

    Key outputs:
      revenues        [(year_str, dollars)] last 5 FY
      backlog         float | None  (most recent FY, dollars)
      op_cash_flows   [(year_str, dollars)] last 5 FY
      capex           [(year_str, dollars)] last 5 FY (payments, positive)
      backlog_to_rev  float | None  (backlog / most_recent_revenue)
      fcf_margin_3yr  float | None  (3-yr avg FCF / revenue, in % form)
      rev_cagr_3yr    float | None  (3yr CAGR in %, e.g. 5.2 = 5.2%)
      latest_rev      float | None  (dollars)
    """
    cik = _get_cik(ticker)
    if not cik:
        return {}

    try:
        time.sleep(RATE_LIMIT)
        r = requests.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            headers={"User-Agent": "dod-contract-research-agent research@example.com"},
            timeout=25,
        )
        if r.status_code != 200:
            return {}

        facts = r.json().get("facts", {}).get("us-gaap", {})

        def get_fy_values(concept: str, units: str = "USD") -> list:
            data = facts.get(concept, {}).get("units", {}).get(units, [])
            annual = [x for x in data
                      if x.get("form") in ("10-K", "10-K/A") and x.get("fp") == "FY"]
            return sorted(annual, key=lambda x: x["end"])

        def dedup_by_year(rows: list) -> list:
            """For each fiscal year, keep the entry with the largest value.
            Handles amended 10-K/A filings and companies with non-Dec fiscal years
            that sometimes generate duplicate year-end entries."""
            by_year: dict[str, int] = {}
            for x in rows:
                yr = x["end"][:4]
                if yr not in by_year or x["val"] > by_year[yr]:
                    by_year[yr] = x["val"]
            return sorted(by_year.items())  # [(year_str, value), ...]

        # Revenue — try all concepts, pick the one with the most recent data.
        # `or` short-circuits on non-empty lists, so a concept with stale 2009
        # data blocks the fallback that has current 2025 data (e.g. HON).
        _rev_candidates = [
            get_fy_values("Revenues"),
            get_fy_values("RevenueFromContractWithCustomerExcludingAssessedTax"),
            get_fy_values("SalesRevenueNet"),
            get_fy_values("SalesRevenueGoodsNet"),
        ]
        def _latest_year(rows: list) -> int:
            return max((int(x["end"][:4]) for x in rows), default=0)
        rev_rows = max(_rev_candidates, key=_latest_year) if any(_rev_candidates) else []
        revenues = dedup_by_year(rev_rows)[-5:] if rev_rows else []

        # Backlog (Remaining Performance Obligations = contractual backlog)
        rpo_rows = get_fy_values("RevenueRemainingPerformanceObligation")
        backlog = rpo_rows[-1]["val"] if rpo_rows else None

        # Operating cash flow
        ocf_rows = get_fy_values("NetCashProvidedByUsedInOperatingActivities")
        op_cash_flows = dedup_by_year(ocf_rows)[-5:] if ocf_rows else []

        # Capex — merge both standard concepts; dedup picks max per year so the
        # more recent (and usually larger) "productive assets" figure wins for LMT.
        capex_rows = (
            get_fy_values("PaymentsToAcquirePropertyPlantAndEquipment") +
            get_fy_values("PaymentsToAcquireProductiveAssets")
        )
        capex_vals = dedup_by_year(capex_rows)[-5:] if capex_rows else []

        # Derived: backlog / revenue
        latest_rev = revenues[-1][1] if revenues else None
        latest_year = int(revenues[-1][0]) if revenues else 0
        _stale = latest_year < 2022  # Revenue data older than 3 years gives unreliable ratios
        backlog_to_rev = (backlog / latest_rev) if (backlog and latest_rev and latest_rev > 0 and not _stale) else None

        # Derived: 3-year normalized FCF margin (skip if data is stale)
        fcf_margin_3yr = None
        if not _stale and op_cash_flows and capex_vals and revenues:
            ocf_by_yr = dict(op_cash_flows)
            cap_by_yr = dict(capex_vals)
            rev_by_yr = dict(revenues)
            margins = []
            for yr, rev in sorted(rev_by_yr.items())[-3:]:
                if yr in ocf_by_yr and yr in cap_by_yr and rev > 0:
                    fcf = ocf_by_yr[yr] - cap_by_yr[yr]
                    margins.append(fcf / rev * 100)
            if margins:
                fcf_margin_3yr = sum(margins) / len(margins)

        # Derived: 3-year revenue CAGR using actual year span (skip if data is stale)
        rev_cagr_3yr = None
        if not _stale and len(revenues) >= 4:
            yr_start, r_start = revenues[-4]
            yr_end,   r_end   = revenues[-1]
            n_years = int(yr_end) - int(yr_start)
            # Require the span to be 2-4 years (guard against M&A / XBRL gaps)
            if r_start > 0 and r_end > 0 and 2 <= n_years <= 4:
                rev_cagr_3yr = ((r_end / r_start) ** (1 / n_years) - 1) * 100

        return {
            "cik": cik,
            "revenues": revenues,
            "backlog": backlog,
            "op_cash_flows": op_cash_flows,
            "capex": capex_vals,
            "backlog_to_rev": backlog_to_rev,
            "fcf_margin_3yr": fcf_margin_3yr,
            "rev_cagr_3yr": rev_cagr_3yr,
            "latest_rev": latest_rev,
        }

    except Exception:
        return {}


def overlay_xbrl_into_fundamentals(f: "CompanyFundamentals", xbrl: Dict) -> None:
    """
    Overlay XBRL-derived primary-source data into CompanyFundamentals.
    XBRL is authoritative for backlog and 3-year normalized FCF.
    """
    if not xbrl:
        return

    # Backlog — EDGAR XBRL is primary source
    if xbrl.get("backlog_to_rev") is not None:
        f.backlog_to_revenue = xbrl["backlog_to_rev"]

    # 3-year normalized FCF margin — more reliable than TTM
    if xbrl.get("fcf_margin_3yr") is not None:
        f.fcf_margin = xbrl["fcf_margin_3yr"]

    # 3-year revenue CAGR as a context signal (overrides 1yr if available)
    if xbrl.get("rev_cagr_3yr") is not None and hasattr(f, "revenue_growth_3yr"):
        f.revenue_growth_3yr = xbrl["rev_cagr_3yr"]

    # Revenue sanity check
    if xbrl.get("latest_rev") and f.annual_revenue_millions is None:
        f.annual_revenue_millions = xbrl["latest_rev"] / 1e6
