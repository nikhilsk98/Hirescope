"""Minimal RFC 9309 robots.txt matcher: longest matching rule wins, Allow wins ties, * and $ wildcards.

(urllib.robotparser applies rules in file order, so "Disallow: /" followed by "Allow: /careers"
wrongly blocks /careers.)
"""
from __future__ import annotations

import re


class Robots:
    def __init__(self, text: str, agent: str = "jobsearch-local"):
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        last_was_rule = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, val = (x.strip() for x in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if last_was_rule:
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(val.lower())
                last_was_rule = False
            elif key in ("allow", "disallow"):
                rules.append((key == "allow", val))
                last_was_rule = True
        if agents:
            groups.append((agents, rules))
        agent = agent.lower()
        mine = [r for a, r in groups if any(x != "*" and x in agent for x in a)]
        star = [r for a, r in groups if "*" in a]
        self.rules = [rule for g in (mine or star) for rule in g]

    @staticmethod
    def _matches(pattern: str, path: str) -> bool:
        if not pattern:
            return False
        rx = re.escape(pattern).replace(r"\*", ".*")
        if rx.endswith(r"\$"):
            rx = rx[:-2] + "$"
        return re.match(rx, path) is not None

    def allowed(self, path_and_query: str) -> bool:
        best: tuple[int, bool] | None = None
        for allow, pattern in self.rules:
            if self._matches(pattern, path_and_query):
                cand = (len(pattern), allow)
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and allow):
                    best = cand
        return True if best is None else best[1]
