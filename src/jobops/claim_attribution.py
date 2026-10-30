"""Conservative literal actor checks, not a general entailment classifier.

Only explicit subject substitutions and a bounded passive-to-active grammar are
recognized. Unknown wording still requires source review; no statement is fixed
or actor inferred by this module.
"""
from __future__ import annotations

import re
import unicodedata

ACTORS = {"applicant": "applicant", "candidate": "applicant", "i": "applicant", "me": "applicant",
          "申请人": "applicant", "本人": "applicant", "team": "team", "团队": "team", "ai": "ai"}
EN_ACTIONS = "marked|labelled|labeled|classified|recorded|reviewed|approved|prepared|completed|verified"
ZH_ACTIONS = "标注为|标记为|标为|归类为|记录为"


def explicit_actor_actions(text: str) -> list[tuple[str, str]]:
    """Retain the existing same-clause rule for direct actor substitutions."""
    result = []
    for clause in re.split(r"[;；。！？!?\n]|\.(?=\s|$)", unicodedata.normalize("NFKC", text).casefold()):
        clause = " ".join(clause.strip().split())
        match = re.match(r"^(?:the )?(applicant|candidate|team|ai|i)\s+(.+)$", clause)
        if match is None:
            match = re.match(r"^(申请人|本人|团队|ai)(?=[\u4e00-\u9fff]|\s)\s*(.+)$", clause)
        if match is not None:
            result.append((ACTORS[match[1]], match[2]))
    return result


def explicit_attribution_conflict_count(statement: str, source_excerpt: str) -> int:
    source_actors: dict[str, set[str]] = {}
    for actor, action in explicit_actor_actions(source_excerpt):
        source_actors.setdefault(action, set()).add(actor)
    return sum(action in source_actors and actor not in source_actors[action]
               for actor, action in explicit_actor_actions(statement))


def _action_key(action: str) -> str:
    return {"labeled": "labelled", "标注为": "mark", "标记为": "mark", "标为": "mark",
            "归类为": "classify", "记录为": "record"}.get(action, action)


def unsupported_passive_actor_count(statement: str, source_excerpt: str) -> int:
    """Reject named performers added to recognized unassigned passive actions.

    A matching explicitly active source clause is sufficient support. Otherwise
    every passive occurrence of that action in the cited excerpt must credit
    the same actor; a broad mixed citation needs narrower evidence. This does
    not recognize synonyms outside the bounded verb list or implicit subjects.
    """
    source = unicodedata.normalize("NFKC", source_excerpt).casefold()
    source = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", source)
    source = " ".join(source.split())
    passives: dict[str, list[str | None]] = {}
    for clause in re.split(r"[;；。！？!?]|\.(?=\s|$)", source):
        for match in re.finditer(r"\b(?:was|were|is|are|been|being|be)\s+"
                                r"(?:(?:not|never|also|already|previously)\s+)*(" + EN_ACTIONS + r")\b", clause):
            # A by-agent in a later coordinated action cannot assign this one.
            tail = re.split(r"\b(?:and|but|while|whereas)\b", clause[match.end():], maxsplit=1)[0]
            agent = re.search(r"\bby\s+(?:the\s+)?(applicant|candidate|team|ai|me)\b", tail)
            passives.setdefault(_action_key(match[1]), []).append(ACTORS[agent[1]] if agent else None)
        for match in re.finditer(r"被\s*(?:(申请人|本人|团队|ai)\s*)?(" + ZH_ACTIONS + r")", clause):
            passives.setdefault(_action_key(match[2]), []).append(ACTORS.get(match[1]))
    direct = set(explicit_actor_actions(source_excerpt))
    conflicts = 0
    for actor, action in explicit_actor_actions(statement):
        if (actor, action) in direct:
            continue
        active = re.match(r"^(?:(?:personally|solely|independently|also)\s+)*"
                          r"(?:(?:has|had)\s+)?(" + EN_ACTIONS + r")\b", action)
        if active is None:
            active = re.search(r"^(?:将|把).+?(" + ZH_ACTIONS + r")", action)
        if active is None:
            continue
        agents = passives.get(_action_key(active[1]), [])
        conflicts += bool(agents and any(credited != actor for credited in agents))
    return conflicts
