from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Iterable

from .errors import SecurityBoundaryError
from .util import has_reparse_component, is_relative_to


SECRET_PATTERNS = (
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)\b\s*[:=]"),
    re.compile(r"(?i)\b(cookie|set-cookie)\s*:"),
    re.compile(r"\b\d{6}\b.*(?:验证码|otp)|(?:验证码|otp).*\b\d{6}\b", re.IGNORECASE),
)

HARD_EXCLUDED_PATTERNS = (
    r"(?:^|[^a-z0-9])tokens?(?:[^a-z0-9]|$)", r"credentials?", r"passwords?", r"passwd",
    r"api[\s._-]*keys?", r"oauth[\s._-]*tokens?", r"access[\s._-]*tokens?", r"refresh[\s._-]*tokens?",
    r"private[\s._-]*keys?", r"cookies?", r"login[\s._-]*data", r"browser[\s._-]*(?:profile|data)",
    r"backup[\s._-]*credentials?", r"credentials?[\s._-]*backup", r"raw[\s._-]*attachments?",
    r"original[\s._-]*(?:chatgpt|hermes)[\s._-]*(?:export|backup)", r"session[\s._-]*logs?",
    r"原始[\s._-]*附件", r"原始[\s._-]*chatgpt[\s._-]*导出", r"chatgpt[\s._-]*原始[\s._-]*导出",
    r"原始[\s._-]*hermes[\s._-]*备份", r"hermes[\s._-]*原始[\s._-]*备份", r"数据[\s._-]*导入[\s._-]*区",
    r"日志", r"验证码", r"备份[\s._-]*凭据", r"私钥", r"密码",
)


def normalized_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def path_has_hard_excluded_name(
    path: Path,
    excluded_segments: Iterable[str],
    excluded_filenames: Iterable[str],
) -> bool:
    segments = {normalized_name(segment) for segment in excluded_segments}
    filenames = {normalized_name(name) for name in excluded_filenames}
    for part in path.parts:
        normalized = normalized_name(part)
        stem = normalized_name(Path(part).stem)
        if normalized in segments or normalized in filenames or stem in segments or stem in filenames:
            return True
        material = f" {normalized} {stem} "
        if any(re.search(pattern, material, re.IGNORECASE) for pattern in HARD_EXCLUDED_PATTERNS):
            return True
    return False


def assert_safe_path(
    candidate: Path,
    allowed_root: Path,
    excluded_segments: Iterable[str],
    excluded_filenames: Iterable[str],
    *,
    must_exist: bool = True,
) -> Path:
    raw_candidate = Path(candidate)
    if path_has_hard_excluded_name(raw_candidate, excluded_segments, excluded_filenames):
        raise SecurityBoundaryError("HARD_EXCLUDED_PATH", "The requested path belongs to a hard-excluded area.", path=str(raw_candidate))
    absolute = Path(raw_candidate.absolute())
    root_absolute = Path(allowed_root.absolute())
    if not is_relative_to(absolute, root_absolute):
        raise SecurityBoundaryError("PATH_OUTSIDE_SOURCE", "The requested path escapes the allowed source.", path=str(absolute))
    if must_exist and not absolute.exists():
        raise SecurityBoundaryError("PATH_NOT_FOUND", "The requested source path does not exist.", path=str(absolute))
    if has_reparse_component(absolute, root_absolute):
        raise SecurityBoundaryError("REPARSE_POINT_DISALLOWED", "Links and reparse points are not accepted as knowledge sources.", path=str(absolute))
    if must_exist:
        resolved = absolute.resolve(strict=True)
        resolved_root = root_absolute.resolve(strict=True)
        if not is_relative_to(resolved, resolved_root):
            raise SecurityBoundaryError("RESOLVED_PATH_OUTSIDE_SOURCE", "The resolved target escapes the allowed source.", path=str(resolved))
        return resolved
    return absolute


def assert_no_plaintext_secret(text: str) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise SecurityBoundaryError("PLAINTEXT_SECRET_DETECTED", "Potential credential, cookie or verification code content was rejected.")


def validate_secure_reference(value: str | None) -> None:
    if value is None:
        return
    if not re.fullmatch(r"secure-ref:[A-Za-z0-9_-]{8,128}", value):
        raise SecurityBoundaryError("INVALID_SECURE_REFERENCE", "Application-private values must use an opaque secure-ref identifier.")


def assert_project_io_path(candidate: Path, project: Path, *, operation: str) -> Path:
    """Restrict ordinary CLI I/O to explicit project zones; secure import has a separate route."""
    if operation not in {"read", "write"}:
        raise ValueError(operation)
    project = project.resolve(strict=True)
    candidate = Path(candidate)
    absolute = candidate.absolute()
    allowed = (
        ("workspace", "state", "reports", "tests/fixtures", "tests/.tmp", "config", "schemas")
        if operation == "read" else ("workspace", "state", "reports", "tests/.tmp")
    )
    if path_has_hard_excluded_name(absolute, (), ()):
        raise SecurityBoundaryError("HARD_EXCLUDED_PATH", "Ordinary CLI I/O rejected a hard-excluded path.")
    if not is_relative_to(absolute, project):
        raise SecurityBoundaryError("CLI_PATH_OUTSIDE_PROJECT", "Ordinary CLI I/O must remain in an approved project area.")
    permitted = any(is_relative_to(absolute, project / relative) for relative in allowed)
    if not permitted:
        raise SecurityBoundaryError("CLI_PATH_AREA_NOT_ALLOWED", "The requested CLI path is outside approved project I/O areas.")
    if operation == "read":
        return assert_safe_path(absolute, project, (), (), must_exist=True)
    if has_reparse_component(absolute.parent, project):
        raise SecurityBoundaryError("REPARSE_POINT_DISALLOWED", "CLI output cannot traverse a link or reparse point.")
    return absolute


def classify_form_field(label: str, blocked_categories: Iterable[str]) -> str:
    from .forms import classify_application_field
    classification, _ = classify_application_field({"label": label}, blocked_categories=blocked_categories)
    return "PREFILL_ALLOWED" if classification in {"ordinary_fixed", "private_fixed"} else "STOP_REQUIRED"
