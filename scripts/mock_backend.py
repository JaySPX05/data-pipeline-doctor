#!/usr/bin/env python3
"""Tiny stand-in for the real backend so you can test replay.py before P4 is done. Stdlib only.

  Terminal 1:  python scripts/mock_backend.py
  Terminal 2:  python scripts/replay.py --speed 3600x --auto-feedback --max-gap 0.2

Accepts POST /api/failures and POST /api/incidents/{id}/feedback and prints what it gets.
Fake "agent": says NEW the first time it sees a failure signature, then KNOWN_ISSUE once feedback
has been received for that signature (so you can see the learning loop work).
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

incidents, learned, counter = {}, set(), [0]


def signature(body):
    last = [l for l in body["log_tail"].splitlines() if l and not l.startswith(("[", " ", "Traceback", "rows_read"))]
    return f"{body['dag_id']}.{body['task_id']}|{last[0] if last else ''}"


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/failures":
            counter[0] += 1
            iid = f"MOCK-{counter[0]:04d}"
            sig = signature(body)
            verdict = "KNOWN_ISSUE" if sig in learned else "NEW"
            incidents[iid] = sig
            print(f"FAILURE  {iid} {body['dag_id']}.{body['task_id']} -> {verdict}  "
                  f"(recent_changes: {len(body.get('recent_changes', []))})")
            return self._send(200, {"incident_id": iid, "verdict": verdict})
        if self.path.startswith("/api/incidents/") and self.path.endswith("/feedback"):
            iid = self.path.split("/")[3]
            if iid in incidents:
                learned.add(incidents[iid])
            print(f"FEEDBACK {iid} verdict={body.get('verdict')} minutes={body.get('resolution_minutes')}")
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("Mock backend on http://localhost:8000  (Ctrl+C to stop)")
    HTTPServer(("", 8000), H).serve_forever()
