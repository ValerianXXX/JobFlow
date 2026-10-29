"""Explicit local PDF conversion; no installer, model, listener or fallback."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import zipfile

from lxml import etree

from .errors import JobOpsError
from .util import has_reparse_component


MAX_PDF_BYTES = 32 * 1024 * 1024
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _office_path(path: Path) -> Path:
    """Use the same existing directory's DOS alias, when NTFS provides one.

    The native office importer can silently produce no output on deeply nested
    paths even though Python can read them. This does not move private content.
    """
    if os.name != "nt":
        return path
    import ctypes
    from ctypes import wintypes
    function = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    function.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    function.restype = wintypes.DWORD
    output = ctypes.create_unicode_buffer(32768)
    length = function(str(path), output, len(output))
    if 0 < length < len(output):
        alias = Path(output.value)
        if alias.samefile(path):
            return alias
    return path


@contextmanager
def _conversion_workspace(parent: Path):
    parent = parent.resolve(strict=True)
    root = Path(tempfile.mkdtemp(prefix="jobflow-pdf-", dir=parent)).resolve(strict=True)
    try:
        yield _office_path(root)
    finally:
        if root.parent != parent or has_reparse_component(root, parent):
            raise JobOpsError("DOCX_PDF_CLEANUP_FAILED", "The converter workspace boundary changed.")
        # LibreOffice creates extension-cache names exceeding MAX_PATH even
        # when the document path itself is short. Python 3.11 rmtree needs the
        # extended absolute spelling; it must still target only this owned dir.
        cleanup = str(root)
        if os.name == "nt" and not cleanup.startswith("\\\\?\\"):
            cleanup = "\\\\?\\UNC\\" + cleanup[2:] if cleanup.startswith("\\\\") else "\\\\?\\" + cleanup
        shutil.rmtree(cleanup)


def selected_pdf_provider() -> str:
    value = os.environ.get("JOBFLOW_PDF_EXPORT_PROVIDER", "word").strip().lower()
    if value not in {"word", "libreoffice"}:
        raise JobOpsError("DOCX_PDF_PROVIDER_INVALID", "Select word or libreoffice for local PDF export.")
    return value


def libreoffice_binary() -> Path | None:
    """Discovery only. An explicit invalid path never falls back to PATH."""
    configured = os.environ.get("JOBFLOW_LIBREOFFICE_BINARY")
    if configured is not None:
        candidate = Path(configured)
        if not candidate.is_absolute():
            return None
        candidates = [candidate]
    elif os.name == "nt":
        candidates = [Path(base) / "LibreOffice" / "program" / "soffice.com"
                      for key in ("ProgramFiles", "ProgramFiles(x86)") if (base := os.environ.get(key))]
    else:
        found = shutil.which("libreoffice") or shutil.which("soffice")
        candidates = [Path(found)] if found else []
    for candidate in candidates:
        if candidate.is_file() and (os.name != "nt" or candidate.name.lower() == "soffice.com"):
            return candidate.resolve()
    return None


def _validate_input(path: Path) -> None:
    # Keep the existing size/ZIP boundary. The optional importer does not fetch
    # external resources or evaluate active document fields to make a preview.
    from .document_builder import _zip_inventory

    _zip_inventory(path)
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                folded = name.casefold()
                if any(part in folded for part in ("vbaproject", "/activex/", "/embeddings/", "customui/")):
                    raise ValueError("active package")
                if not folded.endswith((".xml", ".rels")):
                    continue
                raw = archive.read(name)
                if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                    raise ValueError("xml declaration")
                root = etree.fromstring(raw, etree.XMLParser(resolve_entities=False, no_network=True))
                for node in root.iter():
                    if node.tag == f"{{{REL_NS}}}Relationship" and node.get("TargetMode", "").lower() == "external":
                        # Clickable web/email links are inert during conversion.
                        if (node.get("Type", "") != "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
                                or not node.get("Target", "").lower().startswith(("https://", "http://", "mailto:"))):
                            raise ValueError("external resource")
                    if node.tag in {f"{{{W_NS}}}altChunk", f"{{{W_NS}}}object", f"{{{W_NS}}}control"}:
                        raise ValueError("active content")
                    instruction = None
                    if node.tag == f"{{{W_NS}}}instrText":
                        instruction = "".join(node.itertext())
                    elif node.tag == f"{{{W_NS}}}fldSimple":
                        instruction = node.get(f"{{{W_NS}}}instr", "")
                    if instruction is not None and not re.fullmatch(
                        r"\s*(?:PAGE|NUMPAGES|SECTIONPAGES)(?:\s+\\\*\s+(?:MERGEFORMAT|Arabic|roman|ROMAN))*\s*",
                        instruction,
                    ):
                        raise ValueError("dynamic field")
                if folded == "[content_types].xml" and b"macroenabled" in raw.lower():
                    raise ValueError("macro content type")
    except (ValueError, etree.XMLSyntaxError, OSError, zipfile.BadZipFile) as exc:
        raise JobOpsError("DOCX_PDF_INPUT_UNSUPPORTED", "Local PDF export requires a DOCX without active fields, embedded objects or external resources.") from None


def _run_converter(command: list[str], *, cwd: Path, env: dict[str, str], timeout: float) -> int:
    # Reuse the already tested suspended-start Windows Job implementation. The
    # owner exists before the executable can create children; no PID-wide kill.
    from .release_smoke_process import _WindowsJob, _WindowsProcess

    job = _WindowsJob() if os.name == "nt" else None
    process = None
    try:
        with open(os.devnull, "w+b") as discard:
            try:
                if job:
                    process = _WindowsProcess(command, cwd=cwd, env=env, stdout=discard, stderr=discard)
                    job.assign(process)
                    process.resume()
                else:
                    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                               stdout=discard, stderr=discard, start_new_session=True)
                return process.wait(timeout=timeout)
            finally:
                try:
                    if job:
                        job.terminate()
                    elif process:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                finally:
                    if process:
                        if process.poll() is None:
                            process.kill()
                        process.wait(timeout=10)
    finally:
        try:
            if isinstance(process, _WindowsProcess):
                process.close()
        finally:
            if job:
                job.close()


def export_with_libreoffice(docx_path: Path, pdf_path: Path) -> None:
    binary = libreoffice_binary()
    if binary is None:
        raise JobOpsError("DOCX_PDF_EXPORT_UNAVAILABLE", "The selected local LibreOffice executable is unavailable.")
    _validate_input(docx_path)
    if docx_path.resolve() == pdf_path.resolve():
        raise JobOpsError("MASTER_OVERWRITE_FORBIDDEN", "PDF conversion must not overwrite the source document.")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    stage = "PREPARE"
    try:
        # Callers supply the existing restricted private staging directory (or
        # a synthetic output directory). All profile/cache/input copies stay in
        # this owned child, never the normal office profile or a cloud folder.
        with _conversion_workspace(pdf_path.parent) as root:
            profile, output, cache = root / "profile", root / "output", root / "cache"
            for folder in (profile / "user", output, cache):
                folder.mkdir(parents=True)
            (profile / "user" / "registrymodifications.xcu").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
                '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
                '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
                '</oor:items>', encoding="utf-8",
            )
            source = root / "input.docx"
            shutil.copyfile(docx_path, source)
            command = [str(binary), f"-env:UserInstallation={profile.as_uri()}", "--headless", "--nologo",
                       "--nodefault", "--norestore", "--convert-to", "pdf:writer_pdf_Export",
                       "--outdir", str(output), str(source)]
            env = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "LANG", "LC_ALL"}}
            env.update({"PATH": os.pathsep.join((str(binary.parent), os.defpath)), "TEMP": str(cache), "TMP": str(cache),
                        "TMPDIR": str(cache), "HOME": str(profile), "APPDATA": str(profile), "LOCALAPPDATA": str(cache),
                        "SAL_USE_VCLPLUGIN": "svp"})
            stage = "PROCESS"
            code = _run_converter(command, cwd=root, env=env, timeout=90)
            if code != 0:
                raise JobOpsError("DOCX_PDF_EXPORT_FAILED", "LibreOffice PDF export failed.", failure_stage="PROCESS_EXIT", returncode=code)
            result = output / "input.pdf"
            stage = "OUTPUT"
            if not result.is_file() or result.is_symlink() or not 8 <= result.stat().st_size <= MAX_PDF_BYTES:
                raise JobOpsError("DOCX_PDF_EXPORT_FAILED", "LibreOffice did not produce a fresh bounded PDF.", failure_stage="OUTPUT")
            with result.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise JobOpsError("DOCX_PDF_EXPORT_FAILED", "LibreOffice output is not a PDF.", failure_stage="OUTPUT")
            # Existing destination is untouched until a fresh conversion succeeds.
            stage = "COMMIT"
            os.replace(result, pdf_path)
            stage = "CLEANUP"
    except subprocess.TimeoutExpired:
        raise JobOpsError("DOCX_PDF_EXPORT_FAILED", "Local PDF export exceeded its time budget.", failure_stage="TIMEOUT") from None
    except JobOpsError as exc:
        if exc.code == "DOCX_PDF_EXPORT_FAILED":
            raise
        raise JobOpsError("DOCX_PDF_EXPORT_FAILED", "Local PDF export could not finish safely.", failure_stage=stage) from None
    except OSError:
        raise JobOpsError("DOCX_PDF_EXPORT_FAILED", "Local PDF export could not finish safely.", failure_stage=stage) from None
