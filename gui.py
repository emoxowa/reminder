"""Главное окно приложения на tkinter."""

import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox, ttk

from db import (KIND_ONCE, KIND_RECURRING, STATUS_CANCELLED, STATUS_DONE,
                STATUS_OVERDUE, STATUS_PENDING, STATUSES, ReminderDB)
from notifier import ReminderScheduler
from recurrence import REPEAT_LABELS, REPEAT_NONE, label_to_rule

UI_DATETIME_FORMAT = "%d.%m.%Y %H:%M"
FILTER_ALL = "Все"
KIND_FILTERS = {FILTER_ALL: None, "Одноразовые": KIND_ONCE, "Повторяющиеся": KIND_RECURRING}

STATUS_COLORS = {
    STATUS_PENDING: "#1f6feb",
    STATUS_DONE: "#2da44e",
    STATUS_OVERDUE: "#cf222e",
    STATUS_CANCELLED: "#8c959f",
}


class AddReminderDialog(tk.Toplevel):
    """Модальное окно создания напоминания."""

    def __init__(self, master: tk.Misc, db: ReminderDB):
        super().__init__(master)
        self.db = db
        self.created = False

        self.title("Новое напоминание")
        self.resizable(False, False)
        self.transient(master)

        form = ttk.Frame(self, padding=16)
        form.pack(fill="both", expand=True)

        ttk.Label(form, text="Заголовок:").grid(row=0, column=0, sticky="w")
        self.title_entry = ttk.Entry(form, width=40)
        self.title_entry.grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Описание:").grid(row=1, column=0, sticky="nw")
        self.desc_text = tk.Text(form, width=40, height=5, wrap="word")
        self.desc_text.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Когда:").grid(row=2, column=0, sticky="w")
        self.due_entry = ttk.Entry(form, width=20)
        default_due = (datetime.now() + timedelta(hours=1)).replace(second=0, microsecond=0)
        self.due_entry.insert(0, default_due.strftime(UI_DATETIME_FORMAT))
        self.due_entry.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(form, text="формат: ДД.ММ.ГГГГ ЧЧ:ММ",
                  foreground="gray").grid(row=3, column=1, sticky="w")

        ttk.Label(form, text="Повтор:").grid(row=4, column=0, sticky="w")
        self.repeat_var = tk.StringVar(value=REPEAT_LABELS[REPEAT_NONE])
        ttk.Combobox(form, textvariable=self.repeat_var, state="readonly", width=18,
                     values=tuple(REPEAT_LABELS.values())).grid(row=4, column=1, sticky="w", pady=4)

        buttons = ttk.Frame(form)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Сохранить", command=self.save).pack(side="right", padx=6)

        self.bind("<Return>", lambda e: self.save() if e.widget is not self.desc_text else None)
        self.bind("<Escape>", lambda e: self.destroy())
        self.title_entry.focus_set()
        self.grab_set()

    def save(self) -> None:
        title = self.title_entry.get().strip()
        description = self.desc_text.get("1.0", "end").strip()
        if not title:
            messagebox.showwarning("Ошибка", "Введите заголовок.", parent=self)
            return
        try:
            due_at = datetime.strptime(self.due_entry.get().strip(), UI_DATETIME_FORMAT)
        except ValueError:
            messagebox.showwarning("Ошибка", "Дата должна быть в формате ДД.ММ.ГГГГ ЧЧ:ММ.",
                                   parent=self)
            return
        repeat = label_to_rule(self.repeat_var.get())
        # Повторяющаяся серия с прошлой датой просто начнётся со следующего срабатывания
        if repeat == REPEAT_NONE and due_at <= datetime.now() and not messagebox.askyesno(
                "Время уже прошло", "Указанное время уже прошло. Всё равно сохранить?",
                parent=self):
            return
        self.db.add(title, description, due_at, repeat)
        self.created = True
        self.destroy()


class ReminderApp(tk.Tk):
    def __init__(self, db: ReminderDB):
        super().__init__()
        self.db = db
        self.title("Напоминалка")
        self.geometry("900x460")
        self.minsize(700, 300)

        self._build_toolbar()
        self._build_table()

        self.scheduler = ReminderScheduler(self, db, on_change=self.refresh)
        self.protocol("WM_DELETE_WINDOW", self.on_quit)

        self.refresh()
        self.scheduler.start()

    # --- Построение интерфейса --------------------------------------------

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(10, 8))
        bar.pack(fill="x")

        ttk.Button(bar, text="➕ Добавить", command=self.add_reminder).pack(side="left")
        ttk.Button(bar, text="✔ Готово",
                   command=self.complete_reminder).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="✖ Отменить",
                   command=lambda: self.set_status(STATUS_CANCELLED)).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="🗑 Удалить", command=self.delete_reminder).pack(side="left", padx=(6, 0))

        self.filter_var = tk.StringVar(value=FILTER_ALL)
        combo = ttk.Combobox(bar, textvariable=self.filter_var, state="readonly", width=14,
                             values=(FILTER_ALL, *STATUSES))
        combo.pack(side="right")
        combo.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Label(bar, text="Статус:").pack(side="right", padx=(0, 6))

        self.kind_var = tk.StringVar(value=FILTER_ALL)
        kind_combo = ttk.Combobox(bar, textvariable=self.kind_var, state="readonly", width=14,
                                  values=tuple(KIND_FILTERS))
        kind_combo.pack(side="right", padx=(0, 12))
        kind_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Label(bar, text="Тип:").pack(side="right", padx=(0, 6))

    def _build_table(self) -> None:
        frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        frame.pack(fill="both", expand=True)

        columns = ("title", "description", "due_at", "repeat", "status")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        for col, text, width, stretch in (
            ("title", "Заголовок", 170, True),
            ("description", "Описание", 240, True),
            ("due_at", "Когда", 140, False),
            ("repeat", "Повтор", 110, False),
            ("status", "Статус", 100, False),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, stretch=stretch)
        for status, color in STATUS_COLORS.items():
            self.tree.tag_configure(status, foreground=color)

        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.tree.bind("<Delete>", lambda e: self.delete_reminder())
        self.tree.bind("<BackSpace>", lambda e: self.delete_reminder())

    # --- Действия -----------------------------------------------------------

    def refresh(self) -> None:
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        status = self.filter_var.get()
        reminders = self.db.list(None if status == FILTER_ALL else status,
                                 KIND_FILTERS[self.kind_var.get()])
        for r in reminders:
            iid = str(r.id)
            when = r.trigger_at.strftime(UI_DATETIME_FORMAT)
            if r.snooze_until:
                when += " ⏸"  # отложено
            self.tree.insert("", "end", iid=iid, tags=(r.status,), values=(
                r.title,
                r.description.replace("\n", " "),
                when,
                ("🔁 " if r.is_recurring else "") + REPEAT_LABELS[r.repeat],
                r.status,
            ))
            if iid in selected:
                self.tree.selection_add(iid)

    def _selected_ids(self):
        ids = [int(iid) for iid in self.tree.selection()]
        if not ids:
            messagebox.showinfo("Нет выбора", "Выберите напоминание в списке.", parent=self)
        return ids

    def add_reminder(self) -> None:
        dialog = AddReminderDialog(self, self.db)
        self.wait_window(dialog)
        if dialog.created:
            self.refresh()
            self.scheduler.check_soon()

    def complete_reminder(self) -> None:
        """Одноразовое → «Готово», повторяющееся → перенос на следующее срабатывание."""
        for rid in self._selected_ids():
            self.db.complete(rid)
            self.scheduler.close_popup(rid)
        self.refresh()

    def set_status(self, status: str) -> None:
        for rid in self._selected_ids():
            self.db.set_status(rid, status)
            self.scheduler.close_popup(rid)
        self.refresh()

    def delete_reminder(self) -> None:
        ids = self._selected_ids()
        if not ids or not messagebox.askyesno(
                "Удаление", f"Удалить выбранные напоминания ({len(ids)})?", parent=self):
            return
        for rid in ids:
            self.scheduler.close_popup(rid)
            self.db.delete(rid)
        self.refresh()

    def on_quit(self) -> None:
        self.scheduler.stop()
        self.db.close()
        self.destroy()
