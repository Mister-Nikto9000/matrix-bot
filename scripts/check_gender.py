"""Поиск в arcana.json мужских форм, обращённых к пользователю, вне шаблонов {м|ж}.

Запуск из корня проекта: python -m scripts.check_gender [путь к arcana.json]\nБез аргумента проверяются и arcana.json, и тексты интерфейса messages.py
Шаблоны заменяются на «…», затем ищутся типичные мужские формы при обращении на «ты»:
прилагательные (-ый, -ий, -ой), краткие формы (готов, уверен, рад, сам…),
прошедшее время (-л), «<прилагательное> человек» и «сам/самому».
Это эвристика: найденное нужно просмотреть глазами.
"""
import json
import re
import sys
from pathlib import Path

ARCANA = Path(__file__).resolve().parent.parent / "bot" / "content" / "arcana.json"
TEMPLATE = re.compile(r"\{[^{}|]*\|[^{}|]*\}")
SHORT = (
    "сам|самому|самим|готов|уверен|рад|должен|способен|один|одинок|первым|последним|нужен|открыт|свободен|"
    "счастлив|спокоен|прав|силён|обязан|доволен|согласен|достоин|благодарен|склонен|влюблён|занят|честен"
)
CHECKS = [
    # «ты энергичный», «ты очень чуткий»
    ("прилагательное после «ты»",
     re.compile(r"\bты\s+(?:не\s+)?(?:очень\s+|слишком\s+|такой\s+)?(\w+(?:ый|ий|ой))\b", re.I)),
    # «ты готов», «ты сам», «ты не уверен в…»
    ("краткая форма после «ты»", re.compile(rf"\bты\s+(?:\w+\s+){{0,2}}?({SHORT})\b", re.I)),
    # «ты сделал», «ты не смог»
    ("прошедшее время после «ты»", re.compile(r"\bты\s+(?:не\s+)?(\w+[аеиоуыяё]л)\b", re.I)),
    # «вдумчивый человек», «обновлённым человеком» в предложении с «ты»
    ("«человек» с прилагательным",
     re.compile(r"\bты\b[^.!?]*?\b(\w+(?:ый|ий|ым|им)\s+человек(?:ом)?)\b", re.I)),
    # «сам», «самому» в предложении с «ты»/«тебе»
    ("«сам» при обращении", re.compile(r"\b(?:ты|тебе|тебя)\b[^.!?]*?\b(сам|самому|самим)\b", re.I)),
]
# частые ложные срабатывания: слова, не зависящие от рода
SKIP = {"другой", "какой", "такой", "домой", "порой"}


def scan(path: Path = ARCANA) -> list[tuple[str, str, str, str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    found = []
    for n, fields in data.items():
        for field, value in fields.items():
            for text in value if isinstance(value, list) else [value]:
                plain = TEMPLATE.sub("…", text)
                for kind, pattern in CHECKS:
                    for m in pattern.finditer(plain):
                        if m.group(1).lower() in SKIP:
                            continue
                        ctx = plain[max(0, m.start() - 20):m.end() + 20].strip()
                        found.append((n, field, kind, m.group(1), ctx))
    return found


def scan_messages() -> list[tuple[str, str, str, str, str]]:
    """То же для текстов интерфейса: все строковые константы bot/content/messages.py (и значения словарей)."""
    from bot.content import messages

    found = []
    for name in dir(messages):
        value = getattr(messages, name)
        texts = [value] if isinstance(value, str) else [v for v in value.values() if isinstance(v, str)] if isinstance(value, dict) else []
        for text in texts:
            plain = TEMPLATE.sub("…", text)
            for kind, pattern in CHECKS:
                for m in pattern.finditer(plain):
                    if m.group(1).lower() not in SKIP:
                        found.append(("messages", name, kind, m.group(1), plain[max(0, m.start() - 20):m.end() + 20].strip()))
    return found


def main() -> int:
    found = scan(Path(sys.argv[1]) if len(sys.argv) > 1 else ARCANA)
    found += scan_messages() if len(sys.argv) <= 1 else []
    for n, field, kind, word, ctx in found:
        print(f"[{n}] {field}: {kind}: «{word}» — …{ctx}…")
    print(f"\nНайдено: {len(found)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
