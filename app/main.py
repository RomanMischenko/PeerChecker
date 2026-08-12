import logging
from logging.handlers import TimedRotatingFileHandler
import os
import sys
import time
from app.config import config
from app.storage import Storage
from app.bot import PeerCheckerBot


def setup_timezone() -> None:
    """Apply configured timezone to Python runtime environment."""
    tz = config.TZ
    os.environ["TZ"] = tz
    if hasattr(time, "tzset"):
        try:
            time.tzset()
        except Exception as e:
            print(f"Warning: Could not apply timezone '{tz}': {e}", file=sys.stderr)


def setup_logging() -> None:
    """Configure system logging to stdout and daily rotating file."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(funcName)s): %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
    ]

    try:
        log_file = config.log_file
        file_handler = TimedRotatingFileHandler(
            filename=log_file,
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8",
            utc=False,
        )
        handlers.append(file_handler)
    except Exception as e:
        print(f"Warning: Could not setup file logging: {e}", file=sys.stderr)

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt=date_format,
        handlers=handlers,
    )

    # Suppress verbose telebot info logs
    logging.getLogger("telebot").setLevel(logging.WARNING)

    logging.info("=== Logging system initialized ===")


def main() -> None:
    setup_timezone()
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("Starting School 21 PeerChecker Bot...")

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        logger.error("Please fill required variables in .env (copy from .env.example)")
        sys.exit(1)

    # Initialize SQLite Storage
    storage = Storage(config.db_path)

    # Initialize Bot
    bot_app = PeerCheckerBot(config, storage)

    logger.info("Bot application initialized successfully. Listening for commands...")
    try:
        bot_app.start_polling()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.critical(f"Unhandled error in bot main loop: {e}", exc_info=True)
        sys.exit(1)
    finally:
        bot_app.stop_event.set()
        logger.info("Signaled background monitoring thread to stop.")


if __name__ == "__main__":
    main()
