"""Тексты по роду пользователя.

В текстах (arcana.json, messages.py) слова, зависящие от рода, записаны шаблоном
{мужская форма|женская форма}: «Ты {энергичный|энергичная}», «рассчитал{|а}».
render() подставляет нужную форму; без выбранного пола — женская (основная аудитория).
Плейсхолдеры str.format вида {name} не содержат «|» и не затрагиваются.
"""
import re
from contextvars import ContextVar
from typing import Any

FEMALE, MALE = "f", "m"
GENDERS = (FEMALE, MALE)

_TEMPLATE = re.compile(r"\{([^{}|]*)\|([^{}|]*)\}")

# Пользователь текущего апдейта — по нему DbMiddleware и RenderMiddleware выбирают род
current_user: ContextVar[Any] = ContextVar("current_user", default=None)


def render(text: str, gender: str | None) -> str:
    group = 1 if gender == MALE else 2
    return _TEMPLATE.sub(lambda m: m.group(group), text)


def has_templates(text: str) -> bool:
    return bool(_TEMPLATE.search(text))
