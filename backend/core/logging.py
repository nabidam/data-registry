import logging

from rich.logging import RichHandler


def setup_logging(debug: bool = True) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
