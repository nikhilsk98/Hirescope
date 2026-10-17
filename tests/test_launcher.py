"""The hirescope:// launcher: every link type, strict validation, nothing from a link ever reaches a shell, silent
except `interactive`. (Per-feature behavior is tested with each feature: queue, Bucket, status, Resume Builder tab.)"""
import random
import string
import subprocess

import pytest

from jobsearch import launcher, paths
from jobsearch.storage import db

from .resume_builder.conftest import insert_job

VALID = [
    "hirescope://queue?job=12", "hirescope://queue?job=12&regenerate=1", "hirescope://queue?list=bucket",
    "hirescope://queue?list=bucket&regenerate=1", "hirescope://cancel?job=12", "hirescope://interactive?job=12",
    "hirescope://bucket?job=12&op=add", "hirescope://bucket?job=12,13&op=remove",
    "hirescope://status?job=12&stage=applied", "hirescope://status?job=12&stage=applied&app=aX1&date=2026-10-17&v=3",
    "hirescope://status?app=aX1&stage=interviewing", "hirescope://status?job=12&stage=undo&app=aX1",
    "hirescope://status?job=12&stage=ready",
    *[f"hirescope://status?app=a1&stage={s}" for s in ("oa", "offer", "rejected", "ghosted", "withdrawn")],
]

INVALID = [
    ("hirescope://queue?job=one", "numeric"), ("hirescope://queue?job=1.5", "numeric"), ("hirescope://queue?job=", "numeric"),
    ("hirescope://queue?job=9999999999", "numeric"), ("hirescope://queue?list=all", "unknown value"),
    ("hirescope://queue?list=bucket&regenerate=true", "unknown value"), ("hirescope://cancel?job=1&regenerate=1", "unknown parameter"),
    ("hirescope://status?job=1&stage=Applied", "unknown value"), ("hirescope://status?job=1&stage=hired", "unknown value"),
    ("hirescope://bucket?job=1&op=delete", "unknown value"), ("hirescope://bucket?job=1&op=add&op=remove", "twice"),
    ("hirescope://interactive?job=1;open -a Calculator", "numeric"), ("hirescope://interactive?job=1%20&&%20id", "malformed"),
    ("hirescope://external?file=/etc/hosts", "only"), ("hirescope://external?file=..%2F..%2F.ssh%2Fid_rsa", "absolute path"),
    ("hirescope://external?file=x.json%00.pdf", "control"), ("hirescope://external?file=~/Documents/x.json", "only from"),
    ("hirescope://shell?cmd=id", "unknown action"), ("hirescope:///queue?job=1", "unexpected path"),
    ("javascript:alert(1)", "not a hirescope"), ("hirescope://queue?job=1&" + "&".join(f"x{i}=1" for i in range(20)), "malformed"),
    ("hirescope://queue?" + "job=1" + "1" * 9000, "too long"),
]


@pytest.mark.parametrize("url", VALID)
def test_every_link_type_parses(url):
    action, p = launcher.parse(url)
    assert action in launcher.ACTIONS and p


@pytest.mark.parametrize("url,why", INVALID)
def test_bad_links_are_rejected_with_a_short_error(url, why):
    with pytest.raises(launcher.LinkError, match=why) as e:
        launcher.parse(url)
    assert len(str(e.value)) < 120


def test_fuzzed_links_never_crash_and_never_run_anything(monkeypatch):
    """Random junk: parse() either validates or raises LinkError, nothing else, and nothing is executed."""
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: ran.append(a))
    rnd = random.Random(7)
    alphabet = string.printable + "éß😀%&=?#/\\;|$`'\""
    actions = list(launcher.ACTIONS) + ["x", ""]
    for _ in range(3000):
        tail = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 60)))
        url = f"hirescope://{rnd.choice(actions)}?{tail}"
        try:
            launcher.parse(url)
        except launcher.LinkError:
            pass
    assert ran == []


def test_link_text_never_reaches_a_shell(monkeypatch):
    conn = db.connect(paths.DB)
    pk = insert_job(conn, paths.DATA / "jd", company="DemoCorp", title="Backend Engineer Intern", job_id="l-1")
    conn.close()
    calls = []

    def rec(args, **kw):
        assert not kw.get("shell"), "shell=True is never used"
        calls.append(args)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    monkeypatch.setattr(subprocess, "run", rec)
    for url in (f"hirescope://queue?job={pk}", f"hirescope://bucket?job={pk}&op=add", f"hirescope://cancel?job={pk}",
                f"hirescope://status?job={pk}&stage=applied&app=aL1"):
        launcher.handle(url)
    assert not any("Terminal" in " ".join(map(str, c)) for c in calls)             # silent
    launcher.handle(f"hirescope://interactive?job={pk}")
    osa = calls[-1]
    assert osa[:2] == ["osascript", "-e"]
    line = launcher.interactive_command(pk)
    assert line.startswith("cd ") and line.endswith(f'&& claude "/resume-builder {pk}"') and str(paths.ROOT) in line


def test_main_exit_codes(capsys):
    assert launcher.main([]) == 2 and "exactly one" in capsys.readouterr().err
    assert launcher.main(["hirescope://queue?job=abc"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("HireScope: ") and "numeric" in err


def test_launcher_app_passes_the_link_as_one_quoted_argument():
    src = (paths.ROOT / "scripts" / "launcher" / "HireScopeLauncher.applescript").read_text()
    assert 'quoted form of theURL' in src and 'do shell script "/bin/zsh " & quoted form of' in src
    sh = (paths.ROOT / "scripts" / "launcher.sh").read_text()
    assert 'jobsearch.launcher "$1"' in sh and "eval" not in sh
