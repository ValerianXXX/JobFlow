"""Bounded literal source-status checks, never a general entailment verdict.

Recognize bounded literal subjects, selected action inflections, negation and
explicit proposal/capability scope. Keep objects and qualifiers in the key: shared
words alone cannot establish a contradiction. Unknown syntax is left to source
review. Nothing here generates, repairs, selects or approves a Claim.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .claim_attribution import ACTORS


_VERB_FORMS = (
    ("manage", "manages", "managed", "managing"),
    ("process", "processes", "processed", "processing"),
    ("approve", "approves", "approved", "approving"),
    ("complete", "completes", "completed", "completing"),
    ("deploy", "deploys", "deployed", "deploying"),
    ("test", "tests", "tested", "testing"),
    ("review", "reviews", "reviewed", "reviewing"),
    ("publish", "publishes", "published", "publishing"),
    ("prepare", "prepares", "prepared", "preparing"),
    ("verify", "verifies", "verified", "verifying"),
    ("handle", "handles", "handled", "handling"),
    ("build", "builds", "built", "building"),
    ("write", "writes", "wrote", "written", "writing"),
    ("generate", "generates", "generated", "generating"),
    ("implement", "implements", "implemented", "implementing"),
    ("collect", "collects", "collected", "collecting"),
    ("lead", "leads", "led", "leading"),
    ("deliver", "delivers", "delivered", "delivering"),
    ("launch", "launches", "launched", "launching"),
    ("own", "owns", "owned", "owning"),
    ("record", "records", "recorded", "recording"),
)
_EN_VERBS = {form: forms[0] for forms in _VERB_FORMS for form in forms}
_EN_ACTION = "|".join(sorted(_EN_VERBS, key=len, reverse=True))
_ZH_ACTION = "负责|完成|核验|核对|处理|管理|批准|部署|开发|检查|编写|发布|交付|实施|记录|测试|审批|支付"
_ACTOR = r"(?:the\s+)?(applicant|candidate|team|ai|i|me|申请人|本人|团队)"
_ZH_LITERAL_ACTOR = r"[\u4e00-\u9fff]{0,12}?(?:经理|主管|负责人|同事|客户|团队|供应商|承包商)"
_EN_ACTOR_ROLES = {"manager", "analyst", "vendor", "colleague", "contractor", "engineer",
                   "researcher", "supervisor", "director", "reviewer", "author", "client",
                   "customer", "supplier", "developer", "designer", "team", "operator"}
_NOMINAL_ACTIONS = {
    "deployment": "deploy", "implementation": "implement", "completion": "complete",
    "publication": "publish", "approval": "approve", "delivery": "deliver",
    "review": "review", "management": "manage", "processing": "process",
    "testing": "test", "preparation": "prepare", "verification": "verify",
    "collection": "collect", "launch": "launch", "generation": "generate",
}
_ACTOR_STOP_WORDS = set(_EN_VERBS) | {
    "and", "or", "but", "while", "after", "before", "when", "although", "if", "unless",
    "which", "who", "that", "whose", "whether", "would", "could", "should", "may", "might",
    "not", "no", "never", "to", "for", "by", "of", "in", "on", "at", "during", "with",
    "without", "as", "was", "were", "is", "are", "has", "have", "had", "did", "does", "do",
    "can", "cannot", "will", "shall", "proposed", "planned", "intended", "failed",
    "this", "these", "those", "it", "we", "you", "they", "he", "she", "our", "their", "its", "my", "your",
}
_CONTRACTIONS = {"didn't": "did not", "doesn't": "does not", "don't": "do not",
                 "hasn't": "has not", "haven't": "have not", "hadn't": "had not",
                 "wasn't": "was not", "weren't": "were not", "isn't": "is not",
                 "aren't": "are not", "won't": "will not", "can't": "cannot", "couldn't": "could not"}


@dataclass(frozen=True)
class _Assertion:
    actor: str
    action: str
    object: str
    negative: bool
    prospective: bool = False
    modality: str = "actual"

    @property
    def key(self) -> tuple[str, str, str]:
        return self.actor, self.action, self.object

    @property
    def status(self) -> tuple[bool, bool, str]:
        return self.negative, self.prospective, self.modality


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().replace("’", "'")
    for contraction, expanded in _CONTRACTIONS.items():
        text = re.sub(r"\b" + re.escape(contraction) + r"\b", expanded, text)
    return text


def _object_key(value: str) -> str:
    # Keep dates, numbers, prepositions and all substantive qualifiers. This is
    # not token overlap, noun stemming, synonym matching or cross-language AI.
    text = re.sub(r"\b(?:the|a|an)\s+", "", value.strip())
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    return " ".join(text.split())


def _active(actor: str, action: str) -> _Assertion | None:
    negative = prospective = False
    modality = "actual"
    action = action.strip()
    # Only the explicit first limb of these additive forms is recognized.
    if action.startswith("not only ") and " but also " in action:
        action = action[len("not only "):].split(" but also ", 1)[0]
    elif action.startswith("不仅") and re.search(r",\s*还", action):
        action = re.split(r",\s*还", action[2:], maxsplit=1)[0]
    for kind, pattern in (
        ("inability", r"^(?:(?:am|was|were|is|are|has been|have been|had been)\s+unable\s+to|cannot|can not|could not)\s+"),
        ("failure", r"^(?:failed|fails|fail)\s+to\s+"),
        ("capability", r"^(?:(?:am|was|were|is|are|has been|have been|had been)\s+able\s+to|can|could)\s+"),
        ("expected", r"^(?:am|was|were|is|are|has been|have been|had been)\s+expected\s+to\s+"),
    ):
        match = re.match(pattern, action)
        if match:
            modality, action = kind, action[match.end():]
            break
    action = re.sub(r"^(?:has|have|had|did|does|do)\s+", "", action)
    negation = re.match(r"^(?:not|never)(?:\s+yet)?\s+", action)
    if negation:
        if modality != "actual":
            return None  # Nested modal/negation scope is not a literal equivalence.
        negative, action = True, action[negation.end():]
    plan = re.match(r"^(?:proposed|planned|intended)(?:\s+to)?\s+|^(?:will|shall)\s+", action)
    if plan:
        if negative or modality != "actual":
            return None  # Not planning an action does not deny doing it.
        prospective, action = True, action[plan.end():]
        nominal = re.fullmatch(r"(?:(?:a|an|the)\s+)?(" + "|".join(_NOMINAL_ACTIONS) + r")\s+of\s+(.+)", action)
        if nominal:
            return _Assertion(actor, _NOMINAL_ACTIONS[nominal[1]], _object_key(nominal[2]), False, True)
    match = re.match(r"^(" + _EN_ACTION + r")\b\s+(.+)$", action)
    if match:
        return _Assertion(actor, _EN_VERBS[match[1]], _object_key(match[2]), negative, prospective, modality)

    modal = re.match(r"^(无法|不能|未能|能够|可以|能)", action)
    if modal:
        if negative or prospective or modality != "actual":
            return None
        modality = {"无法": "inability", "不能": "inability", "未能": "failure"}.get(modal[1], "capability")
        action = action[modal.end():]
    negation = re.match(r"^(?:尚未|从未|未曾|没有|并未|未|不)", action)
    if negation:
        if negative or prospective or modality != "actual":
            return None  # Mixed double negation/modal scope is not literal evidence.
        negative, action = True, action[negation.end():]
    else:
        plan = re.match(r"^(?:计划|提出了|提出|拟|将)", action)
        if plan:
            if negative or modality != "actual":
                return None
            prospective, action = True, action[plan.end():]
            if plan[0].startswith("提出") and action.endswith("的计划"):
                action = action[:-3]
        else:
            action = re.sub(r"^(?:已经|已)", "", action)
    match = re.match(r"^(" + _ZH_ACTION + r")(?:了)?\s*(.+)$", action)
    if match:
        return _Assertion(actor, match[1], _object_key(match[2]), negative, prospective, modality)
    return None


def _literal_active(clause: str) -> _Assertion | None:
    """Compare explicit noun phrases literally; never infer who an actor is.

    English role phrases or capitalized names have at most six words, no
    subordinate/modal boundary, and immediately precede a recognized predicate. Chinese
    role phrases are bounded and must end at a role noun. Names and roles never
    become applicant aliases; quotations/conditional/coreference are unverified.
    """
    original = clause
    clause = _normalized(clause)
    subject = re.match(r"^" + _ACTOR + r"(?:\s+|(?=[\u4e00-\u9fff]))(.+)$", clause)
    if subject:
        return _active(ACTORS[subject[1]], subject[2])
    for boundary in list(re.finditer(r"\s+", clause))[:7]:
        actor, action = clause[:boundary.start()], clause[boundary.end():]
        words = re.sub(r"^the\s+", "", actor).split()
        if (not words or len(words) > 6 or any(word in _ACTOR_STOP_WORDS for word in words)
                or not all(re.fullmatch(r"[a-z][a-z'-]*", word) for word in words)):
            continue
        # Preserve the distinction between a subject and an unrecognized verb
        # before an adjectival participle, e.g. "This profile lists completed
        # work". A free-form run of words is not evidence of actor identity.
        raw_words = original[:boundary.start()].split()
        named = (not actor.startswith("the ") and len(raw_words) == len(words)
                 and all(re.fullmatch(r"[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)*", word) for word in raw_words))
        if words[-1] not in _EN_ACTOR_ROLES and not named:
            continue
        parsed = _active("literal:" + " ".join(words), action)
        if parsed is not None:
            return parsed
    subject = re.match(r"^(" + _ZH_LITERAL_ACTOR + r")\s*(.+)$", clause)
    if subject and not re.search(r"没有|未能|无法|不能|计划|提出|已经|能够|尚未", subject[1]):
        return _active("literal:" + _object_key(subject[1]), subject[2])
    return None


def _passive(clause: str) -> _Assertion | None:
    match = re.fullmatch(r"(.+?)\s+(?:was|were|is|are|has been|have been|had been)\s+"
                         r"(?:(not|never)(?:\s+yet)?\s+)?(" + _EN_ACTION + r")\b(.*)", clause)
    if not match:
        return None
    patient, negative, verb, tail = match.groups()
    no_patient = bool(re.match(r"^no\s+", patient))
    patient = re.sub(r"^no\s+", "", patient)
    by = re.search(r"\s+by\s+" + _ACTOR + r"\b", tail)
    if by:
        # A later coordinated action cannot supply the performer of this one.
        if re.search(r"\b(?:and|but|while|whereas)\b", tail):
            return None
        actor = ACTORS[by[1]]
        remainder = tail[:by.start()] + tail[by.end():]
        return _Assertion(actor, _EN_VERBS[verb], _object_key(patient + remainder), bool(negative) or no_patient)
    # Unassigned passive forms can be compared only to other passive forms;
    # never promote their patient into a performing actor.
    return _Assertion("passive:" + _object_key(patient), _EN_VERBS[verb], _object_key(tail), bool(negative) or no_patient)


def _assertions(text: str) -> list[_Assertion]:
    result = []
    lines: list[str] = []
    for line in unicodedata.normalize("NFKC", text).splitlines():
        if lines and is_explicit_assertion_prefix_wrap(lines[-1], line):
            lines[-1] += line.strip()
        else:
            lines.append(line)
    for clause in re.split(r"[;；。！？!?\n]|\.(?=\s|$)", "\n".join(lines)):
        clause = " ".join(clause.strip().split())
        if not clause or re.search(r'["“”「」]', clause):
            continue
        assertion = _literal_active(clause) or _passive(_normalized(clause))
        if assertion is not None:
            result.append(assertion)
    return result


def is_explicit_assertion_prefix_wrap(left: str, right: str) -> bool:
    """Only an explicit CJK actor/status prefix immediately before an action."""
    return bool(re.fullmatch(_ACTOR + r"\s*(?:尚未|从未|未曾|没有|并未|未|不|计划|提出|拟|将|已经|已|无法|不能|未能|能够|可以|能)",
                             _normalized(left).strip())
                and re.match(r"^(?:" + _ZH_ACTION + r")", _normalized(right).strip()))


def has_bounded_action_predicate(statement: str) -> bool:
    """Recognize complete explicit negative/intent forms as predicates too."""
    return bool(_assertions(statement))


def source_assertion_conflict_count(statement: str, source_excerpt: str) -> int:
    """Count explicit same-event status conflicts; zero is not entailment.

    Contradictory occurrences within a broad citation require narrower evidence
    even when another occurrence agrees. Different actors, objects and explicit
    periods remain different events. No text or identifiers escape this module.
    """
    statuses: dict[tuple[str, str, str], set[tuple[bool, bool, str]]] = {}
    for source in _assertions(source_excerpt):
        statuses.setdefault(source.key, set()).add(source.status)
    return sum(any(status != candidate.status for status in statuses.get(candidate.key, ()))
               for candidate in _assertions(statement))


def unsupported_explicit_actor_count(statement: str, source_excerpt: str) -> int:
    """Count actor substitutions for the same bounded action and qualifiers.

    An explicitly matching actor in the source supports attribution; a different
    literal name/role alone establishes no alias. Passives retain their separate
    existing guard. This is not identity resolution or general entailment.
    """
    actors: dict[tuple[str, str], set[str]] = {}
    for source in _assertions(source_excerpt):
        if not source.actor.startswith("passive:"):
            actors.setdefault((source.action, source.object), set()).add(source.actor)
    return sum(bool(actors.get((candidate.action, candidate.object)))
               and candidate.actor not in actors[(candidate.action, candidate.object)]
               for candidate in _assertions(statement) if not candidate.actor.startswith("passive:"))


def short_cjk_additive_clause_supported(statement: str, source_excerpt: str) -> bool:
    """Literal first-clause extraction from 不仅…，还…, not a length bypass."""
    candidate = unicodedata.normalize("NFKC", statement).strip()
    for clause in re.split(r"[。！？\n]", unicodedata.normalize("NFKC", source_excerpt)):
        match = re.fullmatch(r"\s*(申请人|本人|团队)不仅(.+?),\s*还.+\s*", clause)
        if match and candidate == match[1] + match[2] + "。":
            return True
    return False
