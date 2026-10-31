"""Preview the actual Companion popup with explicitly synthetic Chrome APIs.

No extension is installed, no permission is granted and no form is submitted.
This complements the worker contract test; it does not validate an installed
extension or a real employer flow. Open ?scenario=ready, failed or changed for
the other fixed states. Press Enter in this process to stop its loopback server.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from urllib.parse import parse_qs, urlsplit


PROJECT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "unconfirmed": ("PREPARING_APPLICATION", "GUIDED_PREPARATION_START_UNCONFIRMED"),
    "ready": ("REVIEW_PACKET_READY", ""),
    "failed": ("FORM_CAPTURE_FAILED", "GUIDED_PREPARATION_START_FAILED"),
    "changed": ("FORM_CAPTURE_FAILED", "GUIDED_PREPARATION_INPUT_CHANGED"),
}
MESSAGES = {
    "GUIDED_PREPARATION_START_UNCONFIRMED": "Preparation may already be running. Check its status before retrying.",
    "GUIDED_PREPARATION_START_FAILED": "Local preparation could not start. You may retry this import explicitly.",
    "GUIDED_PREPARATION_INPUT_CHANGED": "This import already has a different form. Check its preparation status before starting another import.",
    "": "Synthetic packet ready for local review.",
}


class Preview(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path in {"/popup.js", "/popup.css"}:
            filename = url.path.removeprefix("/")
            body = (PROJECT / "browser-companion" / filename).read_bytes()
            kind = "text/javascript" if filename.endswith(".js") else "text/css"
        elif url.path == "/":
            scenario = parse_qs(url.query).get("scenario", ["unconfirmed"])[0]
            if scenario not in SCENARIOS:
                self.send_error(400); return
            status, code = SCENARIOS[scenario]
            result = {"status": status, "code": code, "message": MESSAGES[code],
                      "mode": "JOB_CAPTURE", "intake_id": "GIN-SYNTHETIC-PREVIEW",
                      "automatic_retry": False, "real_external_actions": 0}
            state = {**result, "paired": True, "last_result": result}
            shim = """<script>
const previewState = __STATE__;
const previewMessages = [];
window.chrome = {
  runtime: {
    getManifest: () => ({version: '0.9.2'}),
    sendMessage: async message => {
      previewMessages.push(message.type);
      if (message.type === 'JOBFLOW_GET_STATUS') return previewState;
      return {status: 'BLOCKED', code: 'SYNTHETIC_PREVIEW_ONLY', automatic_retry: false};
    }
  },
  tabs: {query: async () => [{id: 1, url: 'https://example.invalid/synthetic-application'}]},
  permissions: {contains: async () => false, request: async () => false}
};
</script>""".replace("__STATE__", json.dumps(state))
            html = (PROJECT / "browser-companion/popup.html").read_text(encoding="utf-8")
            html = html.replace("<head>", "<head>" + shim)
            html = html.replace("<body>", '<body><p role="note" style="padding:8px;background:#fff3cd;color:#332701">Synthetic preview — no installed extension, employer or model call.</p>')
            body, kind = html.encode(), "text/html"
        else:
            self.send_error(404); return
        self.send_response(200)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Preview)
    def stop_on_input():
        sys.stdin.readline()
        server.shutdown()
    threading.Thread(target=stop_on_input, daemon=True).start()
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/", "synthetic_only": True}), flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
