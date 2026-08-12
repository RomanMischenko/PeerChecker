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


def test_chunk_text():
    from app.bot import chunk_text
    assert chunk_text("") == []
    short_text = "Hello world"
    assert chunk_text(short_text, max_length=100) == [short_text]

    lines = ["Line " + str(i) for i in range(100)]
    long_text = "\n".join(lines)
    chunks = chunk_text(long_text, max_length=100)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 100


def test_peer_card_null_first_seen(bot_app):
    peer_data = {
        "login": "test_peer",
        "tribe_id": 604,
        "tribe_name": "Northern",
        "status": "VERIFIED",
        "xp": 100,
        "logtime": 5.0,
        "first_seen": None,
        "suspicion_reason": None,
    }
    text, markup = bot_app._build_peer_card_content(peer_data)
    assert "Неизвестно" in text
    assert "Нет" in text
    # Ensure no empty backticks `` `` exist in formatted text
    assert "``" not in text


def test_restore_monitoring_on_startup():
    """Ensure that if monitoring was active in storage, initializing PeerCheckerBot restores monitoring automatically."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_bot_restore.db"
        cfg = Config()
        cfg.TELEGRAM_BOT_TOKEN = "123456:dummy_token"
        cfg.TELEGRAM_ADMIN_IDS = [12345]

        # 1. First instance enables monitoring
        st1 = Storage(db_path)
        app1 = PeerCheckerBot(cfg, st1)
        app1.start_monitoring_loop()
        assert app1.monitoring_active is True
        assert st1.is_monitoring_active() is True
        app1.stop_monitoring_loop()

        # Re-set active to True in storage to simulate crash while monitoring was running
        st1.set_monitoring_active(True)

        # 2. Second instance starts up and should auto-restore monitoring
        st2 = Storage(db_path)
        app2 = PeerCheckerBot(cfg, st2)
        assert app2.monitoring_active is True
        # Clean up monitoring thread
        app2.stop_monitoring_loop()


