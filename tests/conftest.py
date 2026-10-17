"""Shared DEMO fixtures. No network, no embedding model: FakeEmbedder hashes words into vectors."""
import hashlib
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from jobsearch import config as cfgmod
from jobsearch.adapters.base import HttpClient
from jobsearch.config import Company, Config
from jobsearch.extract.resume import parse_resume
from jobsearch.match.semantic import Embedder
from jobsearch.storage import db

FIXTURES = Path(__file__).parent / "fixtures"


class FakeEmbedder(Embedder):
    """Deterministic bag-of-words embedding so semantic scores are testable without a model."""

    def __init__(self, conn=None):
        super().__init__(conn, model_name="fake-bow")

    def _encode(self, texts):
        out = np.zeros((len(texts), 256), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in t.lower().split():
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1
            n = np.linalg.norm(out[i])
            if n:
                out[i] /= n
        return out


@pytest.fixture
def cfg() -> Config:
    return Config(
        prefs=cfgmod.load_preferences(FIXTURES / "demo_preferences.yaml"),
        companies=[Company(name="DemoCo", source="greenhouse", board="democo", tier=1)],
        title_rules=cfgmod.load_title_rules(),
        synonyms=cfgmod.load_synonyms(),
    )


@pytest.fixture
def resume(cfg):
    return parse_resume(FIXTURES / "demo_resume.md", cfg.synonyms)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def embedder(conn):
    return FakeEmbedder(conn)


def mock_client(handler) -> HttpClient:
    c = HttpClient(retries=2, min_interval=0, transport=httpx.MockTransport(handler))
    return c


@pytest.fixture
def greenhouse_payload():
    return json.loads((FIXTURES / "greenhouse_demo.json").read_text())


@pytest.fixture(autouse=True)
def desktop_sandbox(tmp_path, monkeypatch):
    """Resume Desktop copies (resume_builder.desktop) go to a temporary folder, never the real ~/Desktop."""
    monkeypatch.setenv("JOBSEARCH_DESKTOP_DIR", str(tmp_path / "Desktop" / "Generated Resumes"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("jobsearch.adapters.base.time.sleep", lambda s: None)


@pytest.fixture(autouse=True)
def unfreeze_data_paths():
    """paths.DB, JD_DIR, … are derived from paths.DATA when read. monkeypatch's undo writes them back as fixed values,
    which would pin later tests to the real data folder: drop those after every test."""
    yield
    from jobsearch import paths as _p
    for name in [*_p._UNDER_DATA, "SYNC_JS", "EXTERNAL_INBOX"]:
        _p.__dict__.pop(name, None)
