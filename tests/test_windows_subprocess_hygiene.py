from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from _support import PROJECT, project_temp
from jobops.document_builder import export_docx_to_pdf, render_pdf_to_pngs
from jobops.secure_store import WindowsDPAPIStore


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class WindowsSubprocessHygieneTests(unittest.TestCase):
    def test_dpapi_helper_is_noninteractive_and_has_no_console_window(self) -> None:
        script = (
            PROJECT
            / ".agents"
            / "skills"
            / "job-application-operator"
            / "scripts"
            / "secure-store.ps1"
        )
        completed = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        with project_temp() as temp, mock.patch(
            "jobops.secure_store.subprocess.run", return_value=completed
        ) as run:
            WindowsDPAPIStore(script, local_app_data=temp / "local")._run("List")

        command = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertIn("-NonInteractive", command)
        self.assertEqual(kwargs["creationflags"], NO_WINDOW)

    def test_word_export_helper_has_no_console_window(self) -> None:
        with project_temp() as temp:
            docx = temp / "resume.docx"
            pdf = temp / "resume.pdf"
            docx.write_bytes(b"synthetic")

            def export(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                self.assertEqual(kwargs["creationflags"], NO_WINDOW)
                self.assertIn("-NonInteractive", command)
                Path(command[-1]).write_bytes(b"synthetic pdf")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with mock.patch("jobops.document_builder.subprocess.run", side_effect=export):
                export_docx_to_pdf(docx, pdf, temp / "export.ps1")

    def test_poppler_render_helper_has_no_console_window(self) -> None:
        with project_temp() as temp:
            pdf = temp / "resume.pdf"
            pdf.write_bytes(b"synthetic")

            def render(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                self.assertEqual(kwargs["creationflags"], NO_WINDOW)
                Path(str(command[-1]) + "-1.png").write_bytes(b"synthetic png")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with mock.patch("jobops.document_builder.subprocess.run", side_effect=render):
                pages = render_pdf_to_pngs(pdf, temp / "renders", "pdftoppm")
            self.assertEqual(len(pages), 1)


if __name__ == "__main__":
    unittest.main()
