import logging
import os


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        ts = self.formatTime(record, self.datefmt)
        kv = [f"time={ts}"] + [f"{k}={v}" for k, v in base.items()]
        return " ".join(kv)


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(message)s")
    root = logging.getLogger()
    for h in root.handlers:
        h.setFormatter(KeyValueFormatter())
