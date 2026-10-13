"""Local sentence-embedding similarity between resume sections and a JD. No network after model download."""
from __future__ import annotations

import hashlib
import os
import logging
import re
import sqlite3
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
SECTIONS = ("experience", "projects", "coursework")

# MiniLM cosine for related-but-different sentences sits ~0.15; strong paraphrases ~0.6+.
# Linear map of that band to 0-100. Heuristic, documented in the README.
SIM_FLOOR, SIM_CEIL = 0.15, 0.60
TOP_K = 8
MAX_JD_CHUNKS = 40


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]


class Embedder:
    """sentence-transformers on MPS when available, with a SQLite vector cache keyed by text hash."""

    def __init__(self, conn: sqlite3.Connection | None, model_name: str = DEFAULT_MODEL):
        self.conn = conn
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            import torch
            from sentence_transformers import SentenceTransformer
            device = "mps" if torch.backends.mps.is_available() else "cpu"
            log.info("loading embedding model %s on %s", self.model_name, device)
            try:  # use the local cache; only contact Hugging Face on the very first run
                self._model = SentenceTransformer(self.model_name, device=device, local_files_only=True)
            except OSError:
                log.info("model not cached; downloading %s from huggingface.co (one time)", self.model_name)
                self._model = SentenceTransformer(self.model_name, device=device)
        return self._model

    def _encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self._load().encode(texts, batch_size=64, normalize_embeddings=True,
                                              show_progress_bar=False), dtype=np.float32)

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        keys = [_h(t) for t in texts]
        cached: dict[str, np.ndarray] = {}
        if self.conn is not None:
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                q = f"SELECT text_hash, vector FROM embeddings WHERE model=? AND text_hash IN ({','.join('?' * len(chunk))})"
                for row in self.conn.execute(q, [self.model_name, *chunk]):
                    cached[row[0]] = np.frombuffer(row[1], dtype=np.float32)
        todo = list(dict.fromkeys(t for t, k in zip(texts, keys) if k not in cached))
        if todo:
            vecs = self._encode(todo)
            for t, v in zip(todo, vecs):
                cached[_h(t)] = v
            if self.conn is not None:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO embeddings (text_hash, model, vector) VALUES (?,?,?)",
                    [(_h(t), self.model_name, v.tobytes()) for t, v in zip(todo, vecs)],
                )
        return np.stack([cached[k] for k in keys])


def jd_chunks(description: str, min_reqs: list[str], pref_reqs: list[str]) -> list[str]:
    """Requirement lines when the JD is structured; otherwise its substantive lines."""
    reqs = [r for r in [*min_reqs, *pref_reqs] if len(r) >= 20]
    if len(reqs) >= 3:
        return reqs[:MAX_JD_CHUNKS]
    lines = [re.sub(r"^- ", "", ln).strip() for ln in description.splitlines()]
    return [ln for ln in lines if len(ln) >= 40][:MAX_JD_CHUNKS]


@dataclass
class Evidence:
    jd_line: str
    resume_line: str
    section: str
    similarity: float


@dataclass
class SemanticResult:
    score: float | None                     # 0-100 overall, None if not computable
    sections: dict[str, float | None]       # per-section 0-100
    evidence: list[Evidence]


def _scale(raw: float, lo: float = SIM_FLOOR, hi: float = SIM_CEIL) -> float:
    return float(np.clip((raw - lo) / (hi - lo), 0, 1) * 100)


def _coverage(sim: np.ndarray) -> float:
    """sim: (jd_chunks, resume_lines). Mean of the top-K per-JD-line best matches."""
    best = sim.max(axis=1)
    k = min(TOP_K, len(best))
    return float(np.sort(best)[-k:].mean())


def semantic_match(embedder: Embedder, resume_lines: dict[str, list[str]], chunks: list[str],
                   scale: tuple[float, float] | list[float] = (SIM_FLOOR, SIM_CEIL)) -> SemanticResult:
    """scale: preferences scoring.semantic_scale (cosine at 0 / at 100)."""
    lines = [(sec, ln) for sec in SECTIONS for ln in resume_lines.get(sec, []) if len(ln) >= 10]
    if not chunks or not lines:
        return SemanticResult(None, {s: None for s in SECTIONS}, [])
    jd_vecs = embedder.embed(chunks)
    rs_vecs = embedder.embed([ln for _, ln in lines])
    sim = jd_vecs @ rs_vecs.T

    sections: dict[str, float | None] = {}
    for sec in SECTIONS:
        idx = [i for i, (s, _) in enumerate(lines) if s == sec]
        sections[sec] = round(_scale(_coverage(sim[:, idx]), scale[0], scale[1]), 1) if idx else None

    evidence, used = [], set()
    for j in np.argsort(-sim.max(axis=1)):
        i = int(sim[j].argmax())
        if i in used:
            continue
        used.add(i)
        evidence.append(Evidence(chunks[j], lines[i][1], lines[i][0], round(float(sim[j, i]), 3)))
        if len(evidence) == 3:
            break
    return SemanticResult(round(_scale(_coverage(sim), scale[0], scale[1]), 1), sections, evidence)
