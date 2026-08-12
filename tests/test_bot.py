from unittest.mock import MagicMock, patch
import time
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


def test_handle_help_authorized(bot_app):
    msg = MagicMock()
    msg.from_user.id = 12345
    msg.text = "/help"

    handler = [h for h in bot_app.bot.message_handlers if "help" in h["filters"]["commands"]][0]
    handler["function"](msg)

    assert bot_app.bot.reply_to.called
    args, kwargs = bot_app.bot.reply_to.call_args
    assert "/help" in args[1]
    assert "Привет! Я бот поиска и валидации" in args[1]


def test_handle_status_authorized(bot_app):
    msg = MagicMock()
    msg.from_user.id = 12345
    msg.text = "/status"

    handler = [h for h in bot_app.bot.message_handlers if "status" in h["filters"]["commands"]][0]
    handler["function"](msg)

    assert bot_app.bot.reply_to.called
    args, kwargs = bot_app.bot.reply_to.call_args
    assert "PeerChecker Status Report" in args[1]
    assert "[Статус системы]" in args[1]
    assert "[Статистика БД]" in args[1]


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
        with patch.object(PeerCheckerBot, "run_check_and_notify"):
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
            app2.stop_monitoring_loop()


def test_restore_interrupted_check_on_startup():
    """Ensure that if a check was in progress during crash, initializing PeerCheckerBot resumes the check."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_bot_restore_check.db"
        cfg = Config()
        cfg.TELEGRAM_BOT_TOKEN = "123456:dummy_token"
        cfg.TELEGRAM_ADMIN_IDS = [12345]

        st = Storage(db_path)
        st.set_check_in_progress(True)

        with patch.object(PeerCheckerBot, "run_check_and_notify") as mock_check:
            app = PeerCheckerBot(cfg, st)
            time.sleep(0.1)
            assert mock_check.called
            assert app is not None


def test_escape_code_block():
    from app.bot import escape_code_block
    assert escape_code_block("26_08_NN") == "26_08_NN"
    assert escape_code_block("hello`world") == "hello'world"
    assert escape_code_block("") == ""


def test_handle_export_skipped_wave_only(bot_app):
    bot_app.storage.save_peer({
        "login": "skipped_peer",
        "tribe_id": 604,
        "tribe_name": "Northern",
        "status": "SKIPPED_WAVE",
        "xp": 0,
        "logtime": 0.0,
    })

    msg = MagicMock()
    msg.from_user.id = 12345
    msg.text = "/export"

    handler = [h for h in bot_app.bot.message_handlers if "export" in h["filters"]["commands"]][0]
    handler["function"](msg)

    assert bot_app.bot.reply_to.called
    calls = bot_app.bot.reply_to.call_args_list
    assert any("нет пиров для отчета" in str(call) for call in calls)


def test_handle_status_callback_peer_not_found(bot_app):
    bot_app.bot.answer_callback_query = MagicMock()
    call = MagicMock()
    call.from_user.id = 12345
    call.data = "set_status:non_existent_peer:VERIFIED"
    call.id = "call_123"

    handler = [h for h in bot_app.bot.callback_query_handlers if h["filters"]["func"](call)][0]
    handler["function"](call)

    assert bot_app.bot.answer_callback_query.called
    args, kwargs = bot_app.bot.answer_callback_query.call_args
    assert "не найден" in args[1]


def test_handle_peers_skipped_wave(bot_app):
    bot_app.storage.save_peer({
        "login": "wave_peer",
        "tribe_id": 604,
        "tribe_name": "Northern",
        "status": "SKIPPED_PEERS",
        "xp": 0,
        "logtime": 0.0,
    })

    msg = MagicMock()
    msg.from_user.id = 12345
    msg.text = "/peers SKIPPED_PEERS"

    handler = [h for h in bot_app.bot.message_handlers if "peers" in h["filters"]["commands"]][0]
    bot_app.bot.send_document = MagicMock()
    handler["function"](msg)

    assert bot_app.bot.send_document.called
    args, kwargs = bot_app.bot.send_document.call_args
    assert "Skipped Peers" in kwargs.get("caption", "")


def test_handle_peers_expelled(bot_app):
    bot_app.storage.save_peer({
        "login": "expelled_peer",
        "tribe_id": 604,
        "tribe_name": "Northern",
        "status": "EXPELLED",
        "xp": 0,
        "logtime": 0.0,
    })

    msg = MagicMock()
    msg.from_user.id = 12345
    msg.text = "/peers EXPELLED"

    handler = [h for h in bot_app.bot.message_handlers if "peers" in h["filters"]["commands"]][0]
    bot_app.bot.send_document = MagicMock()
    handler["function"](msg)

    assert bot_app.bot.send_document.called
    args, kwargs = bot_app.bot.send_document.call_args
    assert "Expelled" in kwargs.get("caption", "")


def test_handle_export_with_expelled(bot_app):
    bot_app.storage.save_peer({
        "login": "expelled_peer",
        "tribe_id": 604,
        "tribe_name": "Northern",
        "status": "EXPELLED",
        "xp": 0,
        "logtime": 0.0,
    })

    msg = MagicMock()
    msg.from_user.id = 12345
    msg.text = "/export"

    handler = [h for h in bot_app.bot.message_handlers if "export" in h["filters"]["commands"]][0]
    bot_app.bot.send_document = MagicMock()
    handler["function"](msg)

    assert bot_app.bot.send_document.called


def test_chunk_text_long_line_resets_chunk():
    from app.bot import chunk_text
    long_line = "A" * 150
    normal_line = "B" * 20
    text = f"{long_line}\n{normal_line}"
    chunks = chunk_text(text, max_length=100)
    assert len(chunks) >= 3
    assert all(len(c) <= 100 for c in chunks)


def test_run_check_and_notify_expelled_and_restored(bot_app):
    """Test full cycle: peer present -> peer disappears (EXPELLED notification) -> peer restored (VERIFIED re-validation)."""
    with patch("app.bot.S21ApiClient") as mock_api_cls:
        mock_api = MagicMock()
        mock_api_cls.return_value.__enter__.return_value = mock_api

        # Mock validate_peer to return VERIFIED
        bot_app.validator.validate_peer = MagicMock(side_effect=lambda client, login, **kwargs: {
            "login": login,
            "status": "VERIFIED",
            "is_skipped": False,
            "total_xp": 1000,
            "logtime": 10.0,
            "suspicion_reason_text": "Прошел проверку",
            "details": {},
        })

        bot_app._send_to_admins = MagicMock()

        # Run 1: API returns peer_a and peer_b
        mock_api.get_coalition_participants.side_effect = lambda tid, **kw: ["peer_a", "peer_b"] if tid == 604 else []
        bot_app.run_check_and_notify()

        assert bot_app.storage.get_peer("peer_a")["status"] == "VERIFIED"
        assert bot_app.storage.get_peer("peer_b")["status"] == "VERIFIED"

        # Run 2: API returns only peer_b (peer_a disappeared!)
        mock_api.get_coalition_participants.side_effect = lambda tid, **kw: ["peer_b"] if tid == 604 else []
        bot_app.run_check_and_notify()

        assert bot_app.storage.get_peer("peer_a")["status"] == "EXPELLED"
        assert bot_app.storage.get_peer("peer_b")["status"] == "VERIFIED"
        # Admin should have received an expelled notification
        assert any("Обнаружены отчислившиеся пиры" in str(call) for call in bot_app._send_to_admins.call_args_list)

        # Run 3: API returns peer_a and peer_b again (peer_a restored!)
        mock_api.get_coalition_participants.side_effect = lambda tid, **kw: ["peer_a", "peer_b"] if tid == 604 else []
        bot_app.run_check_and_notify()

        assert bot_app.storage.get_peer("peer_a")["status"] == "VERIFIED"
        assert bot_app.storage.get_peer("peer_b")["status"] == "VERIFIED"







