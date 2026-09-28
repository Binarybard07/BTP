import logging
import sys
from typing import Any, Optional, Union

DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    level: Union[int, str] = logging.INFO,
    format_str: Optional[str] = None,
    date_format: Optional[str] = None,
    stream: Optional[Any] = None,
    force: bool = False,
) -> None:
    """
    Configures centralized logging for the equity_strategy package.

    Parameters:
        level: Logging level (e.g. logging.INFO, logging.DEBUG, 'INFO', etc.). Defaults to INFO.
        format_str: Format string for log messages. Defaults to standard timestamped format.
        date_format: Format string for timestamps. Defaults to '%Y-%m-%d %H:%M:%S'.
        stream: Output stream (defaults to sys.stdout).
        force: If True, resets existing handlers and forces re-configuration.
    """
    fmt = format_str or DEFAULT_LOG_FORMAT
    datefmt = date_format or DEFAULT_DATE_FORMAT

    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger()

    if root_logger.handlers and not force:
        root_logger.setLevel(level)
        return

    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt=datefmt,
        stream=stream or sys.stdout,
        force=force,
    )
