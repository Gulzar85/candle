"""Structured (JSON) log formatting for production log aggregation.

A small ``logging.Formatter`` subclass rather than a third-party JSON logging
package — the output shape needed here (one JSON object per line, standard
fields) is simple enough that an extra dependency isn't warranted.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime


class JSONFormatter(logging.Formatter):
    """Render each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)
