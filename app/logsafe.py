"""Keep tokens, cookies and passwords out of the logs, whatever a library chooses to log."""
import logging
import re

QUIET_LOGGERS = ("garminconnect", "garmin_personal_api", "urllib3", "curl_cffi", "requests")
SECRET_NAMES = r"authorization|set-cookie|cookie|password|passwd|[a-z_-]*token|api[_-]?key|secret"
PATTERNS = [
    (re.compile(rf"""(["']?(?:{SECRET_NAMES})["']?\s*[:=]\s*)["']?(?:Bearer\s+|ApiKey\s+)?[^\s"',;}}]+""", re.I), r"\1[redacted]"),
    (re.compile(r"\b(Bearer|ApiKey)\s+[A-Za-z0-9._~+/=-]+"), r"\1 [redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), "[redacted]"),
]


def redact(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def install() -> None:
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # uvicorn only sets up its own loggers, so ours need a way out.
    app_logger = logging.getLogger("app")
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
        app_logger.addHandler(handler)
        app_logger.setLevel(logging.INFO)
    make_record = logging.getLogRecordFactory()
    if getattr(make_record, "redacts", False):
        return

    # The factory, not a filter: a logger's filter doesn't reach its children or other loggers.
    def redacting_record(*args, **kwargs):
        record = make_record(*args, **kwargs)
        message = record.getMessage()
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, None
        return record

    redacting_record.redacts = True
    logging.setLogRecordFactory(redacting_record)
