import logging
import sys
from app.config import config
from app.storage import Storage
from app.bot import PeerCheckerBot


def setup_logging() -> None:
    """Configure system logging to stdout and file."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
    ]

    try:
        log_file = config.log_file
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    except Exception as e:
        print(f"Warning: Could not setup file logging: {e}", file=sys.stderr)

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=handlers,
    )


def main() -> None:
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
