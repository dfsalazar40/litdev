"""Unit tests for the structured (JSON) logging setup."""

import json
import logging

from logging_utils import JsonFormatter, configure_logging


def test_json_formatter_produces_valid_json_with_expected_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="purchase processed",
        args=(),
        exc_info=None,
    )
    record.idempotency_key = "idem-1"
    record.status = "COMPLETED"

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "purchase processed"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["idempotency_key"] == "idem-1"
    assert payload["status"] == "COMPLETED"
    assert "timestamp" in payload


def test_json_formatter_includes_exception_info():
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="unexpected error",
            args=(),
            exc_info=sys.exc_info(),
        )

    payload = json.loads(formatter.format(record))
    assert "boom" in payload["exception"]


def test_configure_logging_replaces_existing_handlers_with_json_formatter():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    try:
        root.addHandler(logging.StreamHandler())  # simulate the Lambda runtime's default handler
        configure_logging()

        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original_handlers:
            root.addHandler(handler)


def test_log_output_is_a_single_json_line(capsys):
    try:
        configure_logging()
        logging.getLogger("test.logger").info("hello", extra={"user_id": "u-1"})
        captured = capsys.readouterr()
        payload = json.loads(captured.err.strip() or captured.out.strip())
        assert payload["message"] == "hello"
        assert payload["user_id"] == "u-1"
    finally:
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
