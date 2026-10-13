"""Resume Builder for HireScope: tailored one-page LaTeX resumes per job, from your own evidence only.

The LLM (Claude Code, through the /resume-builder skill) does the judgment: requirement mapping, selection,
rewriting and review, always as structured JSON. These modules do everything deterministic: evidence bank, JD
analysis, schema validation, truth verification, LaTeX rendering, Tectonic compilation, PDF validation, reports,
versions and the HireScope status/dashboard bridge.
"""
