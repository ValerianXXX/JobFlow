from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .errors import JobOpsError, SecurityBoundaryError
from .locator import LocatedKnowledge
from .security import assert_safe_path, path_has_hard_excluded_name
from .util import load_json, sha256_file, tree_fingerprint


WIKILINK = re.compile(r"!?(?:\[\[)([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")


@dataclass(frozen=True)
class KnowledgeRecord:
    source_id: str
    classification: str
    relative_path: str
    title: str
    heading: str | None
    paragraph: str
    content_fingerprint: str
    historical_completion: bool
    current_health: bool
    external_claim_policy: str

    def as_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


class KnowledgeGateway:
    def __init__(self, location: LocatedKnowledge) -> None:
        self.location = location
        self.manifest = load_json(location.manifest_path)
        self.definitions = {str(item["id"]): item for item in self.manifest["sources"]}
        self.resolved = {str(item["source_id"]): item for item in location.sources}
        self.excluded_segments = self.manifest.get("hard_excluded_segments", [])
        self.excluded_filenames = self.manifest.get("hard_excluded_filenames", [])
        self.extensions = {str(value).casefold() for value in self.manifest.get("readable_extensions", [])}

    def _source_root(self, source_id: str) -> Path:
        if source_id not in self.resolved:
            raise JobOpsError("SOURCE_NOT_RESOLVED", "The requested knowledge source is not resolved.", source_id=source_id)
        return Path(str(self.resolved[source_id]["resolved_path"]))

    def _allowed_prefixes(self, source_id: str) -> tuple[Path, ...]:
        return tuple(Path(str(value)) for value in self.definitions[source_id].get("allowed_prefixes", ["."]))

    def _assert_allowed_prefix(self, source_id: str, relative_path: Path) -> None:
        normalized = Path(os.path.normpath(str(relative_path)))
        if normalized.is_absolute() or ".." in normalized.parts:
            raise SecurityBoundaryError("RELATIVE_PATH_INVALID", "Knowledge paths must stay relative to their source.", path=str(relative_path))
        allowed = False
        for prefix in self._allowed_prefixes(source_id):
            prefix_normalized = Path(os.path.normpath(str(prefix)))
            if prefix_normalized == Path("."):
                allowed = True
                break
            try:
                normalized.relative_to(prefix_normalized)
                allowed = True
                break
            except ValueError:
                if normalized == prefix_normalized:
                    allowed = True
                    break
        if not allowed:
            raise SecurityBoundaryError("PATH_NOT_ALLOWLISTED", "The requested path is outside this source's allowlist.", source_id=source_id, path=normalized.as_posix())

    def safe_path(self, source_id: str, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        self._assert_allowed_prefix(source_id, relative)
        root = self._source_root(source_id)
        target = assert_safe_path(target := root / relative, root, self.excluded_segments, self.excluded_filenames)
        if target.is_file() and target.suffix.casefold() not in self.extensions:
            raise SecurityBoundaryError("FILE_TYPE_NOT_READABLE", "This file type is not approved for knowledge indexing.", path=str(target))
        return target

    def iter_files(self, source_id: str) -> Iterator[Path]:
        root = self._source_root(source_id)
        seen: set[str] = set()
        for prefix in self._allowed_prefixes(source_id):
            start = self.safe_path(source_id, prefix)
            if start.is_file():
                candidates = [start]
            else:
                candidates = []
                for current, directories, filenames in os.walk(start, topdown=True, followlinks=False):
                    current_path = Path(current)
                    directories[:] = [
                        name for name in directories
                        if not path_has_hard_excluded_name(current_path / name, self.excluded_segments, self.excluded_filenames)
                        and not (current_path / name).is_symlink()
                    ]
                    for filename in filenames:
                        candidates.append(current_path / filename)
            for candidate in candidates:
                key = os.path.normcase(str(candidate.absolute()))
                if key in seen or candidate.suffix.casefold() not in self.extensions:
                    continue
                seen.add(key)
                relative = candidate.relative_to(root)
                yield self.safe_path(source_id, relative)

    def read_text(self, source_id: str, relative_path: str | Path) -> str:
        path = self.safe_path(source_id, relative_path)
        if not path.is_file():
            raise SecurityBoundaryError("NOT_A_FILE", "The requested knowledge item is not a file.", path=str(path))
        try:
            return path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SecurityBoundaryError("TEXT_ENCODING_REJECTED", "Knowledge text must be UTF-8 compatible.", path=str(path)) from exc

    def resolve_wikilink(self, source_id: str, link: str, from_relative_path: str | Path) -> str:
        match = WIKILINK.fullmatch(link.strip())
        if not match:
            raise JobOpsError("WIKILINK_INVALID", "The Obsidian link syntax is invalid.", link=link)
        target = match.group(1).strip().replace("\\", "/")
        if not target.casefold().endswith(".md"):
            target += ".md"
        from_parent = Path(from_relative_path).parent
        direct = from_parent / target
        try:
            path = self.safe_path(source_id, direct)
            if path.is_file():
                return path.relative_to(self._source_root(source_id)).as_posix()
        except SecurityBoundaryError:
            pass
        target_name = Path(target).name.casefold()
        matches = [path for path in self.iter_files(source_id) if path.name.casefold() == target_name]
        if not matches:
            raise JobOpsError("WIKILINK_TARGET_NOT_FOUND", "No allowlisted knowledge file matches the link.", link=link)
        if len(matches) > 1:
            raise JobOpsError("WIKILINK_AMBIGUOUS", "More than one allowlisted knowledge file matches the link.", link=link, candidates=[str(path) for path in matches])
        return matches[0].relative_to(self._source_root(source_id)).as_posix()

    def search(self, query: str, *, source_ids: list[str] | None = None, limit: int = 20) -> list[KnowledgeRecord]:
        query = query.strip()
        if not query:
            raise JobOpsError("EMPTY_QUERY", "Knowledge search requires a non-empty query.")
        selected = source_ids or list(self.resolved)
        needle = query.casefold()
        records: list[KnowledgeRecord] = []
        for source_id in selected:
            root = self._source_root(source_id)
            source_meta = self.resolved[source_id]
            for path in self.iter_files(source_id):
                text = self.read_text(source_id, path.relative_to(root))
                if needle not in text.casefold() and needle not in path.stem.casefold():
                    continue
                title = path.stem
                heading: str | None = None
                paragraphs: list[tuple[str | None, str]] = []
                buffer: list[str] = []
                current_heading: str | None = None
                for line in text.splitlines() + [""]:
                    if line.startswith("#"):
                        if buffer:
                            paragraphs.append((current_heading, "\n".join(buffer).strip()))
                            buffer = []
                        current_heading = line.lstrip("#").strip()
                    elif line.strip():
                        buffer.append(line.strip())
                    elif buffer:
                        paragraphs.append((current_heading, "\n".join(buffer).strip()))
                        buffer = []
                matching = [(head, body) for head, body in paragraphs if needle in body.casefold() or (head and needle in head.casefold())]
                if not matching and needle in path.stem.casefold():
                    matching = [(None, paragraphs[0][1] if paragraphs else "")]
                fingerprint = sha256_file(path)
                relative = path.relative_to(root).as_posix()
                source_policy = str(source_meta["external_claim_policy"])
                question_only = tuple(str(value).replace("\\", "/").casefold() for value in self.definitions[source_id].get("question_only_prefixes", []))
                if any(relative.casefold() == prefix or relative.casefold().startswith(prefix + "/") for prefix in question_only):
                    source_policy = "question_only"
                for head, body in matching[:3]:
                    context = f"{relative} {head or ''} {body}".casefold()
                    records.append(KnowledgeRecord(
                        source_id=source_id,
                        classification=str(source_meta["classification"]),
                        relative_path=relative,
                        title=title,
                        heading=head,
                        paragraph=body,
                        content_fingerprint=fingerprint,
                        historical_completion=any(term in context for term in ("已完成", "完成事项", "case-")),
                        current_health=any(term in context for term in ("当前健康", "健康记录", "health")),
                        external_claim_policy=source_policy,
                    ))
                    if len(records) >= limit:
                        return records
        return records

    def snapshot_collections(self) -> dict[str, object]:
        collections = {
            "ai-public": (self.location.root / "AI测试实验室", None),
            "business-public": (self.location.root / "商业决策实验室", None),
            "personal-redacted": (
                self.location.root / "个人AI应用实验室",
                [
                    Path(str(prefix)).relative_to("个人AI应用实验室")
                    for prefix in self.definitions["personal_redacted"].get("allowed_prefixes", [])
                    if str(prefix).replace("\\", "/").startswith("个人AI应用实验室/")
                ],
            ),
            "joint-navigation": (self.location.root / "00-联合导航", None),
        }
        output: dict[str, object] = {}
        for collection_id, (root, allowed_subpaths) in collections.items():
            safe_root = assert_safe_path(root, self.location.root, self.excluded_segments, self.excluded_filenames)
            files: list[Path] = []
            starts = [safe_root / subpath for subpath in allowed_subpaths] if allowed_subpaths else [safe_root]
            for start in starts:
                start = assert_safe_path(start, safe_root, self.excluded_segments, self.excluded_filenames)
                for current, directories, filenames in os.walk(start, topdown=True, followlinks=False):
                    current_path = Path(current)
                    directories[:] = [name for name in directories if not path_has_hard_excluded_name(current_path / name, self.excluded_segments, self.excluded_filenames)]
                    for filename in filenames:
                        path = current_path / filename
                        if not path_has_hard_excluded_name(path, self.excluded_segments, self.excluded_filenames):
                            files.append(path)
            output[collection_id] = {
                "root": f"$KNOWLEDGE_ROOT/{collection_id}",
                "scope": "allowlisted_only" if allowed_subpaths else "full_collection",
                **tree_fingerprint(safe_root, files),
            }
        return {"schema_version": 1, "collections": output}

    @staticmethod
    def compare_snapshots(baseline: dict[str, object], current: dict[str, object]) -> dict[str, object]:
        expected = baseline.get("collections", {})
        actual = current.get("collections", {})
        changed: list[str] = []
        for key in sorted(set(expected) | set(actual)):  # type: ignore[arg-type]
            before = expected.get(key)  # type: ignore[union-attr]
            after = actual.get(key)  # type: ignore[union-attr]
            if not isinstance(before, dict) or not isinstance(after, dict):
                changed.append(str(key))
                continue
            if before.get("file_count") != after.get("file_count") or before.get("tree_sha256") != after.get("tree_sha256"):
                changed.append(str(key))
        return {"status": "UNCHANGED" if not changed else "CHANGED", "changed_collections": changed}
