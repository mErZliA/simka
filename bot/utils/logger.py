import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bot.config import LOG_FILE


def setup_logger(name: str = "simroulette") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s │ %(levelname)-7s │ %(name)-20s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    if LOG_FILE:
        try:
            log_path = Path(LOG_FILE)
            if not log_path.is_absolute():
                log_path = Path(__file__).resolve().parents[2] / log_path
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as exc:
            logger.warning("File logging disabled: %s", exc)
    logger.propagate = True
    return logger
