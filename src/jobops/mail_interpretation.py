"""Conservative status evidence from the current message, preserving source offsets.

This bounded lexical interpreter is not a general language or sender-authentication
model. It excludes recognizable history and counter-context; unfamiliar wording
may still require manual review. It cannot change an application or send mail.
"""
from __future__ import annotations

import re

PATTERNS = {
    "APPLICATION_RECEIVED": r"application (?:has been |was )?received|received your application|申请已收到|收到您的申请",
    "REJECTED": r"not (?:be )?moving forward|not selected|unsuccessful|未通过|未能进入|不予录用",
    "INTERVIEW_INVITED": r"invite you (?:to|for) (?:an? )?interview|interview invitation|邀请[^。！？\n]{0,80}面试|面试邀请",
}
HISTORY_HEADER = re.compile(
    r"^\s*(?:[-_ ]*(?:original message|forwarded message|原始邮件|转发邮件)[-_ ]*"
    r"|begin forwarded message[:：]?|开始转发的邮件[:：]?|on .{1,500} wrote:|在.{1,500}写道[:：])\s*$", re.I,
)
FORWARD_SUBJECT = re.compile(r"^\s*(?:fw|fwd|转发)\s*[:：]", re.I)
INLINE_QUOTE = re.compile(r'"[^"\n]+"|“[^”\n]+”|「[^」\n]+」|(?<!\w)\x27[^\x27\n]+\x27(?!\w)')
COUNTER_CONTEXT = re.compile(
    r"\b(?:if|whether|may|might|could|example|sample|template|newsletter|article|hypothetical)\b"
    r"|\bnot (?:a|the) decision\b|\b(?:not|isn't) true\b"
    r"|如果|假设|示例|模板|新闻|文章|尚未决定|尚未作出|并非|不代表|未作出|还未决定", re.I,
)
NEGATION = {
    "APPLICATION_RECEIVED": re.compile(r"\b(?:not|never|no)\b|n't\b|尚未|没有|未收到|未曾|并未", re.I),
    "INTERVIEW_INVITED": re.compile(r"\b(?:not|never|no|without)\b|n't\b|不是|尚未|暂无|没有|并未|未发出|不要", re.I),
    # Rejections normally contain a negative; only counter-negation is excluded.
    "REJECTED": re.compile(r"\b(?:not|never)\s+(?:an?\s+)?(?:unsuccessful|reject\w*)|没有.{0,8}拒绝", re.I),
}


def active_message_text(subject: str, body: str, ignored_body_ranges=()) -> tuple[str, set[str]]:
    """Mask, rather than remove, ignored content so evidence uses original offsets."""
    original = subject + "\n" + body
    characters = list(original)
    reasons = set()

    def mask(start, end, reason):
        for offset in range(start, end):
            if characters[offset] not in "\r\n":
                characters[offset] = " "
        reasons.add(reason)

    if FORWARD_SUBJECT.match(subject):
        mask(0, len(subject), "FORWARDED_SUBJECT_IGNORED")
    for start, end in ignored_body_ranges:
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(body):
            raise ValueError("Invalid mail evidence range.")
        mask(len(subject) + 1 + start, len(subject) + 1 + end, "HTML_QUOTED_HISTORY_IGNORED")
    offset = len(subject) + 1
    lines = body.splitlines(keepends=True)
    for index, line in enumerate(lines):
        following = "".join(lines[index + 1:index + 7])
        header_block = re.match(r"^\s*(?:from|发件人)[:：]", line, re.I) and re.search(
            r"(?im)^\s*(?:sent|date|发送时间|日期)[:：]", following)
        if HISTORY_HEADER.match(line.strip()) or header_block:
            mask(offset, len(original), "QUOTED_HISTORY_IGNORED")
            break
        if re.match(r"^\s*>+", line):
            mask(offset, offset + len(line), "QUOTED_HISTORY_IGNORED")
        offset += len(line)
    for match in INLINE_QUOTE.finditer("".join(characters)):
        mask(match.start(), match.end(), "QUOTED_TEXT_IGNORED")
    return "".join(characters), reasons


def interpret_status(subject: str, body: str, *, ignored_body_ranges=()) -> dict:
    active, reasons = active_message_text(subject, body, ignored_body_ranges)
    matches = []
    for clause in re.finditer(r"[^.!?。！？;；\r\n]+", active):
        text = clause.group()
        for status, pattern in PATTERNS.items():
            for match in re.finditer(pattern, text, re.I):
                if COUNTER_CONTEXT.search(text) or NEGATION[status].search(text):
                    reasons.add("NEGATED_OR_CONDITIONAL_STATUS")
                    continue
                matches.append((status, {"start": clause.start() + match.start(), "end": clause.start() + match.end()}))
    statuses = {status for status, _ in matches}
    if len(statuses) > 1:
        reasons.add("CONFLICTING_CURRENT_STATUS")
    if not matches:
        reasons.add("NO_CURRENT_STATUS_EVIDENCE")
    return {"active_text": active, "status": next(iter(statuses)) if len(statuses) == 1 else "UNKNOWN",
            "spans": [span for _, span in matches], "review_reasons": sorted(reasons)}
