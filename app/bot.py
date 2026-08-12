import functools
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from typing import Callable, Any
import telebot
from telebot import types

from app.config import Config
from app.s21_api import S21ApiClient, S21ApiError
from app.storage import Storage
from app.validator import PeerValidator

logger = logging.getLogger(__name__)


def admin_only(func: Callable) -> Callable:
    """Decorator to restrict bot commands to authorized admin Telegram User IDs."""
    @functools.wraps(func)
    def wrapper(self: Any, message: types.Message, *args: Any, **kwargs: Any) -> Any:
        user_id = message.from_user.id
        if self.config.TELEGRAM_ADMIN_IDS and user_id not in self.config.TELEGRAM_ADMIN_IDS:
            logger.warning(f"Unauthorized access attempt by user_id {user_id}")
            self.bot.reply_to(message, "⛔ У вас нет прав для управления этим ботом.")
            return None
        return func(self, message, *args, **kwargs)
    return wrapper


class PeerCheckerBot:
    def __init__(self, config: Config, storage: Storage):
        self.config = config
        self.storage = storage
        self.bot = telebot.TeleBot(self.config.TELEGRAM_BOT_TOKEN)
        self.validator = PeerValidator(
            min_xp=self.config.MIN_XP, min_logtime=self.config.MIN_LOGTIME
        )

        self.monitoring_active = False
        self.monitoring_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.check_lock = threading.Lock()

        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register Telegram bot command handlers."""
        @self.bot.message_handler(commands=["start"])
        @admin_only
        def handle_start(message: types.Message) -> None:
            text = (
                "👋 **Привет! Я бот поиска и валидации новых пиров Школы 21.**\n\n"
                "📌 **Доступные команды:**\n"
                "/start — Справка и приветствие\n"
                "/start_monitoring — Запуск автоматического фонового мониторинга\n"
                "/stop_monitoring — Остановка фонового мониторинга\n"
                "/check_now — Запуск проверки вне очереди\n"
                "/status — Статус работы бота и статистика базы данных\n"
                "/peer <login> — Карточка пира с возможностью смены статуса\n"
                "/set_status <login> <verified|suspicious> — Ручная смена статуса пира\n"
            )
            self.bot.reply_to(message, text, parse_mode="Markdown")

        @self.bot.message_handler(commands=["start_monitoring"])
        @admin_only
        def handle_start_monitoring(message: types.Message) -> None:
            if self.monitoring_active:
                self.bot.reply_to(message, "ℹ️ Фоновый мониторинг уже запущен.")
                return

            self.monitoring_active = True
            self.stop_event.clear()
            self.monitoring_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
            self.monitoring_thread.start()
            self.bot.reply_to(
                message,
                f"✅ **Фоновый мониторинг успешно запущен!**\n"
                f"Интервал проверки: каждый(е) {self.config.CHECK_INTERVAL_MINUTES} мин.",
                parse_mode="Markdown",
            )

        @self.bot.message_handler(commands=["stop_monitoring"])
        @admin_only
        def handle_stop_monitoring(message: types.Message) -> None:
            if not self.monitoring_active:
                self.bot.reply_to(message, "ℹ️ Фоновый мониторинг не запущен.")
                return

            self.monitoring_active = False
            self.stop_event.set()
            self.bot.reply_to(message, "🛑 **Фоновый мониторинг остановлен.**", parse_mode="Markdown")

        @self.bot.message_handler(commands=["check_now"])
        @admin_only
        def handle_check_now(message: types.Message) -> None:
            self.bot.reply_to(message, "🔎 **Запускаю мгновенную проверку пиров...**", parse_mode="Markdown")
            threading.Thread(target=self.run_check_and_notify, daemon=True).start()

        @self.bot.message_handler(commands=["status"])
        @admin_only
        def handle_status(message: types.Message) -> None:
            stats = self.storage.get_stats()
            last_check = self.storage.get_last_check_info()

            status_str = "🟢 Запущен" if self.monitoring_active else "🔴 Остановлен"
            last_check_str = last_check["timestamp"] if last_check else "Еще не проводилась"

            text = (
                f"📊 **Текущий статус бота PeerChecker**\n\n"
                f"• **Мониторинг:** {status_str}\n"
                f"• **Интервал:** {self.config.CHECK_INTERVAL_MINUTES} мин.\n"
                f"• **Последняя проверка:** `{last_check_str}`\n"
                f"• **Всего пиров в БД:** {stats.get('total', 0)}\n"
                f"  - ✅ Проверенные (`VERIFIED`): {stats.get('total_verified', 0)}\n"
                f"  - ⚠️ Подозрительные (`SUSPICIOUS`): {stats.get('total_suspicious', 0)}\n\n"
                f"📌 **По трайбам:**\n"
            )

            by_tribe = stats.get("by_tribe", {})
            if not by_tribe:
                text += "_Данных по трайбам пока нет_\n"
            else:
                for tid, tdata in by_tribe.items():
                    text += (
                        f"• **{tdata['tribe_name']}** (ID {tid}): "
                        f"всего {tdata['total']} (✅ {tdata['verified']} / ⚠️ {tdata['suspicious']})\n"
                    )

            self.bot.reply_to(message, text, parse_mode="Markdown")

        @self.bot.message_handler(commands=["peer"])
        @admin_only
        def handle_peer_info(message: types.Message) -> None:
            parts = message.text.strip().split()
            if len(parts) < 2:
                self.bot.reply_to(message, "⚠️ Укажите логин пира. Пример: `/peer ivanov-ivan`", parse_mode="Markdown")
                return

            login = parts[1].strip()
            peer = self.storage.get_peer(login)
            if not peer:
                self.bot.reply_to(message, f"❌ Пир `{login}` не найден в базе данных.", parse_mode="Markdown")
                return

            self._send_peer_card(message.chat.id, peer)

        @self.bot.message_handler(commands=["set_status"])
        @admin_only
        def handle_set_status(message: types.Message) -> None:
            parts = message.text.strip().split()
            if len(parts) < 3:
                self.bot.reply_to(
                    message,
                    "⚠️ Формат команды: `/set_status <login> <verified|suspicious>`",
                    parse_mode="Markdown",
                )
                return

            login = parts[1].strip()
            new_status = parts[2].strip().upper()

            if new_status not in ("VERIFIED", "SUSPICIOUS"):
                self.bot.reply_to(message, "❌ Статус должен быть `VERIFIED` или `SUSPICIOUS`.", parse_mode="Markdown")
                return

            updated = self.storage.update_peer_status(login, new_status, is_manual=True)
            if updated:
                self.bot.reply_to(
                    message,
                    f"✅ Статус пира `{login}` успешно изменен на **{new_status}** (ручная модерация).",
                    parse_mode="Markdown",
                )
            else:
                self.bot.reply_to(message, f"❌ Пир `{login}` не найден в базе данных.", parse_mode="Markdown")

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("set_status:"))
        def handle_status_callback(call: types.CallbackQuery) -> None:
            user_id = call.from_user.id
            if self.config.TELEGRAM_ADMIN_IDS and user_id not in self.config.TELEGRAM_ADMIN_IDS:
                self.bot.answer_callback_query(call.id, "⛔ Нет прав доступа.", show_alert=True)
                return

            # Format: set_status:<login>:<status>
            parts = call.data.split(":")
            if len(parts) == 3:
                login, status = parts[1], parts[2]
                self.storage.update_peer_status(login, status, is_manual=True)
                self.bot.answer_callback_query(call.id, f"Статус пира {login} изменен на {status}")

                peer = self.storage.get_peer(login)
                if peer:
                    text, markup = self._build_peer_card_content(peer)
                    try:
                        self.bot.edit_message_text(
                            text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown"
                        )
                    except Exception:
                        pass

    def _build_peer_card_content(self, peer: dict[str, Any]) -> tuple[str, types.InlineKeyboardMarkup]:
        """Format peer card text and inline buttons."""
        login = peer["login"]
        status = peer["status"]
        status_emoji = "✅ VERIFIED" if status == "VERIFIED" else "⚠️ SUSPICIOUS"
        manual_flag = " (изменено вручную)" if peer.get("is_manual") else ""

        text = (
            f"👤 **Карточка пира `{login}`**\n\n"
            f"• **Трайб:** {peer['tribe_name']} (ID {peer['tribe_id']})\n"
            f"• **Статус:** {status_emoji}{manual_flag}\n"
            f"• **Суммарный XP:** {peer.get('xp', 0)}\n"
            f"• **Логтайм:** {peer.get('logtime', 0.0):.2f} ч/нед\n"
            f"• **Причина / Примечание:** `{peer.get('suspicion_reason') or 'Нет'}`\n"
            f"• **Первое обнаружение:** `{peer.get('first_seen')}`\n"
        )

        markup = types.InlineKeyboardMarkup()
        btn_v = types.InlineKeyboardButton("✅ Установить VERIFIED", callback_data=f"set_status:{login}:VERIFIED")
        btn_s = types.InlineKeyboardButton("⚠️ Установить SUSPICIOUS", callback_data=f"set_status:{login}:SUSPICIOUS")
        markup.add(btn_v, btn_s)
        return text, markup

    def _send_peer_card(self, chat_id: int, peer: dict[str, Any]) -> None:
        text, markup = self._build_peer_card_content(peer)
        self.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

    def _monitoring_loop(self) -> None:
        """Background thread target for periodic monitoring."""
        logger.info("Monitoring loop started.")
        while not self.stop_event.is_set():
            try:
                self.run_check_and_notify()
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}", exc_info=True)

            # Sleep in small steps to allow fast cancellation
            interval_sec = self.config.CHECK_INTERVAL_MINUTES * 60
            for _ in range(int(interval_sec)):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

        logger.info("Monitoring loop stopped.")

    def run_check_and_notify(self) -> None:
        """Core monitoring logic: queries S21 OpenAPI, skips existing DB peers, validates new peers, and notifies admins."""
        if not self.check_lock.acquire(blocking=False):
            logger.warning("Check already in progress. Skipping duplicate run.")
            return

        try:
            logger.info("Starting peer scan across target coalitions...")
            api_client = S21ApiClient(
                login=self.config.S21_LOGIN,
                password=self.config.S21_PASSWORD,
            )

            known_logins = self.storage.get_known_logins()
            logger.info(f"Loaded {len(known_logins)} existing known logins from database.")

            new_peers_by_tribe: dict[int, list[dict[str, Any]]] = {}
            all_new_peers: list[dict[str, Any]] = []

            for tribe_id, tribe_name in self.config.TARGET_COALITIONS.items():
                try:
                    participant_logins = api_client.get_coalition_participants(tribe_id)
                    logger.info(f"Fetched {len(participant_logins)} total logins for tribe {tribe_name} ({tribe_id}).")

                    # Deduplication filter: only check logins not yet in SQLite DB!
                    unprocessed_logins = [l for l in participant_logins if l not in known_logins]
                    logger.info(f"Found {len(unprocessed_logins)} new (unprocessed) logins for tribe {tribe_name}.")

                    tribe_new_peers = []
                    for login in unprocessed_logins:
                        try:
                            val_res = self.validator.validate_peer(api_client, login)
                            val_res["tribe_id"] = tribe_id
                            val_res["tribe_name"] = tribe_name
                            val_res["xp"] = val_res["total_xp"]
                            val_res["logtime"] = val_res["logtime"]
                            tribe_new_peers.append(val_res)
                            all_new_peers.append(val_res)
                            # Update known logins set in memory for this run
                            known_logins.add(login)
                        except Exception as e:
                            logger.error(f"Error validating peer {login}: {e}")

                    if tribe_new_peers:
                        new_peers_by_tribe[tribe_id] = tribe_new_peers

                except Exception as e:
                    logger.error(f"Error checking coalition {tribe_id} ({tribe_name}): {e}")

            # Save new peers to database
            if all_new_peers:
                self.storage.save_peers_batch(all_new_peers)

            now_str = datetime.now().strftime("%d.%m.%Y %H:%M")

            # Notification Logic
            if not all_new_peers:
                report_text = (
                    f"ℹ️ **Статус проверки пиров Школы 21**\n\n"
                    f"• **Результат:** Новых пиров не обнаружено.\n"
                    f"• **Последняя проверка:** `{now_str}`"
                )
                self._send_to_admins(report_text)
                self.storage.log_check_run(0, "Новых пиров не обнаружено")
                return

            # Summary for newly found peers
            total_new = len(all_new_peers)
            summary_text = (
                f"🚨 **Обнаружены новые пиры!** ({total_new} чел.)\n"
                f"• **Время проверки:** `{now_str}`\n\n"
                f"📊 **Распределение по трайбам:**\n"
            )

            for tid, tname in self.config.TARGET_COALITIONS.items():
                tpeers = new_peers_by_tribe.get(tid, [])
                v_count = sum(1 for p in tpeers if p["status"] == "VERIFIED")
                s_count = sum(1 for p in tpeers if p["status"] == "SUSPICIOUS")
                summary_text += f"• **{tname}:** {len(tpeers)} новых (✅ {v_count} verified / ⚠️ {s_count} suspicious)\n"

            # Generate report files per tribe
            files_to_send = []
            temp_dir = tempfile.mkdtemp()

            for tid, tname in self.config.TARGET_COALITIONS.items():
                tpeers = new_peers_by_tribe.get(tid, [])
                if not tpeers:
                    continue

                verified_peers = [p for p in tpeers if p["status"] == "VERIFIED"]
                suspicious_peers = [p for p in tpeers if p["status"] == "SUSPICIOUS"]

                # Verified file
                if verified_peers:
                    v_path = os.path.join(temp_dir, f"{tname}_verified.txt")
                    with open(v_path, "w", encoding="utf-8") as f:
                        f.write(f"=== Список проверенных пиров (VERIFIED) — Трайб {tname} ===\n")
                        f.write(f"Дата проверки: {now_str}\n\n")
                        for p in verified_peers:
                            f.write(f"• Логин: {p['login']} | XP: {p['xp']} | Логтайм: {p['logtime']:.2f} ч/нед\n")
                    files_to_send.append(v_path)

                # Suspicious file
                if suspicious_peers:
                    s_path = os.path.join(temp_dir, f"{tname}_suspicious.txt")
                    with open(s_path, "w", encoding="utf-8") as f:
                        f.write(f"=== Список подозрительных пиров (SUSPICIOUS) — Трайб {tname} ===\n")
                        f.write(f"Дата проверки: {now_str}\n\n")
                        for p in suspicious_peers:
                            f.write(
                                f"• Логин: {p['login']} | XP: {p['xp']} | Логтайм: {p['logtime']:.2f} ч/нед\n"
                                f"  Причина: {p.get('suspicion_reason_text', 'Неизвестно')}\n\n"
                            )
                    files_to_send.append(s_path)

            # Send summary + files to all admins
            self._send_to_admins(summary_text, files=files_to_send)

            # Cleanup temp files
            for fp in files_to_send:
                try:
                    os.remove(fp)
                except Exception:
                    pass

            self.storage.log_check_run(total_new, f"Найдено новых: {total_new}")

        finally:
            self.check_lock.release()

    def _send_to_admins(self, text: str, files: list[str] | None = None) -> None:
        """Send notification text message and optional file attachments to configured admin Telegram IDs."""
        if not self.config.TELEGRAM_ADMIN_IDS:
            logger.warning("No TELEGRAM_ADMIN_IDS configured to send reports.")
            return

        for admin_id in self.config.TELEGRAM_ADMIN_IDS:
            try:
                self.bot.send_message(admin_id, text, parse_mode="Markdown")
                if files:
                    for fp in files:
                        with open(fp, "rb") as doc:
                            self.bot.send_document(admin_id, doc)
            except Exception as e:
                logger.error(f"Failed to send report to admin {admin_id}: {e}")

    def start_polling(self) -> None:
        """Start Telegram bot infinity polling."""
        logger.info("Starting TeleBot infinity polling...")
        self.bot.infinity_polling(timeout=20, long_polling_timeout=10)
