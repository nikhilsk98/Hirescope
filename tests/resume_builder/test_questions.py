"""--queue builds never ask: keyword questions go to "Questions for you"; answers close them everywhere."""
import json

import pytest

from jobsearch import paths as hs_paths
from jobsearch.storage import db
from resume_builder import config as rc
from resume_builder import hirescope_bridge, keywords, questions
from resume_builder.__main__ import main

from .test_rules_v3 import CHECKLIST, _job, _kw

KWS = {"coverage": 40.0, "required_hit": 0, "required_total": 2, "keywords": [
    _kw("Kafka"), _kw("Travis CI"), _kw("Java", status="matched", category="skills")]}


def _open(pk):
    conn = db.connect(hs_paths.DB)
    try:
        return questions.open_count(conn, pk)
    finally:
        conn.close()


def test_prepare_queue_saves_questions_and_never_asks(rb_env, capsys):
    rc.TOOL_CHECKLIST.write_text(CHECKLIST)
    pk = _job(rb_env, company="Q Co", title="Backend Engineer Intern", job_id="demo-q", keywords=KWS)
    assert main(["prepare", str(pk), "--queue"]) == 0
    out = capsys.readouterr().out
    assert "queue mode:" in out and "saved for Nikhil" in out and "ask Nikhil" not in out.replace("saved for Nikhil", "")
    assert _open(pk) == 2
    groups = questions.dashboard_list(db.connect(hs_paths.DB))
    assert {g["keyword"] for g in groups if g["open"]} == {"Kafka", "Travis CI"}
    # a second job asking the same keyword: one entry per keyword, both jobs listed
    pk2 = _job(rb_env, company="Q Two", title="Backend Engineer Intern", job_id="demo-q2", keywords=KWS)
    main(["prepare", str(pk2), "--queue"])
    kafka = next(g for g in questions.dashboard_list(db.connect(hs_paths.DB)) if g["keyword"] == "Kafka")
    assert {j["job_pk"] for j in kafka["jobs"]} == {pk, pk2}
    # resumes.js carries the open questions (dashboard view) and "Ready (questions pending)" counts per job
    hirescope_bridge.set_status(pk, "ready")
    data = json.loads(hirescope_bridge.write_dashboard_data().read_text().split("window.RESUMES = ", 1)[1].rstrip().rstrip(";"))
    assert any(g["keyword"] == "Kafka" for g in data["questions"]) and data["entries"]
    assert data["jobs"][str(pk)]["questions_pending"] == 2


def test_answers_close_questions_in_every_job(rb_env, capsys):
    rc.TOOL_CHECKLIST.write_text(CHECKLIST)
    pk = _job(rb_env, company="Q Co", title="Backend Engineer Intern", job_id="demo-q", keywords=KWS)
    pk2 = _job(rb_env, company="Q Two", title="Backend Engineer Intern", job_id="demo-q2", keywords=KWS)
    for p in (pk, pk2):
        main(["prepare", str(p), "--queue"])
    conn = db.connect(hs_paths.DB)
    kafka = next(g for g in questions.dashboard_list(conn) if g["keyword"] == "Kafka")
    out = questions.answer(kafka["ids"][0], yes=False)
    assert out["answer"] == "no" and set(out["jobs"]) == {pk, pk2}
    assert _open(pk) == 1 and _open(pk2) == 1
    assert keywords.answers()["kafka"]["answer"] == "no"          # never asked again (rule 61)
    # an answer saved any other way (add-evidence / add-tool / decline / an interactive session) closes it too
    keywords.record_answer("Travis CI", "saved")
    assert _open(pk) == 0 and _open(pk2) == 0
    assert main(["questions"]) == 0 and "No open questions" in capsys.readouterr().out
    main(["questions", "--all"])
    assert "answered no" in capsys.readouterr().out
    # the next prepare doesn't ask answered keywords again
    main(["prepare", str(pk), "--queue", "--force", "--regenerate"])
    assert _open(pk) == 0


def test_cli_answer_needs_explicit_confirmation(rb_env, capsys):
    rc.TOOL_CHECKLIST.write_text(CHECKLIST)
    pk = _job(rb_env, company="Q Co", title="Backend Engineer Intern", job_id="demo-q", keywords=KWS)
    main(["prepare", str(pk), "--queue"])
    qid = next(g for g in questions.dashboard_list(db.connect(hs_paths.DB)) if g["keyword"] == "Kafka")["ids"][0]
    assert main(["questions", "answer", str(qid), "--yes", "--entry", "exp-demo", "--answer", "Kafka consumers"]) == 2
    assert "confirm" in capsys.readouterr().err
    assert main(["questions", "answer", str(qid), "--yes", "--entry", "exp-demo", "--answer",
                 "Built Kafka consumers for the order pipeline", "--level", "production", "--confirm"]) == 0
    assert "Regenerate them" in capsys.readouterr().out
    assert _open(pk) == 1                                        # Travis CI still open


def test_answer_link_saves_and_closes_everywhere(rb_env):
    from jobsearch import launcher
    rc.TOOL_CHECKLIST.write_text(CHECKLIST)
    pk = _job(rb_env, company="Q Co", title="Backend Engineer Intern", job_id="demo-q", keywords=KWS)
    main(["prepare", str(pk), "--queue"])
    g = {x["keyword"]: x for x in questions.dashboard_list(db.connect(hs_paths.DB))}
    for bad, why in ((f"hirescope://answer?q={g['Kafka']['ids'][0]}&a=yes", "needs entry"),
                     ("hirescope://answer?q=1&a=maybe", "unknown value"), ("hirescope://answer?q=1&a=yes&entry=x&text=" + "a" * 4001, "longer"),
                     ("hirescope://answer?q=1&a=yes&entry=../x&text=hi", "entry must be"),
                     ("hirescope://answer?q=1&a=yes&entry=exp-demo&level=expert&text=hi", "level must be")):
        with pytest.raises(launcher.LinkError, match=why):
            launcher.parse(bad)
    from urllib.parse import quote
    msg = launcher.handle(f"hirescope://answer?q={g['Kafka']['ids'][0]}&a=yes&entry=exp-demo&level=production&text="
                          + quote("Built Kafka consumers for the order pipeline\nwith retries"))
    assert "saved your answer (yes) for Kafka" in msg
    assert "Kafka consumers" in rc.ADDITIONS.read_text()
    launcher.handle(f"hirescope://answer?q={g['Travis CI']['ids'][0]}&a=no")
    assert _open(pk) == 0
    data = json.loads((hs_paths.DASHBOARD / "resumes.js").read_text().split("window.RESUMES = ", 1)[1].rstrip().rstrip(";"))
    assert not any(x["open"] for x in data["questions"])
