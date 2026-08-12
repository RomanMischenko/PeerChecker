from unittest.mock import MagicMock
import tempfile
from pathlib import Path
import pytest

from app.config import Config
from app.storage import Storage
from app.bot import PeerCheckerBot
from telebot import types


@pytest.fixture
def bot_app():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_bot_peers.db"
        cfg = Config()
        cfg.TELEGRAM_BOT_TOKEN = "123456:dummy_token"
        cfg.TELEGRAM_ADMIN_IDS = [12345]

        st = Storage(db_path)
        app = PeerCheckerBot(cfg, st)
        app.bot.reply_to = MagicMock()
        yield app


def test_handle_start_authorized(bot_app):
    msg = MagicMock()
    msg.from_user.id = 12345
    msg.text = "/start"

    # Call handler registered for /start
    handler = [h for h in bot_app.bot.message_handlers if "start" in h["filters"]["commands"]][0]
    handler["function"](msg)

    assert bot_app.bot.reply_to.called
    args, kwargs = bot_app.bot.reply_to.call_args
    assert "Привет! Я бот поиска и валидации" in args[1]


def test_handle_start_unauthorized(bot_app):
    msg = MagicMock()
    msg.from_user.id = 99999
    msg.text = "/start"

    handler = [h for h in bot_app.bot.message_handlers if "start" in h["filters"]["commands"]][0]
    handler["function"](msg)

    assert bot_app.bot.reply_to.called
    args, kwargs = bot_app.bot.reply_to.call_args
    assert "У вас нет прав" in args[1]


def test_escape_markdown():
    from app.bot import escape_markdown
    assert escape_markdown("john_doe*test`[1]") == r"john\_doe\*test\`\[1]"
    assert escape_markdown("") == ""


def test_handle_check_now_busy(bot_app):
    msg = MagicMock()
    msg.from_user.id = 12345
    msg.text = "/check_now"

    # Acquire check lock to simulate ongoing check
    bot_app.check_lock.acquire()

    handler = [h for h in bot_app.bot.message_handlers if "check_now" in h["filters"]["commands"]][0]
    handler["function"](msg)

    assert bot_app.bot.reply_to.called
    args, kwargs = bot_app.bot.reply_to.call_args
    assert "уже выполняется" in args[1]

    bot_app.check_lock.release()

