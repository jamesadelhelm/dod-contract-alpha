"""
Configuration for DoD Contract Intelligence Agent.
"""

import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
SRC_DIR = BASE_DIR / "src"

TICKER_MAP_PATH = DATA_DIR / "ticker_map.yaml"
SAMPLE_CONTRACTS_PATH = DATA_DIR / "sample_contracts.json"
MOCK_FUNDAMENTALS_PATH = DATA_DIR / "mock_fundamentals.json"
REPORTS_DIR.mkdir(exist_ok=True)

# ── DoD Data Sources ──────────────────────────────────────────────────────────
DOD_CONTRACTS_URL = "https://www.defense.gov/News/Contracts/"
USASPENDING_API_BASE = "https://api.usaspending.gov/api/v2"

# ── Scoring Weights ───────────────────────────────────────────────────────────
SCORE_WEIGHTS = {
    "buffett_quality": 0.25,
    "graham_value": 0.20,
    "dod_stability": 0.20,
    "management": 0.15,
    "contract_catalyst": 0.10,
    "balance_sheet": 0.10,
}

# ── Verdict Thresholds ────────────────────────────────────────────────────────
# Calibrated for the defense / government services universe. Graham's original
# framework (1930s) assumed P/E ≤ 12x as "cheap". Defense primes legitimately
# trade at 18–30x — a 25x P/E Lockheed or General Dynamics earns 10/20 Graham
# P/E points vs. 20/20 for a net-net stock. The natural scoring ceiling for a
# quality defense company is ~72–78, not 85–90 like a consumer compounder with
# low multiples. Thresholds are set 5–7 pts lower than Graham-absolute baselines
# so the tool produces actionable signals within this universe.
VERDICT_THRESHOLDS = {
    "strong_candidate": 78,
    "potentially_attractive": 68,
    "watchlist": 58,
    "low_conviction": 48,
}

# ── Score Override Rules ──────────────────────────────────────────────────────
# If any of these conditions are met, the final score is capped
OVERRIDE_RULES = {
    "unprofitable_high_dilution_max_buffett": 45,
    "dangerous_balance_sheet_max_final": 65,
    "idiq_ceiling_only_max_catalyst": 40,
    "low_ticker_confidence_flag_threshold": 0.70,
}

# ── Specialist Tier Filter ────────────────────────────────────────────────────
# The "sweet spot" for this strategy: mid-cap, high gov concentration,
# specialized work that institutional coverage tends to underweight.
#
# Rationale: Large primes (LMT, NOC, RTX, GD) are extremely well-covered.
# Contract news for them is priced in within hours. The edge is in the tier
# below — companies where a $200M sole-source award is 15-20% of annual
# revenue and sell-side coverage is 3-8 analysts instead of 25+.
SPECIALIST_TIER = {
    # Market cap band (millions USD). Below floor = too small/illiquid.
    # Above ceiling = too large for contract news to move the needle.
    "market_cap_floor_millions": 400,
    "market_cap_ceiling_millions": 15_000,

    # DoD revenue concentration. Below this, the contract stream is a
    # sideshow relative to the company's core commercial business.
    "min_dod_revenue_pct": 35,

    # Contract value as % of annual revenue — the minimum for a contract
    # to be "meaningful" to a specialist-tier company's thesis.
    "min_contract_to_revenue_pct": 3.0,

    # Bonus added to final score for companies in the specialist sweet spot.
    # Intentionally modest — this is a tiebreaker, not a trump card.
    "score_bonus_in_tier": 6.0,

    # Partial credit for companies near the edges of the tier.
    "score_bonus_near_tier": 3.0,

    # Companies above market cap ceiling can still qualify if they have
    # a uniquely specialized, sole-source position (e.g. BWXT for naval nuclear).
    "sole_source_ceiling_override_millions": 25_000,
}

# Tickers that are definitionally large-cap primes — contract news is
# institutional knowledge. Used to suppress specialist bonus even if they
# somehow fit the size filter.
LARGE_CAP_PRIMES = {"LMT", "NOC", "RTX", "GD", "BA", "HII", "LHX", "TXT", "L3H"}

# ── Ticker → Sector Override ──────────────────────────────────────────────────
# The keyword classifier reads USAspending contract *descriptions*, which often
# don't reflect a company's primary sector. For example, a BAH intelligence
# contract says "reconnaissance support" → triggers Space keywords. A SAIC IT
# contract says "facility maintenance" → triggers Infrastructure keywords.
# These overrides apply AFTER the per-contract keyword vote and ensure the DCF
# growth assumptions, terminal rate, and stability score use the right sector.
TICKER_SECTOR_OVERRIDES = {
    # IT / analytics / consulting — descriptions rarely contain "cloud" or "IT services"
    "BAH":  "AI / Data / Software",       # analytics-dominant; intelligence ≠ space company
    "SAIC": "Cloud / IT Services",         # enterprise IT services, not construction
    "CACI": "Consulting / Services",       # government IT consulting
    "ACN":  "Cloud / IT Services",         # IT consulting
    "PLTR": "AI / Data / Software",        # data analytics platform
    "AMTM": "Consulting / Services",       # government operations/maintenance services
    # Defense primes misclassified as Unclear or wrong sector
    "RTX":  "Traditional Defense Prime",
    "BA":   "Aerospace",
    "TXT":  "Aerospace",
    "LHX":  "Traditional Defense Prime",
    "AVAV": "Aerospace",                   # UAS/drone manufacturer
    # Healthcare managed care
    "HUM":  "Military Healthcare",
    # Space / satellite comms
    "VSAT": "Space",
}

# ── Sector Keywords ───────────────────────────────────────────────────────────
SECTOR_KEYWORDS = {
    "Shipbuilding": [
        "submarine", "destroyer", "carrier", "frigate", "amphibious", "shipbuilding",
        "ship repair", "naval vessel", "DDG", "SSN", "SSBN", "LPD", "LHD", "CVN"
    ],
    "Aerospace": [
        "aircraft", "fighter", "bomber", "helicopter", "rotorcraft", "UAV", "drone",
        "F-35", "F-16", "B-21", "C-130", "V-22", "CH-47", "UH-60", "engine", "propulsion"
    ],
    "Space": [
        "satellite", "launch vehicle", "orbit", "space", "LEO", "GEO", "GPS", "SDA",
        "space domain", "missile warning", "SBIRS", "reconnaissance"
    ],
    "Cybersecurity": [
        "cyber", "endpoint", "detection", "EDR", "XDR", "zero trust", "network security",
        "vulnerability", "DISA", "information assurance", "encryption", "SIEM"
    ],
    "AI / Data / Software": [
        "artificial intelligence", "machine learning", "AI/ML", "data analytics",
        "algorithm", "predictive", "autonomous", "decision support", "software development",
        "data exploitation", "natural language"
    ],
    "Cloud / IT Services": [
        "cloud", "enterprise IT", "help desk", "ERP", "digital transformation",
        "IT services", "systems integration", "modernization", "C4I",
        "command control", "communications", "network services", "telecommunications",
        "telecom", "wireless", "cellular", "broadband", "connectivity", "FirstNet",
        "managed network", "voice services", "data services",
        "VPN", "VPNS", "dedicated access", "virtual private", "network access",
        "bandwidth", "circuit", "satellite communications", "SATCOM",
        "emergency preparedness", "priority service", "NSEP", "government communications"
    ],
    "Military Healthcare": [
        "TRICARE", "military healthcare", "DHA", "military treatment", "medical readiness",
        "beneficiaries", "managed care", "health services", "clinical"
    ],
    "Pharmaceutical / Biotech": [
        "vaccine", "pharmaceutical", "drug", "biological", "antiviral", "therapeutic",
        "biodefense", "MCM", "medical countermeasure", "BARDA", "pandemic"
    ],
    "Medical Devices": [
        "medical device", "diagnostic", "imaging", "laboratory", "detection instrument",
        "mass spectrometry", "biosensor", "point of care", "CBRN", "biological threat"
    ],
    "Logistics": [
        "logistics", "supply chain", "distribution", "transportation", "sustainment",
        "maintenance", "DLA", "DLTS", "warehousing", "depot"
    ],
    "Energy / Nuclear": [
        "nuclear", "reactor", "fuel", "propulsion", "enrichment", "radiological",
        "energy", "power generation", "NNSA"
    ],
    "Infrastructure / Construction": [
        "construction", "infrastructure", "hardening", "installation", "base operations",
        "facility", "engineering", "architect", "MILCON", "directed energy", "electronic warfare",
        "remediation", "remedial", "remedial action", "remedial design",
        "environmental", "cleanup", "contamination", "groundwater",
        "hazardous waste", "FUDS", "formerly used defense", "environmental services",
        "environmental restoration", "soil", "site cleanup", "decontamination",
        "superfund", "NPL site", "operable unit", "landfill", "mine waste",
        "demolition", "excavation", "site investigation", "property investigation"
    ],
    "Traditional Defense Prime": [
        "missile", "interceptor", "munition", "weapon system", "THAAD", "Patriot",
        "Javelin", "Stinger", "cruise missile", "hypersonic", "defense system"
    ],
    "Industrial Components": [
        "component", "parts", "hardware", "manufacturing", "MRO", "overhaul",
        "avionics", "electronics", "sensor"
    ],
}
