"""Turn a noisy task log into a stable failure fingerprint.

Two failures with the same root cause should get the same fingerprint even though
their timestamps, hostnames, row counts and run ids differ.  Owner: P1 (Jay).
"""
import hashlib
import re

# Order matters: specific patterns before the generic number pattern.
_NOISE: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<UUID>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<HEX>"),
    (re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{12,}\b", re.I), "<HASH>"),
    (re.compile(r"\b(?:ip|host|worker|executor)-[\w.-]+", re.I), "<HOST>"),
    (re.compile(r"\bo\d+(?=\.)"), "o<N>"),  # py4j JVM object ids: "calling o697.save"
    (re.compile(r"(?:/[\w.\-]+)+\.(?:py|scala|java|sql)"), "<PATH>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<N>"),
    (re.compile(r"\s+"), " "),
]

# e.g. "KeyError: 'customer_id'", "botocore.exceptions.ClientError: ... ExpiredToken",
#      "java.lang.OutOfMemoryError: Java heap space", "kafka.errors.KafkaTimeoutError: ...",
#      "Caused by: java.sql.SQLException: ..." (Java cause chains),
#      ": java.lang.OutOfMemoryError: Java heap space" (py4j prints the JVM cause on its own line)
# We scan from the bottom, so the deepest cause wins over its wrapper (Py4JJavaError, etc.).
_ERROR_LINE = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)?(?:ERROR\s*-?\s*)?(?:Caused by:\s*|:\s+)?"
    r"(?P<cls>[A-Za-z_][\w.]*(?:Error|Exception|Timeout|Exhausted|Failure|Failed))\b:?\s*(?P<msg>.*)$"
)


def normalize(text: str) -> str:
    out = text
    for pattern, repl in _NOISE:
        out = pattern.sub(repl, out)
    return out.strip()


def extract_error(log: str) -> tuple[str, str]:
    """Return (error_class, message) from the last error-looking line of the log."""
    lines = [line for line in log.strip().splitlines() if line.strip()]
    for line in reversed(lines):
        m = _ERROR_LINE.match(line)
        if m:
            cls = m.group("cls").split(".")[-1]
            return cls, m.group("msg").strip()[:500]
    return "UnknownError", (lines[-1].strip()[:500] if lines else "")


def fingerprint(task_id: str, log: str) -> tuple[str, str, str]:
    """Return (fingerprint, error_class, error_message)."""
    error_class, message = extract_error(log)
    signature = f"{task_id}|{error_class}|{normalize(message)}"
    digest = hashlib.sha1(signature.encode()).hexdigest()[:8]
    return f"fp_{digest}", error_class, message
