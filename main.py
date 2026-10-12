"""Точка входа: python3 main.py"""

from db import ReminderDB
from gui import ReminderApp


def main() -> None:
    db = ReminderDB()  # при создании проверяет и создаёт таблицы
    app = ReminderApp(db)
    app.mainloop()


if __name__ == "__main__":
    main()
