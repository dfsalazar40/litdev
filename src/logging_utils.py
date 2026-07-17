"""Structured (JSON) logging for CloudWatch Logs.

The Lambda runtime pre-attaches its own plain-text handler to the root logger
before our code ever runs, so simply calling logging.basicConfig() would not be
enough - it would leave that handler in place and our format would lose. This
module removes it and installs a JSON formatter instead, so every log line is a
single JSON object that CloudWatch Logs Insights can filter/query on.
"""

from __future__ import annotations

import json
import logging

_RESERVED_RECORD_KEYS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()) | {
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Anything passed via logger.info(..., extra={...}) ends up as plain
        # attributes on the record - surface those as structured fields too.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_KEYS:
                payload[key] = value

        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
