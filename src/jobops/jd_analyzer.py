from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .util import sha256_bytes


UNKNOWN = "UNKNOWN"


SECTION_ALIASES = {
    "responsibilities": ("responsibilities", "what you'll do", "what you will do", "职责", "岗位职责"),
    "hard_requirements": ("required", "requirements", "minimum qualifications", "must have", "必要条件", "任职要求", "硬性要求"),
    "preferred_qualifications": ("preferred", "preferred qualifications", "nice to have", "加分项", "优先条件"),
}


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    category: str
    text: str
    logic: str
    items: tuple[str, ...]
    threshold: int | None

    def as_dict(self) -> dict[str, object]:
        return {**self.__dict__, "items": list(self.items)}


@dataclass(frozen=True)
class NormalizedJD:
    snapshot_hash: str
    company: str = UNKNOWN
    title: str = UNKNOWN
    location: str = UNKNOWN
    salary: str = UNKNOWN
    work_authorization: str = UNKNOWN
    deadline: str = UNKNOWN
    level: str = UNKNOWN
    employment_type: str = UNKNOWN
    responsibilities: tuple[str, ...] = field(default_factory=tuple)
    hard_requirements: tuple[str, ...] = field(default_factory=tuple)
    preferred_qualifications: tuple[str, ...] = field(default_factory=tuple)
    requirements: tuple[Requirement, ...] = field(default_factory=tuple)
    keywords: tuple[str, ...] = field(default_factory=tuple)
    untrusted_instruction_signals: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in self.__dict__.items():
            if key == "requirements":
                result[key] = [item.as_dict() for item in value]
            elif isinstance(value, tuple):
                result[key] = list(value)
            else:
                result[key] = value
        return result


def _clean_item(value: str) -> str:
    aliases = {"英语": "english", "英文": "english", "中文": "chinese", "汉语": "chinese", "普通话": "mandarin"}
    cleaned = value.strip().strip("()（）.;。；").casefold()
    return aliases.get(cleaned, cleaned)


def _boolean_expression(value: str):
    """Parse a bounded AND/OR expression with parentheses; AND binds tighter than OR."""
    material = value.replace("（", "(").replace("）", ")")
    material = re.sub(r"(?i)\b(?:either|one\s+of)\b", " ", material)
    material = re.sub(r"(?:任选其一|任选一项|任选)", " ", material)
    material = material.replace("以及", " and ").replace("并且", " and ").replace("和", " and ").replace("或", " or ")
    raw = re.split(r"(?i)(\(|\)|\band\b|\bor\b)", material)
    tokens = [token.strip(" \t,，、:：.;。；") for token in raw if token.strip(" \t,，、:：.;。；")]
    position = 0

    def factor():
        nonlocal position
        if position >= len(tokens):
            raise ValueError("missing operand")
        if tokens[position] == "(":
            position += 1
            result = parse_or()
            if position >= len(tokens) or tokens[position] != ")":
                raise ValueError("unbalanced parentheses")
            position += 1
            return result
        if tokens[position].casefold() in {"and", "or"} or tokens[position] == ")":
            raise ValueError("unexpected operator")
        result = ("LEAF", _clean_item(tokens[position]))
        position += 1
        return result

    def parse_and():
        nonlocal position
        values = [factor()]
        while position < len(tokens) and tokens[position].casefold() == "and":
            position += 1
            values.append(factor())
        return values[0] if len(values) == 1 else ("ALL", tuple(values))

    def parse_or():
        nonlocal position
        values = [parse_and()]
        while position < len(tokens) and tokens[position].casefold() == "or":
            position += 1
            values.append(parse_and())
        return values[0] if len(values) == 1 else ("ANY", tuple(values))

    try:
        expression = parse_or()
        return expression if position == len(tokens) else None
    except ValueError:
        return None


def _expression_items(expression) -> tuple[str, ...]:
    if expression is None:
        return ()
    if expression[0] == "LEAF":
        return (expression[1],)
    return tuple(dict.fromkeys(item for child in expression[1] for item in _expression_items(child)))


def _requirement(text: str, index: int) -> Requirement:
    value = text.strip()
    lower = value.casefold()
    category = "skill"
    if re.search(r"\d+\s*\+?\s*(?:years?|年)", lower):
        category = "years"
    elif any(term in lower for term in ("degree", "bachelor", "master", "phd", "学历", "本科", "硕士", "博士")):
        category = "education"
    elif any(term in lower for term in ("certification", "certificate", "license", "证书", "资格证")):
        category = "certification"
    elif any(term in lower for term in ("language", "english", "chinese", "mandarin", "英语", "英文", "中文", "语言")):
        category = "language"
    elif any(term in lower for term in ("work authorization", "authorized to work", "工作授权", "合法工作")):
        category = "work_authorization"
    elif any(term in lower for term in ("sponsorship", "visa", "签证", "担保")):
        category = "visa"
    elif any(term in lower for term in ("travel", "出差")):
        category = "travel"
    elif any(term in lower for term in ("relocat", "搬迁")):
        category = "relocation"
    elif any(term in lower for term in ("salary", "compensation", "pay range", "薪资", "薪酬")):
        category = "salary"
    elif any(term in lower for term in ("location", "located", "reside", "time zone", "工作地点", "地点", "城市")):
        category = "location"
    elif any(term in lower for term in ("senior", "junior", "entry level", "manager level", "职级", "级别")):
        category = "level"

    threshold_match = re.search(r"(?i)at\s+least\s+(\d+)\s+(?:of|from)\s+(.+)", value)
    if threshold_match:
        threshold = int(threshold_match.group(1))
        material = threshold_match.group(2)
        items = tuple(_clean_item(item) for item in re.split(r"\s*(?:,|/|\band\b|\bor\b|，|、|和|或)\s*", material, flags=re.IGNORECASE) if _clean_item(item))
        logic = "AT_LEAST"
    elif category in {"skill", "language", "certification"} and re.search(r"(?i)\band\b|\bor\b|以及|并且|和|或|任选|[()]|[（）]", value):
        expression = _boolean_expression(value)
        items = _expression_items(expression)
        if not expression or not items:
            items, logic = (_clean_item(value),), "SINGLE"
        else:
            logic = "ANY" if expression[0] == "ANY" else "ALL" if expression[0] == "ALL" else "SINGLE"
        threshold = None
    else:
        items, logic, threshold = (_clean_item(value),), "SINGLE", None
    return Requirement(f"REQ-{index:03d}", category, value, logic, items, threshold)


def _field(text: str, labels: Iterable[str]) -> str:
    label_pattern = "|".join(re.escape(value) for value in labels)
    match = re.search(rf"(?im)^\s*(?:{label_pattern})\s*(?::|：|\|)\s*(.+?)\s*\|?\s*$", text)
    return match.group(1).strip() if match else UNKNOWN


def _sections(text: str) -> dict[str, list[str]]:
    alias_to_key = {alias.casefold(): key for key, aliases in SECTION_ALIASES.items() for alias in aliases}
    result = {key: [] for key in SECTION_ALIASES}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        table_match = re.match(r"^([^|]{2,60})\|\s*(.+?)\s*\|?$", line)
        if table_match and table_match.group(1).strip().casefold() in alias_to_key:
            result[alias_to_key[table_match.group(1).strip().casefold()]].append(table_match.group(2).strip())
            continue
        inline_match = re.match(r"^([^:：]{2,60})[:：]\s*(.+)$", line)
        if inline_match and inline_match.group(1).strip().casefold() in alias_to_key:
            result[alias_to_key[inline_match.group(1).strip().casefold()]].append(inline_match.group(2).strip())
            current = alias_to_key[inline_match.group(1).strip().casefold()]
            continue
        normalized_heading = line.rstrip(":：").strip().casefold()
        if normalized_heading in alias_to_key:
            current = alias_to_key[normalized_heading]
            continue
        heading_match = re.match(r"^([^:：]{2,60})[:：]\s*$", line)
        if heading_match and heading_match.group(1).strip().casefold() in alias_to_key:
            current = alias_to_key[heading_match.group(1).strip().casefold()]
            continue
        if not line:
            continue
        if current and re.match(r"^(?:[-*•]|\d+[.)])\s+", line):
            result[current].append(re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", line).strip())
        elif current and not re.match(r"^[A-Za-z][A-Za-z /&-]{1,40}:\s*", line):
            result[current].append(line)
    return result


def _level(title: str, text: str) -> str:
    # Seniority words in duties (for example, "present to senior management")
    # do not describe the role itself. Infer a level only from the role title
    # or from an explicit level/seniority field; otherwise leave it UNKNOWN for
    # the applicant to review.
    explicit = " ".join(
        match.group(1)
        for match in re.finditer(
            r"(?im)^(?:level|seniority|job level|职位级别|职级)\s*[:：]\s*([^\r\n]{1,80})$",
            text,
        )
    )
    material = f"{title} {explicit}".casefold()
    levels = (
        ("executive", ("chief ", "vice president", "vp ", "总裁", "首席")),
        ("director", ("director", "head of", "负责人", "总监")),
        ("manager", ("manager", "管理", "经理")),
        ("senior", ("senior", "sr.", "资深", "高级")),
        ("entry", ("entry level", "junior", "new grad", "初级", "应届")),
    )
    for level, terms in levels:
        if any(term in material for term in terms):
            return level
    return UNKNOWN


def _keywords(text: str, limit: int = 30) -> tuple[str, ...]:
    stop = {"the", "and", "for", "with", "this", "that", "you", "our", "your", "will", "are", "from", "have", "years", "experience", "required", "preferred", "responsibilities", "company", "role"}
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9+.#/-]{2,}|[\u4e00-\u9fff]{2,8}", text.casefold())
    counts: dict[str, int] = {}
    for token in tokens:
        token = token.strip("-/.#")
        if token in {"analyze", "analyzes", "analyzed", "analyzing", "analytical", "analyst", "analysts", "analytics"}:
            token = "analysis"
        if token in stop or len(token) < 2:
            continue
        counts[token] = counts.get(token, 0) + 1
    return tuple(value for value, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit])


def analyze_jd(text: str) -> NormalizedJD:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    sections = _sections(normalized)
    company = _field(normalized, ("company", "company name", "公司", "公司名称"))
    title = _field(normalized, ("role", "title", "job title", "职位", "岗位"))
    location = _field(normalized, ("location", "work location", "地点", "工作地点"))
    salary = _field(normalized, ("salary", "compensation", "pay range", "薪资", "薪酬"))
    authorization = _field(normalized, ("work authorization", "visa sponsorship", "工作授权", "签证"))
    deadline = _field(normalized, ("deadline", "apply by", "closing date", "截止时间", "申请截止"))
    employment_type = _field(normalized, ("employment type", "job type", "雇佣类型", "职位类型"))
    signals = []
    for pattern, name in (
        (r"(?i)ignore (?:all |any )?(?:previous|prior|system) instructions", "prompt_injection"),
        (r"(?i)(?:download|run|execute) (?:this |the )?(?:program|script|macro|executable)", "untrusted_executable_instruction"),
        (r"(?i)(?:send|upload).{0,30}(?:password|token|cookie|bank|social security|身份证|银行卡)", "sensitive_exfiltration_instruction"),
    ):
        if re.search(pattern, normalized):
            signals.append(name)
    requirements = tuple(_requirement(value, index + 1) for index, value in enumerate(sections["hard_requirements"]))
    return NormalizedJD(
        snapshot_hash=sha256_bytes(normalized.encode("utf-8")),
        company=company,
        title=title,
        location=location,
        salary=salary,
        work_authorization=authorization,
        deadline=deadline,
        level=_level(title, normalized),
        employment_type=employment_type,
        responsibilities=tuple(sections["responsibilities"]),
        hard_requirements=tuple(sections["hard_requirements"]),
        preferred_qualifications=tuple(sections["preferred_qualifications"]),
        requirements=requirements,
        keywords=_keywords(normalized),
        untrusted_instruction_signals=tuple(signals),
    )
