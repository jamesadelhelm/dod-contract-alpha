"""
Unit tests for src/classify_sector.py, focused on the awardee-name fallback
heuristic used when a contract's description has zero keyword hits.

Regression coverage for a bug where the fallback used plain substring checks
(`w in awardee`) instead of the word-boundary matching (`_kw_hit`) used
everywhere else in the file, causing false positives such as:
  - "Township Solutions Inc" -> Shipbuilding ("ship" substring of "township")
  - "Cybernetics Analytics LLC" -> Cybersecurity ("cyber" substring of
    "cybernetics")
"""
from src.classify_sector import classify_sector
from src.models import Contract, Sector


def _make_contract(awardee_name: str, description: str = "") -> Contract:
    return Contract(awardee_name=awardee_name, description=description)


class TestAwardeeFallbackHeuristic:
    def test_generic_awardee_name_stays_unclear(self):
        c = _make_contract("Township Solutions Inc", description="general administrative support")
        assert classify_sector(c) == Sector.UNCLEAR

    def test_cybernetics_name_does_not_false_positive_cybersecurity(self):
        c = _make_contract("Cybernetics Analytics LLC", description="general research support")
        assert classify_sector(c) != Sector.CYBERSECURITY

    def test_friendship_name_does_not_false_positive_shipbuilding(self):
        c = _make_contract("Friendship Logistics Group", description="general office supplies")
        assert classify_sector(c) != Sector.SHIPBUILDING

    def test_genuine_health_awardee_still_matches(self):
        c = _make_contract("Acme Medical Services Inc", description="general support services")
        assert classify_sector(c) == Sector.MILITARY_HEALTHCARE

    def test_genuine_naval_awardee_still_matches(self):
        c = _make_contract("Atlantic Marine Corporation", description="general support services")
        assert classify_sector(c) == Sector.SHIPBUILDING

    def test_genuine_cyber_awardee_still_matches(self):
        c = _make_contract("Cyber Defense Group LLC", description="general support services")
        assert classify_sector(c) == Sector.CYBERSECURITY

    def test_security_solutions_phrase_still_matches(self):
        c = _make_contract("Apex Security Solutions LLC", description="general support services")
        assert classify_sector(c) == Sector.CYBERSECURITY


class TestKeywordDescriptionPass:
    def test_submarine_description_classifies_shipbuilding(self):
        c = _make_contract("Generic Corp", description="Virginia-class submarine maintenance support")
        assert classify_sector(c) == Sector.SHIPBUILDING

    def test_engineering_description_does_not_false_positive_aerospace(self):
        # "engine" is an Aerospace keyword; word-boundary matching must not
        # fire on "engineering".
        c = _make_contract("Generic Corp", description="civil engineering and site design services")
        assert classify_sector(c) != Sector.AEROSPACE
