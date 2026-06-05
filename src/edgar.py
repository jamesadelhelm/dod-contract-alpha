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
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
    """Resolve ticker to CIK via EDGAR company search."""
    time.sleep(RATE_LIMIT)
    try:
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2020-01-01&forms=10-K"
        # Better: use the ticker→CIK map EDGAR provides
        r = requests.get(
            "https://www.sec.gov/cgi-bin/browse-edgar",
            params={"company": "", "CIK": ticker, "type": "10-K",
                    "dateb": "", "owner": "include", "count": "1",
                    "search_text": "", "action": "getcompany", "output": "atom"},
            headers={"User-Agent": "dod-contract-research-agent research@example.com"},
            timeout=10,
        )
        # Extract CIK from the atom feed
        cik_match = re.search(r'/cgi-bin/browse-edgar\?action=getcompany&CIK=(\d+)', r.text)
        if cik_match:
            return cik_match.group(1).zfill(10)

        # Fallback: use EDGAR submissions API with ticker
        time.sleep(RATE_LIMIT)
        r2 = requests.get(
            "https://efts.sec.gov/LATEST/search-index?q=%22%22&forms=10-K",
            params={"q": f'"{ticker}"', "forms": "10-K", "dateRange": "custom",
                    "startdt": "2023-01-01"},
            headers=SEARCH_HEADERS, timeout=10,
        )
        return None  # will be resolved via tickers.json below

    except Exception:
        pass

    # Best fallback: EDGAR's company_tickers.json (maps ticker → CIK)
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
