import logging
import json
from typing import Any, Dict
from contextvars import ContextVar


# Context variable to hold request-id for the current task
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Injects request_id from contextvars into log records."""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        try:
            record.request_id = request_id_ctx.get()
        except Exception:
            record.request_id = "-"
        return True


class JsonOrKeyValueFormatter(logging.Formatter):
    """
    Lightweight formatter. If message is a dict, emit JSON. Otherwise emit key=value logfmt.
    Always includes level, logger, and request_id.
    """

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        base: Dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
        }

        # Capture any structured extras provided via logger.extra
        extra_data: Dict[str, Any] = {}
        if hasattr(record, "extra_data") and isinstance(getattr(record, "extra_data"), dict):
            extra_data = getattr(record, "extra_data") or {}

        msg = record.getMessage()
        if isinstance(record.args, dict):
            # If args is a dict and msg has %s etc, fall back to msg only
            pass

        # If the log message is a serialized dict, prefer as-is
        if isinstance(msg, dict):
            merged = {**base, **msg}
            if extra_data:
                merged["meta"] = {**(merged.get("meta", {}) or {}), **extra_data}
            return json.dumps(merged, default=str)

        # Try to parse msg as JSON for convenience
        try:
            parsed = json.loads(msg)
            if isinstance(parsed, dict):
                merged = {**base, **parsed}
                if extra_data:
                    merged["meta"] = {**(merged.get("meta", {}) or {}), **extra_data}
                return json.dumps(merged, default=str)
        except Exception:
            pass

        # Fallback to key=value format
        extras = []
        extras.append(f"ts={self.formatTime(record, datefmt='%Y-%m-%dT%H:%M:%S%z')}")
        for k, v in base.items():
            if k == "level":
                continue
            # quote values that include whitespace
            val = str(v)
            if any(ch.isspace() for ch in val):
                val = f'"{val}"'
            extras.append(f"{k}={val}")
        # Append structured extras (flatten simple types, JSON-encode complex)
        if extra_data:
            for k, v in extra_data.items():
                try:
                    if isinstance(v, (str, int, float)):
                        sval = str(v)
                        if isinstance(v, str) and any(ch.isspace() for ch in sval):
                            sval = f'"{sval}"'
                        extras.append(f"{k}={sval}")
                    elif isinstance(v, bool) or v is None:
                        extras.append(f"{k}={v}")
                    else:
                        extras.append(f"{k}={json.dumps(v, separators=(',', ':'))}")
                except Exception:
                    extras.append(f"{k}=")
        # Append the original message last
        extras.append(f"msg=\"{msg}\"")
        return " ".join(extras)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure application-wide logging with request-id context and sensible defaults."""
    root = logging.getLogger()
    root.setLevel(level)

    # Clear existing handlers to avoid duplicate logs on reload
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(JsonOrKeyValueFormatter())
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)

    # Quiet some noisy loggers, but keep httpx informational requests
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def set_request_id(request_id: str) -> None:
    request_id_ctx.set(request_id)
