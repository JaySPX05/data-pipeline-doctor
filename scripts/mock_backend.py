#!/usr/bin/env python3
"""Stand-in for the real backend so you can test replay.py before everything is wired. Stdlib only.

  Terminal 1:  python scripts/mock_backend.py
  Terminal 2:  python scripts/replay.py --speed 3600x --auto-feedback --max-gap 0.2

Mimics the FROZEN schema (backend/app/models/schemas.py): rejects bad payloads with 422, like the
real API. Fake "agent": says NEW the first time it sees a failure signature, KNOWN_ISSUE after
feedback has been received for it (so you can watch the learning loop and `correct` flip).
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

FAILURE_REQUIRED = ["dag_id", "task_id", "occurred_at", "log_tail"]
FEEDBACK_REQUIRED = ["correct", "root_cause", "fix"]
incidents, learned, counter = {}, set(), [0]


def signature(body):
    last = [l for l in body["log_tail"].splitlines() if l.strip()][-2:]
    return f"{body['task_id']}|{last[0] if last else ''}"[:200]


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_GET(self):
        if self.path.startswith("/api/incidents/"):
            inc = incidents.get(self.path.split("/")[3])
            return self._send(200, inc) if inc else self._send(404, {"detail": "not found"})
        self._send(404, {"detail": "not found"})

    def do_POST(self):
        body = self._body()
        if self.path == "/api/failures":
            missing = [k for k in FAILURE_REQUIRED if k not in body]
            if missing or not isinstance(body.get("recent_changes", []), list):
                print(f"422 /api/failures missing={missing}")
                return self._send(422, {"detail": f"missing/invalid: {missing}"})
            counter[0] += 1
            iid = f"INC-MOCK-{counter[0]:04d}"
            sig = signature(body)
            verdict = "KNOWN_ISSUE" if sig in learned else "NEW"
            incidents[iid] = {"id": iid, "dag_id": body["dag_id"], "task_id": body["task_id"], "sig": sig,
                              "status": "diagnosed", "diagnosis": {"verdict": verdict, "memory_used": sig in learned}}
            print(f"FAILURE  {iid} {body['dag_id']}.{body['task_id']} -> {verdict}  "
                  f"(recent_changes: {len(body.get('recent_changes', []))})")
            return self._send(200, incidents[iid])
        if self.path.startswith("/api/incidents/") and self.path.endswith("/feedback"):
            iid = self.path.split("/")[3]
            missing = [k for k in FEEDBACK_REQUIRED if k not in body]
            if missing or not isinstance(body.get("correct"), bool):
                print(f"422 feedback missing={missing}")
                return self._send(422, {"detail": f"missing/invalid: {missing}"})
            if iid in incidents:
                learned.add(incidents[iid]["sig"])
            kind = "confirmed resolution" if body["correct"] else "CORRECTION"
            print(f"FEEDBACK {iid} correct={body['correct']} -> stored as {kind}, minutes={body.get('minutes_to_fix')}")
            return self._send(200, {"ok": True})
        self._send(404, {"detail": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("Mock backend on http://localhost:8000  (Ctrl+C to stop)")
    HTTPServer(("", 8000), H).serve_forever()
