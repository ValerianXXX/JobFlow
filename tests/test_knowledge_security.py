from __future__ import annotations

import unittest

from _support import fixture_manifest, make_knowledge_root, project_temp
from jobops.errors import JobOpsError, SecurityBoundaryError
from jobops.knowledge import KnowledgeGateway
from jobops.locator import locate_knowledge_root


class KnowledgeSecurityTests(unittest.TestCase):
    def gateway(self, temp):
        manifest = fixture_manifest(temp / "manifest.json")
        root = make_knowledge_root(temp / "AI计划")
        location = locate_knowledge_root(temp, manifest, environment={}, local_config_path=temp / "absent.json")
        return KnowledgeGateway(location), root

    def test_hard_excluded_path_cannot_be_read(self) -> None:
        with project_temp() as temp:
            gateway, root = self.gateway(temp)
            forbidden = root / "vault" / "raw-attachments"
            forbidden.mkdir()
            (forbidden / "secret.txt").write_text("synthetic forbidden fixture", encoding="utf-8")
            with self.assertRaises(SecurityBoundaryError) as caught:
                gateway.read_text("personal_redacted", "raw-attachments/secret.txt")
            self.assertEqual(caught.exception.code, "HARD_EXCLUDED_PATH")
            self.assertEqual(gateway.search("synthetic forbidden fixture"), [])

    def test_path_traversal_is_rejected(self) -> None:
        with project_temp() as temp:
            gateway, _ = self.gateway(temp)
            with self.assertRaises(SecurityBoundaryError):
                gateway.read_text("personal_redacted", "../outside.txt")

    def test_search_returns_provenance_and_fingerprint(self) -> None:
        with project_temp() as temp:
            gateway, root = self.gateway(temp)
            note = root / "vault" / "PAI-CASE-TEST.md"
            note.write_text("# 已完成事项\n\n验证过的求职流程。\n", encoding="utf-8")
            records = gateway.search("求职")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].source_id, "personal_redacted")
            self.assertTrue(records[0].historical_completion)
            self.assertRegex(records[0].content_fingerprint, r"^sha256:[a-f0-9]{64}$")

    def test_wikilink_resolves_only_allowlisted_target(self) -> None:
        with project_temp() as temp:
            gateway, root = self.gateway(temp)
            (root / "vault" / "Target.md").write_text("ok", encoding="utf-8")
            self.assertEqual(gateway.resolve_wikilink("personal_redacted", "[[Target]]", "marker.md"), "Target.md")
            with self.assertRaises(JobOpsError):
                gateway.resolve_wikilink("personal_redacted", "[[Missing]]", "marker.md")


if __name__ == "__main__":
    unittest.main()

