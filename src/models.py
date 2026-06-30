"""
Data models using stdlib dataclasses (no pydantic dependency).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum


class ContractType(str, Enum):
    NEW_AWARD = "New Award"
    MODIFICATION = "Modification"
    OPTION_EXERCISE = "Option Exercise"
    IDIQ = "IDIQ"
    DELIVERY_ORDER = "Delivery Order"
    TASK_ORDER = "Task Order"
    SOLE_SOURCE = "Sole Source"
    UNKNOWN = "Unknown"


class Sector(str, Enum):
    TRADITIONAL_DEFENSE_PRIME = "Traditional Defense Prime"
    AEROSPACE = "Aerospace"
    SHIPBUILDING = "Shipbuilding"
    SPACE = "Space"
    CYBERSECURITY = "Cybersecurity"
    AI_DATA_SOFTWARE = "AI / Data / Software"
    CLOUD_IT_SERVICES = "Cloud / IT Services"
    MILITARY_HEALTHCARE = "Military Healthcare"
    PHARMACEUTICAL_BIOTECH = "Pharmaceutical / Biotech"
    MEDICAL_DEVICES = "Medical Devices"
    LOGISTICS = "Logistics"
    INFRASTRUCTURE_CONSTRUCTION = "Infrastructure / Construction"
    ENERGY_NUCLEAR = "Energy / Nuclear"
    INDUSTRIAL_COMPONENTS = "Industrial Components"
    CONSULTING_SERVICES = "Consulting / Services"
    PRIVATE_NO_TICKER = "Private / No Public Ticker"
    UNCLEAR = "Unclear"


class Verdict(str, Enum):
    STRONG_CANDIDATE = "Strong Candidate"
    POTENTIALLY_ATTRACTIVE = "Potentially Attractive"
    WATCHLIST = "Watchlist"
    LOW_CONVICTION = "Low Conviction"
    IGNORE = "Ignore"
    HIGH_QUALITY_BUT_EXPENSIVE = "High Quality But Expensive"
    RESEARCH_FURTHER = "Research Further"


@dataclass
class Contract:
    awardee_name: str
    parent_company: Optional[str] = None
    ticker: Optional[str] = None
    ticker_confidence: float = 0.0
    contract_value: float = 0.0
    funded_amount: Optional[float] = None
    contract_type: ContractType = ContractType.UNKNOWN
    agency: Optional[str] = None
    branch: Optional[str] = None
    description: str = ""
    location: Optional[str] = None
    completion_date: Optional[str] = None
    award_date: Optional[str] = None
    is_sole_source: bool = False
    is_competitive: bool = False
    is_idiq: bool = False
    sector: Sector = Sector.UNCLEAR
    keywords: List[str] = field(default_factory=list)
    investment_relevance_notes: str = ""
    raw_text: str = ""
    # "Fixed-Price", "Cost-Plus", "T&M", "Other", None = unknown
    pricing_type: Optional[str] = None


@dataclass
class CompanyFundamentals:
    ticker: str
    company_name: str = ""
    market_cap_millions: Optional[float] = None
    annual_revenue_millions: Optional[float] = None
    government_revenue_pct: Optional[float] = None
    dod_revenue_pct: Optional[float] = None
    roe: Optional[float] = None
    roic: Optional[float] = None
    free_cash_flow_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    ev_ebitda: Optional[float] = None
    price_to_book: Optional[float] = None
    fcf_yield: Optional[float] = None
    debt_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    interest_coverage: Optional[float] = None
    backlog_to_revenue: Optional[float] = None
    net_debt_millions: Optional[float] = None
    debt_ebitda: Optional[float] = None
    insider_ownership_pct: Optional[float] = None
    insider_net_pct_6m: Optional[float] = None        # net % of held shares bought (positive=buying)
    earnings_stability_years: Optional[int] = None
    moat_rating: Optional[str] = None
    gross_margin: Optional[float] = None          # % gross profit / revenue
    revenue_growth_1yr: Optional[float] = None    # % YoY revenue growth (TTM)
    revenue_growth_forward: Optional[float] = None # % fwd revenue growth (analyst consensus avg of 0y/+1y)
    data_source: str = "mock"
    data_notes: str = ""
    current_price: Optional[float] = None
    shares_millions: Optional[float] = None
    # Analyst consensus (from yfinance)
    analyst_count: Optional[int] = None
    analyst_target_price: Optional[float] = None          # mean price target
    analyst_recommendation: Optional[str] = None          # "buy", "hold", "sell", "strong_buy"
    upside_to_target: Optional[float] = None              # % upside/downside to mean target
    # Price momentum
    price_52w_high: Optional[float] = None
    price_52w_low: Optional[float] = None
    pct_off_52w_high: Optional[float] = None              # negative = below 52w high
    return_1yr: Optional[float] = None                    # 1-year price return %
    # Margin trends (YoY change in percentage points; positive = expanding)
    operating_margin_delta: Optional[float] = None
    gross_margin_delta: Optional[float] = None
    # Short interest
    short_pct_of_float: Optional[float] = None     # % of float sold short
    short_ratio_days: Optional[float] = None        # days to cover at avg volume
    # Capital return
    dividend_yield: Optional[float] = None          # % annual dividend yield
    payout_ratio: Optional[float] = None            # % of earnings paid as dividends
    shares_chg_1yr_pct: Optional[float] = None      # % YoY change in diluted shares (neg = buyback)
    # Earnings calendar
    next_earnings_date: Optional[str] = None        # "YYYY-MM-DD"
    # Multi-year growth context
    revenue_cagr_3yr: Optional[float] = None        # 3-yr revenue CAGR %
    # Liquidity
    avg_daily_volume: Optional[float] = None        # 10-day avg volume in shares


@dataclass
class ComponentScore:
    raw: float
    weight: float
    explanation: str
    flags: List[str] = field(default_factory=list)


@dataclass
class CompanyScore:
    ticker: str
    company_name: str
    sector: Sector
    buffett_quality: ComponentScore
    graham_value: ComponentScore
    dod_stability: ComponentScore
    management: ComponentScore
    contract_catalyst: ComponentScore
    balance_sheet: ComponentScore
    final_score: float
    verdict: Verdict
    overall_explanation: str
    recent_contracts: List[Contract] = field(default_factory=list)
    why_it_matters: str = ""
    why_it_might_not_matter: str = ""
    key_risks: List[str] = field(default_factory=list)
    what_to_verify: List[str] = field(default_factory=list)
    red_flags: List[str] = field(default_factory=list)
    low_ticker_confidence: bool = False
    specialist: object = None  # SpecialistProfile, set after scoring
    dcf: object = None           # DCFResult, set after scoring
    data_completeness_pct: float = 0.0  # % of key fundamentals fields that are non-None


class SpecialistTierStatus(str, Enum):
    IN_TIER      = "In Tier"
    NEAR_TIER    = "Near Tier"
    LARGE_PRIME  = "Large Prime"
    TOO_SMALL    = "Too Small"
    LOW_GOV_CONC = "Low Gov Concentration"
    UNKNOWN      = "Unknown"


@dataclass
class SpecialistProfile:
    """
    Captures whether a company sits in the 'specialist sweet spot':
    mid-cap, high DoD revenue concentration, specialized/sole-source work
    that institutional coverage tends to underweight.
    """
    status: SpecialistTierStatus = SpecialistTierStatus.UNKNOWN
    market_cap_millions: Optional[float] = None
    dod_revenue_pct: Optional[float] = None
    contract_to_revenue_pct: Optional[float] = None
    is_sole_source: bool = False
    score_adjustment: float = 0.0
    rationale: str = ""
    analyst_coverage_note: str = ""


@dataclass
class MacroContext:
    """Live macro environment data fetched at run time."""
    ten_year_yield: Optional[float] = None      # % e.g. 4.53 — used as Rf proxy
    three_month_yield: Optional[float] = None   # % e.g. 5.25 — yield curve shape
    dcf_baseline_rf: float = 4.5                # Rf assumed in DCF base WACC (9%)
    rate_delta_pp: Optional[float] = None       # ten_year_yield − dcf_baseline_rf (pp)
    iv_impact_pct: Optional[float] = None       # approx % Δ in IVs from rate delta
    defense_budget_bn: float = 895.0            # FY2026 DoD topline ($B)
    defense_budget_growth_pct: float = 3.3      # YoY growth vs FY2025
    fetch_date: Optional[str] = None
    fetch_error: Optional[str] = None


@dataclass
class DCFResult:
    """Lightweight reference — full class lives in src/dcf.py"""
    ticker: str = ""
    verdict: str = ""
    central_estimate: Optional[float] = None
    fair_value_range_low: Optional[float] = None
    fair_value_range_high: Optional[float] = None
    margin_of_safety_base: Optional[float] = None
    implied_growth_rate: Optional[float] = None
    valuation_score: float = 50.0
    valuation_note: str = ""
    data_quality: str = "insufficient"
    caveats: List[str] = field(default_factory=list)
    discount_rate_base: float = 9.0
    discount_rate_adjustments: List[str] = field(default_factory=list)
    bear_iv: Optional[float] = None
    base_iv: Optional[float] = None
    bull_iv: Optional[float] = None
    bear_mos: Optional[float] = None
    bull_mos: Optional[float] = None
    bear_growth: Optional[float] = None
    base_growth: Optional[float] = None
    bull_growth: Optional[float] = None
    current_price: Optional[float] = None
