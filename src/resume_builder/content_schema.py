"""The resume content contract: what the drafter (LLM) writes, validated before anything is rendered.

The LLM never writes LaTeX. It picks entries by id and writes bullet text, each bullet citing the evidence ids it
is based on. Facts that must not change (titles, employers, locations, dates, degrees, institutions) are not in the
JSON at all: the renderer takes them from the evidence bank. Unknown fields are rejected.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

SCHEMA_VERSION = 1
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_ID = r"^[a-z0-9][a-z0-9:-]{0,80}$"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _clean(v: str) -> str:
    if _CTRL.search(v):
        raise ValueError("control characters are not allowed")
    return re.sub(r"\s+", " ", v)


class Bullet(_M):
    id: str | None = Field(default=None, pattern=_ID)   # stable id; assigned on save when missing
    text: str = Field(min_length=10, max_length=400)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    priority: int = Field(default=3, ge=1, le=5)          # 1 = most important; trimming removes 5s first
    origin: Literal["rewritten", "verbatim", "locked", "manual"] = "rewritten"
    talking_points: list[str] = Field(default_factory=list, max_length=3)

    _t = field_validator("text")(lambda cls, v: _clean(v))


class ExperienceEntry(_M):
    entry_id: str = Field(pattern=r"^exp-")
    priority: int = Field(default=3, ge=1, le=5)
    title: str | None = Field(default=None, max_length=80)   # v4: one of the titles the master records for the role
    bullets: list[Bullet] = Field(min_length=1, max_length=6)


class ProjectEntry(_M):
    entry_id: str = Field(pattern=r"^proj-")
    priority: int = Field(default=3, ge=1, le=5)
    tech: list[str] | None = Field(default=None, max_length=6)   # title-line tech; default: as in master resume
    bullets: list[Bullet] = Field(min_length=1, max_length=4)

    @field_validator("tech")
    @classmethod
    def _tech(cls, v):
        return [_clean(t) for t in v] if v else v


class EducationEntry(_M):
    entry_id: str = Field(pattern=r"^edu-")
    course_ids: list[str] = Field(default_factory=list, max_length=6)
    detail_ids: list[str] = Field(default_factory=list, max_length=3)   # e.g. the GPA line


class SkillItem(_M):
    name: str = Field(min_length=1, max_length=80)        # a certification name can be long
    evidence_ids: list[str] = Field(min_length=1, max_length=6)

    _n = field_validator("name")(lambda cls, v: _clean(v))


class SkillCategory(_M):
    category: str = Field(min_length=2, max_length=30)
    items: list[SkillItem] = Field(min_length=1, max_length=12)


class Summary(_M):
    text: str = Field(min_length=20, max_length=400)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)

    _t = field_validator("text")(lambda cls, v: _clean(v))


class RequirementMap(_M):
    text: str = Field(max_length=600)
    kind: Literal["required", "preferred", "mentioned"] = "required"
    category: Literal["direct", "transferable", "missing_evidence", "gap", "needs_judgment"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    note: str = Field(default="", max_length=400)


class Notes(_M):
    selection: list[str] = Field(default_factory=list, max_length=20)   # why these projects / roles / bullets
    removed: list[str] = Field(default_factory=list, max_length=40)
    reviewer: list[str] = Field(default_factory=list, max_length=30)    # reviewer critique applied
    # self-reported skills Nikhil approved for this resume although the JD doesn't name them (rule 53; at most 4)
    approved_skills: list[str] = Field(default_factory=list, max_length=4)
    stage: Literal["baseline", "draft", "reviewed"] = "baseline"


Section = Literal["summary", "skills", "education", "experience", "projects"]


class Content(_M):
    schema_version: Literal[1] = SCHEMA_VERSION
    job_pk: int
    profile: str = Field(max_length=30)
    track: Literal["coop_intern", "full_time", "unclear"]
    section_order: list[Section]
    summary: Summary | None = None
    skills: list[SkillCategory] = Field(min_length=1, max_length=6)
    education: list[EducationEntry] = Field(min_length=1, max_length=4)
    experience: list[ExperienceEntry] = Field(default_factory=list, max_length=8)
    projects: list[ProjectEntry] = Field(default_factory=list, max_length=4)
    requirements: list[RequirementMap] = Field(default_factory=list, max_length=60)
    notes: Notes = Field(default_factory=Notes)
    contact_location: Literal["default", "relocation"] = "default"   # v4 rule 31: relocation line for remote /
    #                                                                   out-of-state jobs

    @field_validator("section_order")
    @classmethod
    def _order(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("section_order lists a section twice")
        for need in ("skills", "education", "experience", "projects"):
            if need not in v:
                raise ValueError(f"section_order is missing '{need}'")
        return v

    def all_bullets(self):
        for e in self.experience:
            for b in e.bullets:
                yield "experience", e.entry_id, b
        for p in self.projects:
            for b in p.bullets:
                yield "projects", p.entry_id, b


class ContentError(Exception):
    pass


def parse(data: str | dict) -> Content:
    try:
        obj = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError as e:
        raise ContentError(f"content is not valid JSON: {e}") from e
    try:
        return Content.model_validate(obj)
    except ValidationError as e:
        lines = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:25]]
        raise ContentError("content does not match the schema:\n  - " + "\n  - ".join(lines)) from e


def dump(content: Content) -> str:
    return json.dumps(content.model_dump(mode="json", exclude_none=False), indent=1, ensure_ascii=False)


def json_schema() -> dict:
    return Content.model_json_schema()
