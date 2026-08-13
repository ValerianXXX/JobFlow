from __future__ import annotations

import shutil
import unittest

from _support import fixture_manifest, make_knowledge_root, project_temp, write_json
from jobops.errors import LocationError
from jobops.locator import locate_knowledge_root


class LocatorTests(unittest.TestCase):
    def test_bounded_search_survives_project_move(self) -> None:
        with project_temp() as temp:
            manifest = fixture_manifest(temp / "manifest.json")
            expected = make_knowledge_root(temp / "AI计划")
            original = temp / "project-original" / "nested"
            original.mkdir(parents=True)
            first = locate_knowledge_root(original, manifest, environment={}, local_config_path=temp / "absent.json", max_ancestor_depth=4)
            moved_parent = temp / "project-moved"
            shutil.move(str(original.parent), str(moved_parent))
            second = locate_knowledge_root(moved_parent / "nested", manifest, environment={}, local_config_path=temp / "absent.json", max_ancestor_depth=4)
            self.assertEqual(first.root, expected.resolve())
            self.assertEqual(second.root, expected.resolve())

    def test_zero_candidate_blocks(self) -> None:
        with project_temp() as temp:
            manifest = fixture_manifest(temp / "manifest.json")
            start = temp / "project"
            start.mkdir()
            with self.assertRaises(LocationError) as caught:
                locate_knowledge_root(start, manifest, environment={}, local_config_path=temp / "absent.json", max_ancestor_depth=1)
            self.assertEqual(caught.exception.code, "KNOWLEDGE_ROOT_NOT_FOUND")

    def test_multiple_candidates_block(self) -> None:
        with project_temp() as temp:
            manifest = fixture_manifest(temp / "manifest.json")
            make_knowledge_root(temp / "AI计划")
            start = temp / "project" / "nested"
            start.mkdir(parents=True)
            make_knowledge_root(temp / "project" / "AI计划")
            with self.assertRaises(LocationError) as caught:
                locate_knowledge_root(start, manifest, environment={}, local_config_path=temp / "absent.json", max_ancestor_depth=3)
            self.assertEqual(caught.exception.code, "MULTIPLE_KNOWLEDGE_ROOTS")
            self.assertEqual(len(caught.exception.details["candidates"]), 2)

    def test_invalid_environment_location_fails_closed(self) -> None:
        with project_temp() as temp:
            manifest = fixture_manifest(temp / "manifest.json")
            make_knowledge_root(temp / "AI计划")
            env_doc = temp / "env-location.json"
            write_json(env_doc, {"knowledge_root": str(temp / "missing")})
            with self.assertRaises(LocationError) as caught:
                locate_knowledge_root(temp, manifest, environment={"JOBOPS_KNOWLEDGE_MANIFEST": str(env_doc)}, local_config_path=temp / "absent.json")
            self.assertEqual(caught.exception.code, "ENV_CANDIDATE_INVALID")

    def test_private_location_precedes_nearby_search(self) -> None:
        with project_temp() as temp:
            manifest = fixture_manifest(temp / "manifest.json")
            selected = make_knowledge_root(temp / "private-vault")
            make_knowledge_root(temp / "AI计划")
            private = temp / "private.json"
            write_json(private, {"knowledge_root": str(selected)})
            result = locate_knowledge_root(temp, manifest, environment={}, local_config_path=private)
            self.assertEqual(result.root, selected.resolve())
            self.assertEqual(result.discovery_method, "private_location_config")


if __name__ == "__main__":
    unittest.main()

