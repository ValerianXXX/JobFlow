"""Synthetic material-review UI fixture: actual HTTP/PDF, memory-store double."""
import json
import threading

from test_application_material_review import ApplicationMaterialReviewTests
from jobops.onboarding_server import create_server


def main():
    fixture = ApplicationMaterialReviewTests('test_list_and_download_are_bound_and_do_not_grant_approval')
    server = None
    try:
        fixture.setUp()
        before = fixture.snapshot()[0]
        listed = fixture.service.application_materials(fixture.request)
        server = create_server(fixture.service, token='synthetic-material-review-ui')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(json.dumps({'url': server.url, 'application_id': fixture.application_id,
                          'materials': listed['materials'], 'synthetic_only': True}), flush=True)
        input()
        after = fixture.snapshot()[0]
        unchanged = all(before[name] == after[name] for name in (
            'applications', 'review_packets', 'materials', 'approvals', 'external_action_attempts'))
        assert unchanged, 'Read-only application state changed'
        print(json.dumps({'application_state_unchanged': unchanged, 'new_model_calls': 0,
                          'store': 'MEMORY_CONTRACT_DOUBLE', 'independent_human': False}), flush=True)
    finally:
        if server:
            server.shutdown()
            server.server_close()
        fixture.doCleanups()


if __name__ == '__main__':
    main()
