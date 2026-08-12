import functools
import logging
import os
import re
import shutil
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


def escape_markdown(text: str) -> str:
    """Escape Telegram Markdown (v1) special characters."""
    if not text:
        return ""
    return re.sub(r"([_*`\[])", r"\\\1", text)


def escape_code_block(text: str) -> str:
    """Sanitize text to be safely placed inside inline code block `text` in Markdown v1."""
    if not text:
        return ""
    return text.replace("`", "'")


def chunk_text(text: str, max_length: int = 4000) -> list[str]:
    """Split text into chunks of maximum max_length without breaking markdown lines if possible."""
    if not text:
        return []
    if len(text) <= max_length:
        return [text]
    chunks = []
    lines = text.split("\n")
    current_chunk: list[str] = []
    current_length = 0

    for line in lines:
        if current_length + len(line) + 1 > max_length:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_length = 0
            if len(line) > max_length:
                for i in range(0, len(line), max_length):
                    chunks.append(line[i : i + max_length])
                current_chunk = []
                current_length = 0
            else:
                current_chunk.append(line)
                current_length = len(line)
        else:
            current_chunk.append(line)
            current_length += len(line) + 1

    if current_chunk:
        chunks.append("\n".join(current_chunk))
    return chunks


class PeerCheckerBot:
    def __init__(self, config: Config, storage: Storage):
        self.config = config
        self.storage = storage
        self.bot = telebot.TeleBot(self.config.TELEGRAM_BOT_TOKEN)
        self.validator = PeerValidator(
            target_project_ids=self.config.TARGET_PROJECT_IDS,
            min_accepted_projects=self.config.MIN_ACCEPTED_PROJECTS,
            min_logtime=self.config.MIN_LOGTIME,
            target_class_names=self.config.target_class_names,
        )

        self.monitoring_active = False
        self.monitoring_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.check_lock = threading.Lock()

        self._register_handlers()
        self.restore_persistent_state()

    def _register_handlers(self) -> None:
        """Register Telegram bot command handlers."""
        def admin_only(func: Callable) -> Callable:
            """Decorator to restrict bot commands to authorized admin Telegram User IDs."""
            @functools.wraps(func)
            def wrapper(message: types.Message, *args: Any, **kwargs: Any) -> Any:
                user_id = message.from_user.id
                if self.config.TELEGRAM_ADMIN_IDS and user_id not in self.config.TELEGRAM_ADMIN_IDS:
                    logger.warning(f"Unauthorized access attempt by user_id {user_id}")
                    self.bot.reply_to(message, "У вас нет прав для управления этим ботом.")
                    return None
                return func(message, *args, **kwargs)
            return wrapper

        @self.bot.message_handler(commands=["start", "help"])
        @admin_only
        def handle_start(message: types.Message) -> None:
            text = (
                "Привет! Я бот поиска и валидации новых пиров Школы 21.\n\n"
                "Доступные команды:\n"
                "/start, /help — Справка и приветствие\n"
                "/start_monitoring — Запуск автоматического фонового мониторинга\n"
                "/stop_monitoring — Остановка фонового мониторинга\n"
                "/check_now — Запуск проверки вне очереди\n"
                "/status — Статус работы бота и статистика базы данных\n"
                "/peers — Список пиров из БД (/peers verified, /peers 604)\n"
                "/export — Экспорт текущих пиров в .txt файлы по трайбам\n"
                "/peer <login> — Карточка пира с возможностью смены статуса\n"
                "/set_status <login> <verified|suspicious> — Ручная смена статуса пира\n"
            )
            self.bot.reply_to(message, text)

        @self.bot.message_handler(commands=["start_monitoring", "startmonitoring"])
        @admin_only
        def handle_start_monitoring(message: types.Message) -> None:
            if self.monitoring_active:
                self.bot.reply_to(message, "Фоновый мониторинг уже запущен.")
                return

            self.start_monitoring_loop()
            logger.info(f"Monitoring started by admin user_id {message.from_user.id}")
            self.bot.reply_to(
                message,
                f"Фоновый мониторинг успешно запущен!\n"
                f"Интервал проверки: каждый(е) {self.config.CHECK_INTERVAL_MINUTES} мин.",
                parse_mode="Markdown",
            )

        @self.bot.message_handler(commands=["stop_monitoring", "stopmonitoring"])
        @admin_only
        def handle_stop_monitoring(message: types.Message) -> None:
            if not self.monitoring_active:
                self.bot.reply_to(message, "Фоновый мониторинг не запущен.")
                return

            self.stop_monitoring_loop()
            logger.info(f"Monitoring stopped by admin user_id {message.from_user.id}")
            self.bot.reply_to(message, "Фоновый мониторинг остановлен.", parse_mode="Markdown")

        @self.bot.message_handler(commands=["check_now", "checknow"])
        @admin_only
        def handle_check_now(message: types.Message) -> None:
            if self.check_lock.locked():
                self.bot.reply_to(message, "Проверка пиров уже выполняется в данный момент. Пожалуйста, подождите.")
                return
            self.bot.reply_to(message, "Запускаю мгновенную проверку пиров...", parse_mode="Markdown")
            logger.info(f"Manual check triggered by admin user_id {message.from_user.id}")
            threading.Thread(target=self.run_check_and_notify, daemon=True).start()

        @self.bot.message_handler(commands=["status"])
        @admin_only
        def handle_status(message: types.Message) -> None:
            try:
                stats = self.storage.get_stats()
                last_check = self.storage.get_last_check_info()

                monitoring_badge = "🟢 Активен" if self.monitoring_active else "🔴 Остановлен"
                interval_str = f"каждые {self.config.CHECK_INTERVAL_MINUTES} мин"

                raw_ts = last_check["timestamp"] if last_check and "timestamp" in last_check else None
                if raw_ts:
                    try:
                        dt = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                        last_check_str = dt.strftime("%d.%m.%Y %H:%M:%S")
                    except Exception:
                        last_check_str = str(raw_ts)
                else:
                    last_check_str = "Еще не проводилась"

                wave_str = self.config.TARGET_CLASS_NAME if self.config.TARGET_CLASS_NAME else "Все волны"

                total_all = stats.get("total", 0)
                verified = stats.get("total_verified", 0)
                suspicious = stats.get("total_suspicious", 0)
                skipped = stats.get("total_skipped_wave", 0)

                lines = [
                    "**PeerChecker Status Report**\n",
                    "```text",
                    "[Статус системы]",
                    f"Мониторинг:     {monitoring_badge} ({interval_str})",
                    f"Фильтр волны:   {wave_str}",
                    f"Проверка:       {last_check_str}",
                    "",
                    "[Статистика БД]",
                    f"Всего записей:  {total_all}",
                    f"├ VERIFIED:     {verified}",
                    f"├ SUSPICIOUS:   {suspicious}",
                    f"└ SKIPPED_WAVE: {skipped}",
                    "",
                    "[По трайбам]",
                ]

                by_tribe = stats.get("by_tribe", {})
                if not by_tribe:
                    lines.append("• Данных по трайбам пока нет")
                else:
                    for tid, tdata in by_tribe.items():
                        tname = tdata.get("tribe_name", f"Tribe {tid}")
                        ttotal = tdata.get("total", 0)
                        tver = tdata.get("verified", 0)
                        tsusp = tdata.get("suspicious", 0)
                        tskip = tdata.get("skipped_wave", 0)
                        lines.append(
                            f"• {tname:<12} ({tid}): {ttotal:<4} [V:{tver} | S:{tsusp} | Skip:{tskip}]"
                        )

                lines.append("```")
                text = "\n".join(lines)

                self.bot.reply_to(message, text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Error handling /status: {e}", exc_info=True)
                self.bot.reply_to(message, f"❌ Ошибка при выполнении команды: {e}")

        @self.bot.message_handler(commands=["peer"])
        @admin_only
        def handle_peer_info(message: types.Message) -> None:
            try:
                parts = message.text.strip().split()
                if len(parts) < 2:
                    self.bot.reply_to(message, "Укажите логин пира. Пример: `/peer ivanov-ivan`", parse_mode="Markdown")
                    return

                login = parts[1].strip()
                peer = self.storage.get_peer(login)
                if not peer:
                    self.bot.reply_to(message, f"Пир `{escape_code_block(login)}` не найден в базе данных.", parse_mode="Markdown")
                    return

                self._send_peer_card(message.chat.id, peer)
            except Exception as e:
                logger.error(f"Error handling /peer: {e}", exc_info=True)
                self.bot.reply_to(message, f"❌ Ошибка при выполнении команды: {e}")

        @self.bot.message_handler(commands=["set_status", "setstatus"])
        @admin_only
        def handle_set_status(message: types.Message) -> None:
            try:
                parts = message.text.strip().split()
                if len(parts) < 3:
                    self.bot.reply_to(
                        message,
                        "Формат команды: `/set_status <login> <verified|suspicious>`",
                        parse_mode="Markdown",
                    )
                    return

                login = parts[1].strip()
                new_status = parts[2].strip().upper()

                if new_status not in ("VERIFIED", "SUSPICIOUS"):
                    self.bot.reply_to(message, "Статус должен быть `VERIFIED` или `SUSPICIOUS`.", parse_mode="Markdown")
                    return

                updated = self.storage.update_peer_status(login, new_status, is_manual=True)
                if updated:
                    self.bot.reply_to(
                        message,
                        f"Статус пира `{escape_code_block(login)}` успешно изменен на **{new_status}** (ручная модерация).",
                        parse_mode="Markdown",
                    )
                else:
                    self.bot.reply_to(message, f"Пир `{escape_code_block(login)}` не найден в базе данных.", parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Error handling /set_status: {e}", exc_info=True)
                self.bot.reply_to(message, f"❌ Ошибка при выполнении команды: {e}")

        @self.bot.message_handler(commands=["peers", "list"])
        @admin_only
        def handle_peers(message: types.Message) -> None:
            try:
                parts = message.text.strip().split()[1:]

                filter_status = None
                filter_tribe = None

                for arg in parts:
                    arg_upper = arg.upper()
                    if arg_upper in ("VERIFIED", "SUSPICIOUS", "SKIPPED_WAVE"):
                        filter_status = arg_upper
                    elif arg_upper != "ALL":
                        filter_tribe = arg

                peers = self.storage.get_filtered_peers(tribe_id=filter_tribe, status=filter_status)
                if not peers:
                    self.bot.reply_to(message, "Пиров по данному запросу не найдено.", parse_mode="Markdown")
                    return

                filter_desc = []
                if filter_tribe:
                    filter_desc.append(f"трайб: `{escape_code_block(filter_tribe)}`")
                if filter_status:
                    filter_desc.append(f"статус: `{filter_status}`")
                desc_str = f" ({', '.join(filter_desc)})" if filter_desc else ""

                now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"peers_{now_str}.txt"
                temp_path = os.path.join(tempfile.gettempdir(), filename)

                try:
                    with open(temp_path, "w", encoding="utf-8") as f:
                        f.write(f"=== Список пиров из БД (всего: {len(peers)}) ===\n")
                        if desc_str:
                            f.write(f"Фильтры: {desc_str}\n")
                        f.write(f"Дата экспорта: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n")
                        for p in peers:
                            manual_str = " [ручной статус]" if p.get("is_manual") else ""
                            f.write(
                                f"• {p['login']} | Трайб: {p['tribe_name']} (ID {p['tribe_id']}) | "
                                f"Статус: {p['status']}{manual_str} | XP: {p['xp']} | Логтайм: {p['logtime']:.2f} ч/нед\n"
                            )
                            if p.get("suspicion_reason"):
                                f.write(f"  Причина: {p['suspicion_reason']}\n")

                    v_count = sum(1 for p in peers if p["status"] == "VERIFIED")
                    s_count = sum(1 for p in peers if p["status"] == "SUSPICIOUS")
                    w_count = sum(1 for p in peers if p["status"] == "SKIPPED_WAVE")

                    status_counts = [f"Verified: **{v_count}**", f"Suspicious: **{s_count}**"]
                    if w_count > 0 or filter_status == "SKIPPED_WAVE":
                        status_counts.append(f"Skipped Wave: **{w_count}**")

                    caption = (
                        f"**Список пиров из БД{desc_str}**\n\n"
                        f"• Всего найдено: **{len(peers)}** чел.\n"
                        f"• {' | '.join(status_counts)}\n"
                        f"Подробный список прикреплен в файле."
                    )

                    with open(temp_path, "rb") as doc:
                        self.bot.send_document(message.chat.id, doc, caption=caption, parse_mode="Markdown")
                finally:
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"Error handling /peers: {e}", exc_info=True)
                self.bot.reply_to(message, f"❌ Ошибка при выполнении команды: {e}")

        @self.bot.message_handler(commands=["export", "export_txt"])
        @admin_only
        def handle_export(message: types.Message) -> None:
            try:
                peers = self.storage.get_all_peers()
                if not peers:
                    self.bot.reply_to(message, "❌ В базе данных пока нет сохраненных пиров.", parse_mode="Markdown")
                    return

                self.bot.reply_to(message, "📁 **Формирую файлы экспорта списка пиров по трайбам...**", parse_mode="Markdown")

                now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
                temp_dir = tempfile.mkdtemp()
                files_to_send = []

                try:
                    by_tribe: dict[int, list[dict[str, Any]]] = {}
                    for p in peers:
                        by_tribe.setdefault(p["tribe_id"], []).append(p)

                    for tid, tpeers in by_tribe.items():
                        tname = tpeers[0]["tribe_name"]
                        verified_peers = [p for p in tpeers if p["status"] == "VERIFIED"]
                        suspicious_peers = [p for p in tpeers if p["status"] == "SUSPICIOUS"]

                        if verified_peers:
                            v_path = os.path.join(temp_dir, f"{tname}_verified.txt")
                            with open(v_path, "w", encoding="utf-8") as f:
                                f.write(f"=== Проверенные пиры (VERIFIED) — Трайб {tname} (ID {tid}) ===\n")
                                f.write(f"Дата: {now_str}\n\n")
                                for p in verified_peers:
                                    f.write(f"• Логин: {p['login']} | XP: {p['xp']} | Логтайм: {p['logtime']:.2f} ч/нед\n")
                            files_to_send.append(v_path)

                        if suspicious_peers:
                            s_path = os.path.join(temp_dir, f"{tname}_suspicious.txt")
                            with open(s_path, "w", encoding="utf-8") as f:
                                f.write(f"=== Подозрительные пиры (SUSPICIOUS) — Трайб {tname} (ID {tid}) ===\n")
                                f.write(f"Дата: {now_str}\n\n")
                                for p in suspicious_peers:
                                    f.write(
                                        f"• Логин: {p['login']} | XP: {p['xp']} | Логтайм: {p['logtime']:.2f} ч/нед\n"
                                        f"  Причина: {p.get('suspicion_reason', 'Неизвестно')}\n\n"
                                    )
                            files_to_send.append(s_path)

                    if not files_to_send:
                        self.bot.reply_to(
                            message,
                            "ℹ️ **Экспорт завершен:** В базе данных нет проверенных (`VERIFIED`) или подозрительных (`SUSPICIOUS`) пиров для отчета.",
                            parse_mode="Markdown",
                        )
                        return

                    for fp in files_to_send:
                        with open(fp, "rb") as doc:
                            self.bot.send_document(message.chat.id, doc)
                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as e:
                logger.error(f"Error handling /export: {e}", exc_info=True)
                self.bot.reply_to(message, f"❌ Ошибка при экспорте пиров: {e}")

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("set_status:"))
        def handle_status_callback(call: types.CallbackQuery) -> None:
            user_id = call.from_user.id
            if self.config.TELEGRAM_ADMIN_IDS and user_id not in self.config.TELEGRAM_ADMIN_IDS:
                try:
                    self.bot.answer_callback_query(call.id, "⛔ Нет прав доступа.", show_alert=True)
                except Exception:
                    pass
                return

            # Format: set_status:<login>:<status>
            parts = call.data.split(":")
            if len(parts) == 3:
                login, status = parts[1], parts[2]
                try:
                    updated = self.storage.update_peer_status(login, status, is_manual=True)
                    if updated:
                        try:
                            self.bot.answer_callback_query(call.id, f"Статус пира {login} изменен на {status}")
                        except Exception:
                            pass
                        peer = self.storage.get_peer(login)
                        if peer:
                            text, markup = self._build_peer_card_content(peer)
                            try:
                                self.bot.edit_message_text(
                                    text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown"
                                )
                            except Exception:
                                pass
                    else:
                        try:
                            self.bot.answer_callback_query(call.id, f"⚠️ Пир '{login}' не найден в БД.", show_alert=True)
                        except Exception:
                            pass
                except Exception as e:
                    logger.error(f"Error in handle_status_callback: {e}", exc_info=True)
                    try:
                        self.bot.answer_callback_query(call.id, f"⚠️ Ошибка смены статуса: {e}", show_alert=True)
                    except Exception:
                        pass
            else:
                try:
                    self.bot.answer_callback_query(call.id, "⚠️ Ошибка формата данных.")
                except Exception:
                    pass

    def _build_peer_card_content(self, peer: dict[str, Any]) -> tuple[str, types.InlineKeyboardMarkup]:
        """Format peer card text and inline buttons."""
        login = peer["login"]
        status = peer["status"]
        status_emoji = "✅ VERIFIED" if status == "VERIFIED" else "⚠️ SUSPICIOUS"
        manual_flag = " (изменено вручную)" if peer.get("is_manual") else ""

        first_seen_val = peer.get("first_seen") or "Неизвестно"
        suspicion_reason_val = peer.get("suspicion_reason") or "Нет"

        text = (
            f"👤 **Карточка пира `{escape_code_block(login)}`**\n\n"
            f"• **Трайб:** {escape_markdown(peer['tribe_name'])} (ID {peer['tribe_id']})\n"
            f"• **Статус:** {status_emoji}{manual_flag}\n"
            f"• **Суммарный XP:** {peer.get('xp', 0)}\n"
            f"• **Логтайм:** {peer.get('logtime', 0.0):.2f} ч/нед\n"
            f"• **Причина / Примечание:** `{escape_code_block(suspicion_reason_val)}`\n"
            f"• **Первое обнаружение:** `{escape_code_block(first_seen_val)}`\n"
        )

        markup = types.InlineKeyboardMarkup()
        btn_v = types.InlineKeyboardButton("✅ Установить VERIFIED", callback_data=f"set_status:{login}:VERIFIED")
        btn_s = types.InlineKeyboardButton("⚠️ Установить SUSPICIOUS", callback_data=f"set_status:{login}:SUSPICIOUS")
        markup.add(btn_v, btn_s)
        return text, markup

    def _send_peer_card(self, chat_id: int, peer: dict[str, Any]) -> None:
        text, markup = self._build_peer_card_content(peer)
        self.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

    def start_monitoring_loop(self) -> None:
        """Start background monitoring thread and persist active state in storage."""
        if self.monitoring_thread and self.monitoring_thread.is_alive():
            self.stop_event.set()
            self.monitoring_thread.join(timeout=3.0)

        self.monitoring_active = True
        self.storage.set_monitoring_active(True)
        self.stop_event.clear()
        thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitoring_thread = thread
        thread.start()

    def stop_monitoring_loop(self) -> None:
        """Stop background monitoring thread and update persistent storage."""
        self.monitoring_active = False
        self.storage.set_monitoring_active(False)
        self.stop_event.set()

    def restore_persistent_state(self) -> None:
        """Restore active state (background monitoring or interrupted check_now scan) from SQLite storage."""
        monitoring_was_active = self.storage.is_monitoring_active()
        check_was_in_progress = self.storage.is_check_in_progress()

        if monitoring_was_active:
            logger.info("Restoring active background monitoring state from persistent SQLite storage...")
            self.start_monitoring_loop()
            try:
                self._send_to_admins(
                    "🔄 **Бот перезапущен.** Фоновый мониторинг автоматически возобновлен из сохраненного состояния базы данных."
                )
            except Exception as e:
                logger.warning(f"Could not send startup recovery notification to admins: {e}")
        elif check_was_in_progress:
            logger.info("Resuming interrupted scan (/check_now) from persistent SQLite storage...")
            threading.Thread(target=self.run_check_and_notify, daemon=True).start()
            try:
                self._send_to_admins(
                    "🔄 **Бот перезапущен.** Возобновляю незавершенную проверку пиров (`/check_now`) с места остановки..."
                )
            except Exception as e:
                logger.warning(f"Could not send startup check recovery notification to admins: {e}")

    def _monitoring_loop(self) -> None:
        """Background thread target for periodic monitoring."""
        logger.info("Monitoring loop started.")
        current_t = threading.current_thread()
        while not self.stop_event.is_set() and self.monitoring_thread == current_t:
            try:
                self.run_check_and_notify(is_background=True)
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}", exc_info=True)

            # Sleep in small steps to allow fast cancellation
            interval_sec = self.config.CHECK_INTERVAL_MINUTES * 60
            for _ in range(int(interval_sec)):
                if self.stop_event.is_set() or self.monitoring_thread != current_t:
                    break
                time.sleep(1)

        logger.info("Monitoring loop stopped.")

    def run_check_and_notify(self, is_background: bool = False) -> None:
        """Core monitoring logic: queries S21 OpenAPI, skips existing DB peers, validates new peers, and notifies admins."""
        current_t = threading.current_thread()
        if is_background and (self.stop_event.is_set() or self.monitoring_thread != current_t):
            logger.info("Scan aborted by stop signal or stale thread.")
            return

        if not self.check_lock.acquire(blocking=False):
            logger.warning("Check already in progress. Skipping duplicate run.")
            return

        self.storage.set_check_in_progress(True)
        try:
            logger.info("Starting peer scan across target coalitions...")
            with S21ApiClient(
                login=self.config.S21_LOGIN,
                password=self.config.S21_PASSWORD,
            ) as api_client:

                known_logins = self.storage.get_known_logins()
                logger.info(f"Loaded {len(known_logins)} existing known logins from database.")

                new_peers_by_tribe: dict[int, list[dict[str, Any]]] = {}
                all_new_peers: list[dict[str, Any]] = []
                skipped_wave_count = 0
                total_unprocessed_count = 0

                for tribe_id, tribe_name in self.config.TARGET_COALITIONS.items():
                    if is_background and (self.stop_event.is_set() or self.monitoring_thread != current_t):
                        logger.info("Scan aborted by stop signal or stale thread.")
                        return

                    try:
                        participant_logins = api_client.get_coalition_participants(
                            tribe_id, stop_event=self.stop_event if is_background else None
                        )
                        if is_background and (self.stop_event.is_set() or self.monitoring_thread != current_t):
                            logger.info("Scan aborted by stop signal or stale thread.")
                            return

                        logger.info(f"Fetched {len(participant_logins)} total logins for tribe {tribe_name} ({tribe_id}).")

                        # Deduplication filter: only check logins not yet in SQLite DB!
                        unprocessed_logins = [l for l in participant_logins if l not in known_logins]
                        total_unprocessed_count += len(unprocessed_logins)
                        logger.info(f"Found {len(unprocessed_logins)} new (unprocessed) logins for tribe {tribe_name}.")

                        tribe_new_peers = []
                        for idx, login in enumerate(unprocessed_logins):
                            if is_background and (self.stop_event.is_set() or self.monitoring_thread != current_t):
                                logger.info("Scan aborted by stop signal or stale thread during peer validation.")
                                return

                            try:
                                val_res = self.validator.validate_peer(
                                    api_client, login, current_index=idx + 1, total_count=len(unprocessed_logins)
                                )

                                val_res["tribe_id"] = tribe_id
                                val_res["tribe_name"] = tribe_name
                                val_res["xp"] = val_res["total_xp"]
                                val_res["logtime"] = val_res["logtime"]

                                # Save peer to DB immediately so validated progress is retained
                                self.storage.save_peer(val_res)
                                known_logins.add(login)

                                # Only add to notifications if not skipped by wave filter
                                if val_res.get("is_skipped"):
                                    skipped_wave_count += 1
                                else:
                                    tribe_new_peers.append(val_res)
                                    all_new_peers.append(val_res)

                            except Exception as e:
                                logger.error(f"Error validating peer {login}: {e}", exc_info=True)

                        if tribe_new_peers:
                            new_peers_by_tribe[tribe_id] = tribe_new_peers

                    except Exception as e:
                        logger.error(f"Error checking coalition {tribe_id} ({tribe_name}): {e}")

                now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
                target_wave_str = self.config.TARGET_CLASS_NAME if self.config.TARGET_CLASS_NAME else "Все волны"

                # Notification Logic
                if not all_new_peers:
                    if total_unprocessed_count == 0:
                        report_text = (
                            f"ℹ️ **Статус проверки пиров Школы 21**\n\n"
                            f"• **Результат:** Новых логинов на платформе не обнаружено.\n"
                            f"• **Последняя проверка:** `{escape_code_block(now_str)}`"
                        )
                        log_msg = "Новых логинов на платформе не обнаружено"
                    else:
                        report_text = (
                            f"ℹ️ **Статус проверки пиров Школы 21**\n\n"
                            f"• **Целевая волна:** `{escape_code_block(target_wave_str)}`\n"
                            f"• **Результат:** Пиры целевой волны пока не зарегистрированы.\n"
                            f"• **Проверено новых пиров:** {total_unprocessed_count} (все из других волн, пропущены)\n"
                            f"• **Последняя проверка:** `{escape_code_block(now_str)}`"
                        )
                        log_msg = f"Проверено {total_unprocessed_count} новых пиров, пиров целевой волны ({target_wave_str}) не обнаружено"

                    self._send_to_admins(report_text)
                    self.storage.log_check_run(0, log_msg)
                    return

                # Summary for newly found target wave peers
                total_new = len(all_new_peers)
                summary_text = (
                    f"🚨 **Обнаружены новые пиры целевой волны!** ({total_new} чел.)\n"
                    f"• **Целевая волна:** `{escape_code_block(target_wave_str)}`\n"
                    f"• **Время проверки:** `{escape_code_block(now_str)}`\n"
                )
                if skipped_wave_count > 0:
                    summary_text += f"• **Пропущено из других волн:** {skipped_wave_count} чел.\n"
                summary_text += "\n📊 **Распределение по трайбам:**\n"

                for tid, tname in self.config.TARGET_COALITIONS.items():
                    tpeers = new_peers_by_tribe.get(tid, [])
                    v_count = sum(1 for p in tpeers if p["status"] == "VERIFIED")
                    s_count = sum(1 for p in tpeers if p["status"] == "SUSPICIOUS")
                    summary_text += f"• **{escape_markdown(tname)}:** {len(tpeers)} новых (✅ {v_count} verified / ⚠️ {s_count} suspicious)\n"

                # Generate report files per tribe
                files_to_send = []
                temp_dir = tempfile.mkdtemp()

                try:
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

                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True)

                self.storage.log_check_run(total_new, f"Найдено новых: {total_new}")

        finally:
            self.storage.set_check_in_progress(False)
            self.check_lock.release()

    def _send_to_admins(self, text: str, files: list[str] | None = None) -> None:
        """Send notification text message and optional file attachments to configured admin Telegram IDs."""
        if not self.config.TELEGRAM_ADMIN_IDS:
            logger.warning("No TELEGRAM_ADMIN_IDS configured to send reports.")
            return

        text_chunks = chunk_text(text, max_length=4000)
        for admin_id in self.config.TELEGRAM_ADMIN_IDS:
            try:
                for chunk in text_chunks:
                    self.bot.send_message(admin_id, chunk, parse_mode="Markdown")
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
