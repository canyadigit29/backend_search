import json
import logging
import sys
from typing import Any, Dict
import contextvars

from app.core.config import settings

# Context variable to propagate request IDs into log records
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Common extras
        if hasattr(record, "request_id"):
            payload["request_id"] = getattr(record, "request_id")
        else:
            rid = request_id_var.get()
            if rid:
                payload["request_id"] = rid
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_data") and isinstance(getattr(record, "extra_data"), dict):
            # Merge structured fields under 'meta'
            payload.setdefault("meta", {}).update(getattr(record, "extra_data"))
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    level = getattr(logging, (settings.LOG_LEVEL or "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    # Clear pre-existing handlers to avoid duplicates in server restarts
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stdout)
    if settings.LOG_JSON:
        handler.setFormatter(JsonFormatter())
    else:
        fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(handler)


def log_info(logger: logging.Logger, message: str, extra_data: Dict[str, Any] | None = None) -> None:
    logger.info(message, extra={"extra_data": extra_data or {}})


def log_error(logger: logging.Logger, message: str, extra_data: Dict[str, Any] | None = None, exc_info: bool = False) -> None:
    logger.error(message, extra={"extra_data": extra_data or {}}, exc_info=exc_info)
