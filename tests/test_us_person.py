"""U.S. person / export-control requirements: hard ones filtered on both tracks (work_authorization.us_person),
scoped or hedged ones kept with a "Check eligibility: export control" signal, EEO boilerplate ignored."""
import pytest

from jobsearch.extract.flags import extract_flags
from jobsearch.match.rules import apply_rules

from .test_match import job
from .test_phase3 import NOW, flag

PERSON = "US person required (export control)"

HARD = [
    # "requires a U.S. Person" (RTX, job 35173)
    "U.S. Citizen, U.S. Person, or Immigration Status Requirements:\nThis job requires a U.S. Person. A U.S. Person "
    "is a lawful permanent resident as defined in 8 U.S.C. 1101(a)(20) or who is a protected individual as defined "
    "by 8 U.S.C. 1324b(a)(3).",
    "Applicants must be a US person as defined in 22 CFR 120.62.",                       # 22 CFR 120.62
    "Candidates must qualify as a U.S. person under 22 C.F.R. 120.15.",                  # 22 CFR 120.15 (old)
    "Candidates must be a U.S. citizen, lawful permanent resident, or protected individual as defined by "
    "8 U.S.C. 1324b(a)(3).",                                                              # 8 U.S.C. 1324b(a)(3)
    "This position is subject to ITAR.",                                                  # ITAR
    "This role requires compliance with EAR regulations.",                                # EAR
    "This position requires access to export-controlled technology.",                     # export controlled
    "Due to export control regulations, applicants are required to be U.S. persons.",     # export control
    "Must be a U.S. citizen or permanent resident.",                                      # citizen or PR
    "Green card holders and U.S. citizens only.",                                         # green card holder
    "Applicants must be lawful permanent residents or U.S. citizens.",                    # lawful permanent resident
    "Candidates must be a protected individual under U.S. immigration law.",              # protected individual
]


@pytest.mark.parametrize("text", HARD)
def test_hard_us_person_requirements(text):
    f = extract_flags(text)
    assert f["citizenship"]["value"] == PERSON, text
    assert f["citizenship"]["evidence"] and len(f["citizenship"]["evidence"]) <= 240


def test_rtx_quote_is_the_requirement_sentence():
    f = extract_flags(HARD[0])
    assert f["citizenship"]["evidence"] == "This job requires a U.S. Person."


@pytest.mark.parametrize("text", [
    "All qualified applicants will receive consideration for employment without regard to race, color, religion, sex, "
    "national origin, citizenship status, disability, or protected veteran status.",
    "We do not discriminate on the basis of citizenship status, in accordance with 8 U.S.C. 1324b.",
    "Equal Opportunity Employer, including disability and veterans; citizenship status is a protected class.",
    "Must be a U.S. citizen, permanent resident, or otherwise authorized to work in the U.S.",
    "Experience with ITAR compliance processes is a plus.",
    "We have grown 30% each YEAR since 2019.",
])
def test_not_requirements(text):
    f = extract_flags(text)
    assert f["citizenship"]["value"] == "Not stated" and f["export_control"]["value"] == "Not stated", text


@pytest.mark.parametrize("text", [
    "This position may require access to export-controlled information.",
    "Candidates may need to meet export requirements.",
    "For positions requiring access to ITAR data, candidates must be U.S. persons.",
    "Some projects might involve EAR-controlled technical data.",
])
def test_ambiguous_export_control_is_flagged_not_required(text):
    f = extract_flags(text)
    assert f["citizenship"]["value"] == "Not stated", text
    assert f["export_control"]["value"] == "Possibly required (export control)" and f["export_control"]["evidence"]


def test_eeo_next_to_a_real_requirement_keeps_the_requirement():
    f = extract_flags("We are an equal opportunity employer regardless of citizenship status. "
                      "This job requires a U.S. Person.")
    assert f["citizenship"]["value"] == PERSON and f["citizenship"]["evidence"] == "This job requires a U.S. Person."


def _prefs(cfg, us_person=False, us_citizen=False):
    p = cfg.prefs.model_copy(deep=True)
    p.work_authorization.us_citizen = us_citizen
    p.work_authorization.us_person = us_person
    return p


@pytest.mark.parametrize("track,title", [("full_time", "software engineer"), ("coop_intern", "software engineer co-op")])
def test_us_person_requirement_filtered_on_both_tracks_with_quote(cfg, track, title):
    j = job(track=track, normalized_title=title, flags={"citizenship": flag(PERSON, "This job requires a U.S. Person.")})
    r = apply_rules(j, _prefs(cfg), cfg.title_rules, NOW)
    assert r.hidden and "This job requires a U.S. Person." in r.reasons[0]
    assert not apply_rules(j, _prefs(cfg, us_person=True, us_citizen=False), cfg.title_rules, NOW).hidden
    assert apply_rules(j, _prefs(cfg, us_person=None, us_citizen=False), cfg.title_rules, NOW).hidden   # follows us_citizen


@pytest.mark.parametrize("track,title", [("full_time", "software engineer"), ("coop_intern", "software engineer co-op")])
def test_possible_export_control_kept_with_check_eligibility(cfg, track, title):
    ev = "This position may require access to export-controlled information."
    j = job(track=track, normalized_title=title,
            flags={"export_control": flag("Possibly required (export control)", ev)})
    r = apply_rules(j, _prefs(cfg), cfg.title_rules, NOW)
    assert not r.hidden and r.signals["check_eligibility"] == f"Export control — “{ev}”"
    assert "check_eligibility" not in apply_rules(j, _prefs(cfg, us_person=True), cfg.title_rules, NOW).signals


# --- real postings from the 2026-10-16 rescore -------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "- Work Authorization: Must be a US Citizen or Permanent Resident (Green Card holder).",            # label ≠ open
    "· US work authorization required; candidates must be a U.S. citizen, permanent resident, or green card holder.",
    "If this role is based in the United States, it requires access to export-controlled information or items that "
    "require “U.S. Person” status.",                                                                    # quoted, U.S.-based
    "- This position is restricted to US citizens or lawful permanent residents",
    # the ITAR boilerplate's licence route is still a requirement in practice
    "To conform to U.S. Government space technology export regulations, including the International Traffic in Arms "
    "Regulations (ITAR) you must be a U.S. citizen, lawful permanent resident of the U.S., protected individual as "
    "defined by 8 U.S.C. 1324b(a)(3), or eligible to obtain the required authorizations from the U.S. Department of "
    "State.",
    "- Applicants must be either a U.S. citizen, U.S. national, legal permanent resident, asylee, refugee, or eligible "
    "to apply for and obtain the appropriate export control license from the U.S. Department of State.",
    "As such, applicants must be eligible to access export-controlled information as defined under applicable law.",
])
def test_real_hard_requirements(text):
    assert extract_flags(text)["citizenship"]["value"] == PERSON, text


@pytest.mark.parametrize("text", [
    "As such, applicants for this position – except US Citizens, US Permanent Residents, and protected individuals as "
    "defined by 8 U.S.C. 1324b(a)(3) – may have to go through an export review process.",
    "To conform to US export control regulations, candidates should be eligible for any required authorizations from "
    "the US government.",
    # sanctioned-country boilerplate
    "Elasticsearch develops and distributes technology and information that is subject to U.S. and other countries’ "
    "export controls and licensing requirements for individuals who are nationals of sanctioned countries.",
])
def test_real_ambiguous_cases_are_flagged(text):
    f = extract_flags(text)
    assert f["citizenship"]["value"] == "Not stated" and f["export_control"]["value"].startswith("Possibly"), text


def test_export_control_as_a_skill_is_ignored():
    f = extract_flags("- A security-first mindset with experience implementing Zero Trust, least-privilege access, and "
                      "compliance frameworks such as SOC2, FedRAMP, or ITAR.")
    assert f["citizenship"]["value"] == "Not stated" and f["export_control"]["value"] == "Not stated"


@pytest.mark.parametrize("text", [
    "- Permanently authorized to work in the U.S., must not require sponsorship of an employment visa (e.g., H-1B or "
    "green card) at the time of application or in the future.",
    "This includes, but IS NOT LIMITED TO: F1-OPT, F1-CPT, H-1B, TN, L-1, J-1, etc. For additional information around "
    "work authorization needs please refer to the following resources:Nonimmigrant Workers and Green Card for "
    "Employment-Based Immigrants",
])
def test_sponsorship_statements_naming_green_cards_are_not_us_person_rules(text):
    f = extract_flags(text)
    assert f["citizenship"]["value"] == "Not stated" and f["export_control"]["value"] == "Not stated", text
