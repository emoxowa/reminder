"""Правила повтора напоминаний и расчёт следующего срабатывания."""

import calendar
from datetime import datetime, timedelta
from typing import Optional

REPEAT_NONE = "none"
REPEAT_HOURLY = "hourly"
REPEAT_DAILY = "daily"
REPEAT_WEEKDAYS = "weekdays"
REPEAT_WEEKLY = "weekly"
REPEAT_MONTHLY = "monthly"
REPEAT_YEARLY = "yearly"

# Порядок важен: так правила показываются в интерфейсе
REPEAT_LABELS = {
    REPEAT_NONE: "Однократно",
    REPEAT_HOURLY: "Каждый час",
    REPEAT_DAILY: "Ежедневно",
    REPEAT_WEEKDAYS: "По будням",
    REPEAT_WEEKLY: "Еженедельно",
    REPEAT_MONTHLY: "Ежемесячно",
    REPEAT_YEARLY: "Ежегодно",
}
REPEAT_RULES = tuple(REPEAT_LABELS)

_FIXED_STEPS = {
    REPEAT_HOURLY: timedelta(hours=1),
    REPEAT_DAILY: timedelta(days=1),
    REPEAT_WEEKLY: timedelta(weeks=1),
}


def label_to_rule(label: str) -> str:
    for rule, text in REPEAT_LABELS.items():
        if text == label:
            return rule
    raise ValueError(f"Неизвестное правило повтора: {label}")


def add_months(dt: datetime, months: int) -> datetime:
    """Сдвигает дату на N месяцев; 31-е число в коротком месяце становится последним днём."""
    month_index = dt.month - 1 + months
    year, month = dt.year + month_index // 12, month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _next_fixed_step(start: datetime, step: timedelta, after: datetime) -> datetime:
    if start > after:
        return start
    k = (after - start) // step + 1
    return start + k * step


def next_occurrence(start: datetime, rule: str, after: datetime) -> Optional[datetime]:
    """Ближайшее срабатывание серии, начинающейся в `start`, строго позже `after`.
    Серия всегда считается от `start`, поэтому время не «уплывает»
    (например, напоминание на 31-е число в феврале сработает 28-го, а в марте снова 31-го).
    Для однократных напоминаний возвращает None."""
    if rule == REPEAT_NONE:
        return None
    if rule in _FIXED_STEPS:
        return _next_fixed_step(start, _FIXED_STEPS[rule], after)
    if rule == REPEAT_WEEKDAYS:
        candidate = _next_fixed_step(start, timedelta(days=1), after)
        while candidate.weekday() >= 5:  # суббота, воскресенье
            candidate += timedelta(days=1)
        return candidate
    if rule in (REPEAT_MONTHLY, REPEAT_YEARLY):
        if start > after:
            return start
        step = 1 if rule == REPEAT_MONTHLY else 12
        months = (after.year - start.year) * 12 + after.month - start.month
        k = months // step
        candidate = add_months(start, k * step)
        while candidate <= after:
            k += 1
            candidate = add_months(start, k * step)
        return candidate
    raise ValueError(f"Неизвестное правило повтора: {rule}")
