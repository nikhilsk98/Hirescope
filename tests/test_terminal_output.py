"""Quiet terminal: the run summary and real failures on the terminal, everything else in the JSON log (--verbose: all)."""
import json
import logging

import pytest

from jobsearch import paths
from jobsearch import run as R
from jobsearch.pipeline import LOG_ONLY


@pytest.fixture
def logs(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "LOG_DIR", tmp_path)
    yield tmp_path
    logging.getLogger().handlers[:] = []


def emit():
    logging.getLogger("jobsearch").info("run summary: {\"new\": 3}")
    logging.getLogger("jobsearch").warning("MANUAL CHECK NEEDED: DemoCo https://demo.example (DEMO)", extra=LOG_ONLY)
    logging.getLogger("jobsearch.adapters.base").warning("GET https://demo.example failed (HTTP 503); retry 1 in 1.0s")
    logging.getLogger("jobsearch.adapters.base").error("DEMO adapter error")
    logging.getLogger("jobsearch").warning("INCOMPLETE COVERAGE: DemoCo (greenhouse) failed: HTTP 500")


def test_quiet_terminal_keeps_real_failures_and_logs_everything(logs, capsys):
    log_file = R.setup_logging(verbose=False)
    emit()
    err = capsys.readouterr().err
    assert "INCOMPLETE COVERAGE" in err and "DEMO adapter error" in err
    assert "run summary" not in err and "DemoCo https://" not in err and "retry 1" not in err
    logged = [json.loads(line)["msg"] for line in log_file.read_text().splitlines()]
    assert len(logged) == 5 and any(m.startswith("run summary") for m in logged)


def test_verbose_terminal_shows_everything(logs, capsys):
    R.setup_logging(verbose=True)
    emit()
    err = capsys.readouterr().err
    assert all(s in err for s in ("run summary", "DemoCo https://", "retry 1", "INCOMPLETE COVERAGE"))


def test_manual_companies_collapse_into_one_line():
    manual = [{"company": c} for c in ("Zeta", "alpha", "Beta", "Gamma", "Delta", "Eps", "Beta")]
    line = R.manual_line(manual)
    assert line.startswith("MANUAL CHECK NEEDED: 6 companies (alpha, Beta, Delta, Eps, Gamma and 1 more)")
    assert "\n" not in line
    assert R.manual_line([{"company": "DemoCo"}]).startswith("MANUAL CHECK NEEDED: 1 company (DemoCo);")
