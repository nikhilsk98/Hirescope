"""Locks never override truthfulness: a locked claim the master no longer supports (Legacy "do not use", unverified /
pending / planned metrics, Excluded or missing evidence, any truth-check failure) is dropped with a notice. DEMO data."""
import json
import shutil

import pytest

from resume_builder import config as rc
from resume_builder import versions
from resume_builder.evidence import build_bank

from .conftest import FIX, needs_tex
from .test_versions import full_build, jd_of


@pytest.fixture
def bank_v4(rb_env):
    shutil.copy(FIX / "demo_master_v4.md", rc.MASTER_RESUME)
    shutil.copy(FIX / "demo_template_v4.tex", rc.TEMPLATE)
    return build_bank()


def _id(bank, start):
    return next(i.id for i in bank.items.values() if i.text.startswith(start))


def _bullet(bid, text, ev, entry="exp-x"):
    return {"section": "experience", "entry_id": entry, "id": bid, "text": text, "evidence_ids": ev, "priority": 1,
            "origin": "rewritten", "talking_points": []}


def test_locked_claims_the_master_marks_unusable_are_dropped_with_a_notice(bank_v4, tmp_path):
    order = _id(bank_v4, "Built an ordering platform")
    good = "Built an ordering platform for a DEMO bakery supplier using React, Spring Boot, and PostgreSQL, cutting " \
           "order-entry time by 30%."
    unverified = next(x for x in bank_v4.excluded if x["text"].startswith("Raised"))   # states the +45% metric
    locks = {"bullets": {
        "e:keep": _bullet("e:keep", good, [order]),
        "e:legacy": _bullet("e:legacy", "Built an ordering platform in React with 99% uptime", [order]),
        "e:unverified": _bullet("e:unverified", unverified["text"], [unverified["id"]]),
        "e:gone": _bullet("e:gone", "Built backend services across 20+ client deployments", ["exp-x-b7"]),
    }, "entries": {"exp-x": {"entry_id": "exp-x", "bullets": [
        _bullet("e:k2", good, [order]), _bullet("e:bad2", "Cut order-entry time by 30% at 10x scale", [order])]}},
        "sections": {}}
    jd = tmp_path / "job"
    jd.mkdir()
    versions.save_locks(jd, locks)

    kept, notices = versions.enforce_truth(jd, versions.load_locks(jd), bank_v4)
    assert set(kept["bullets"]) == {"e:keep"}                                   # a supported lock stays locked
    assert [b["id"] for b in kept["entries"]["exp-x"]["bullets"]] == ["e:k2"]
    assert versions.load_locks(jd) == kept                                      # dropped for good, not just this build
    text = "\n".join(notices)
    assert len(notices) == 4 and text.count("Locks never override truthfulness") == 4
    assert "99% is a figure the master marks legacy (do not use)" in text
    assert f"cites {unverified['id']}, which the master marks uses an unverified metric" in text
    assert "cites unknown evidence 'exp-x-b7'" in text
    assert "10x" in text
    hist = [json.loads(ln) for ln in (jd / "history.jsonl").read_text().splitlines()]
    assert {h["target"] for h in hist if h["action"] == "lock dropped (truth)"} == {"e:legacy", "e:unverified",
                                                                                    "e:gone", "e:bad2"}
    assert versions.enforce_truth(jd, versions.load_locks(jd), bank_v4)[1] == []   # nothing left to drop


def test_an_entry_lock_with_no_supported_bullet_left_is_removed(bank_v4, tmp_path):
    order = _id(bank_v4, "Built an ordering platform")
    locks = {"bullets": {}, "sections": {}, "entries": {"exp-x": {"entry_id": "exp-x", "bullets": [
        _bullet("e:1", "Built an ordering platform with 99% uptime", [order])]}}}
    kept, notices = versions.enforce_truth(tmp_path, locks, bank_v4)
    assert kept["entries"] == {} and len(notices) == 1


@needs_tex()
def test_regeneration_drops_an_untruthful_lock_and_builds_ready(rb_env):
    full_build(rb_env)
    jd = jd_of(rb_env)
    bid = versions._content_of(jd / "v1").experience[0].bullets[0].id
    versions.lock(jd, bid)
    locks = versions.load_locks(jd)
    locks["bullets"][bid]["text"] += ", serving 5,000+ daily users"        # a figure no evidence states
    versions.save_locks(jd, locks)

    out = full_build(rb_env, regenerate=True)
    assert out["status"] == "ready", out.get("reasons")
    assert any("Locks never override truthfulness" in w for w in out["prepare"]["analysis"]["lock_dropped"])
    assert bid not in versions.load_locks(jd)["bullets"]
    c = versions._content_of(jd / f"v{out['version']}")
    assert not any("5,000+" in b.text for _s, _e, b in c.all_bullets())
