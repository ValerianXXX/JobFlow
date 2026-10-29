"""Cheap local discovery. Registration is never proof of successful export."""
from __future__ import annotations

import importlib.util
import os
import shutil


def word_registered() -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"):
            return True
    except OSError:
        return False


def document_capabilities() -> dict:
    from .errors import JobOpsError
    from .local_pdf_export import libreoffice_binary, selected_pdf_provider

    try:
        provider = selected_pdf_provider()
        available = word_registered() if provider == "word" else libreoffice_binary() is not None
        configuration = "VALID"
    except JobOpsError:
        provider, available, configuration = None, False, "INVALID"
    return {
        "docx_generation": importlib.util.find_spec("docx") is not None,
        "pdf_export": "DETECTED_NOT_VERIFIED" if available else "UNAVAILABLE",
        "pdf_export_provider": provider.upper() if provider else None,
        "pdf_export_configuration": configuration,
        "page_preview": all(importlib.util.find_spec(name) is not None for name in ("pdfplumber", "pypdfium2")),
        "application_pdf_renderer": shutil.which("pdftoppm") is not None,
        "automatic_installation": False,
        "real_external_actions": 0,
    }
