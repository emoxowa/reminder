"""Уведомления: системные уведомления macOS, всплывающее окно поверх всех окон
и планировщик, который периодически проверяет базу."""

import subprocess
import tkinter as tk
from datetime import datetime, timedelta
from typing import Callable, Dict

from db import Reminder, ReminderDB
from recurrence import REPEAT_LABELS

CHECK_INTERVAL_MS = 5_000           # как часто проверять базу
OVERDUE_GRACE = timedelta(minutes=5)  # сколько ждать реакции на уведомление
SNOOZE_MINUTES = 10

# Текст передаётся в AppleScript через argv, поэтому кавычки экранировать не нужно
_APPLESCRIPT = [
    "-e", "on run argv",
    "-e", 'display notification (item 2 of argv) with title (item 1 of argv) '
          'subtitle (item 3 of argv) sound name "Glass"',
    "-e", "end run",
]


def system_notification(title: str, message: str, subtitle: str = "") -> None:
    """Показывает системное уведомление macOS через osascript (не блокирует GUI)."""
    try:
        subprocess.Popen(
            ["osascript", *_APPLESCRIPT, title, message or " ", subtitle],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass  # не macOS или osascript недоступен — остаётся popup-окно


class ReminderPopup(tk.Toplevel):
    """Окно напоминания, которое держится поверх всех окон."""

    def __init__(self, master: tk.Misc, reminder: Reminder,
                 on_done: Callable[[int], None],
                 on_snooze: Callable[[int], None],
                 on_close: Callable[[int], None]):
        super().__init__(master)
        self.reminder_id = reminder.id
        self._on_close = on_close

        self.title("Напоминание")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.close)

        frame = tk.Frame(self, padx=20, pady=16)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="⏰ " + reminder.title, font=("Helvetica", 16, "bold"),
                 wraplength=360, justify="left").pack(anchor="w")
        when = reminder.trigger_at.strftime("%d.%m.%Y %H:%M")
        if reminder.is_recurring:
            when += f"  •  🔁 {REPEAT_LABELS[reminder.repeat]}"
        tk.Label(frame, text=when, fg="gray").pack(anchor="w", pady=(2, 8))
        if reminder.description:
            tk.Label(frame, text=reminder.description, wraplength=360,
                     justify="left").pack(anchor="w", pady=(0, 12))

        buttons = tk.Frame(frame)
        buttons.pack(fill="x")
        done_text = "Готово (до следующего раза)" if reminder.is_recurring else "Готово"
        tk.Button(buttons, text=done_text,
                  command=lambda: self._act(on_done)).pack(side="left")
        tk.Button(buttons, text=f"Отложить на {SNOOZE_MINUTES} мин",
                  command=lambda: self._act(on_snooze)).pack(side="left", padx=6)
        tk.Button(buttons, text="Закрыть", command=self.close).pack(side="right")

        self._center()
        # Поднимаем окно, даже если главное окно свёрнуто
        self.deiconify()
        self.lift()
        self.focus_force()
        self.bell()

    def _center(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - w) // 2
        y = self.winfo_screenheight() // 4
        self.geometry(f"+{x}+{y}")

    def _act(self, callback: Callable[[int], None]) -> None:
        callback(self.reminder_id)
        self.close()

    def close(self) -> None:
        self._on_close(self.reminder_id)
        self.destroy()


class ReminderScheduler:
    """Периодически проверяет базу в главном потоке Tk через `after`.
    Работает независимо от того, свёрнуто ли главное окно."""

    def __init__(self, root: tk.Tk, db: ReminderDB, on_change: Callable[[], None]):
        self.root = root
        self.db = db
        self.on_change = on_change   # колбэк для обновления списка в GUI
        self._popups: Dict[int, ReminderPopup] = {}
        self._job = None

    def start(self) -> None:
        self.check()

    def stop(self) -> None:
        if self._job is not None:
            self.root.after_cancel(self._job)
            self._job = None

    def check_soon(self) -> None:
        """Внеочередная проверка (например, после добавления напоминания)."""
        self.stop()
        self._job = self.root.after(0, self.check)

    def check(self) -> None:
        try:
            now = datetime.now()
            changed = False
            for reminder in self.db.due_unnotified(now):
                self.notify(reminder)
                self.db.mark_notified(reminder.id)
                changed = True
            if self.db.process_missed(now, OVERDUE_GRACE):
                changed = True
            if changed:
                self.on_change()
        finally:
            self._job = self.root.after(CHECK_INTERVAL_MS, self.check)

    def notify(self, reminder: Reminder) -> None:
        system_notification("Напоминание", reminder.description or reminder.title,
                            reminder.title if reminder.description else "")
        if reminder.id not in self._popups:
            self._popups[reminder.id] = ReminderPopup(
                self.root, reminder,
                on_done=self._done,
                on_snooze=self._snooze,
                on_close=lambda rid: self._popups.pop(rid, None),
            )

    def close_popup(self, reminder_id: int) -> None:
        """Закрывает окно напоминания, если его удалили/изменили из главного окна."""
        popup = self._popups.get(reminder_id)
        if popup is not None:
            popup.close()

    def _done(self, reminder_id: int) -> None:
        self.db.complete(reminder_id)
        self.on_change()

    def _snooze(self, reminder_id: int) -> None:
        self.db.snooze(reminder_id, SNOOZE_MINUTES)
        self.on_change()
