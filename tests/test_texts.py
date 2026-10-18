from bot.services import texts

FIELDS = ["name", "teaser", "personality", "talents", "purpose", "comfort",
          "money", "relationships", "karma", "compat", "year"]


def test_all_22_arcana_have_all_fields():
    for n in range(1, 23):
        a = texts.arcana(n)
        for f in FIELDS:
            value = a[f]
            variants = value if isinstance(value, list) else [value]
            assert variants and all(isinstance(v, str) and v.strip() for v in variants), (n, f)


def test_names_unique():
    assert len({texts.name(n) for n in range(1, 23)}) == 22


# --- Варианты текстов (поле может быть списком) ---

VARIANTS = [f"Вариант {i}." for i in range(10)]


def _with_variants(monkeypatch, field="teaser"):
    data = {k: dict(v) for k, v in texts._arcana().items()}
    data["7"][field] = VARIANTS
    monkeypatch.setattr(texts, "_arcana", lambda: data)


def test_string_field_works_as_before():
    assert texts.text(7, "teaser") == texts.arcana(7)["teaser"]
    assert texts.text(7, "teaser", texts.user_key(123)) == texts.arcana(7)["teaser"]


def test_variant_choice_is_stable(monkeypatch):
    _with_variants(monkeypatch)
    key = texts.user_key(123456)
    first = texts.text(7, "teaser", key)
    assert first in VARIANTS
    assert all(texts.text(7, "teaser", key) == first for _ in range(20))
    # индекс считается через sha256 — не зависит от PYTHONHASHSEED и перезапуска
    import hashlib
    expected = int.from_bytes(hashlib.sha256(b"123456|7|teaser").digest()[:8], "big") % len(VARIANTS)
    assert first == VARIANTS[expected]


def test_different_users_can_get_different_variants(monkeypatch):
    _with_variants(monkeypatch)
    seen = {texts.text(7, "teaser", texts.user_key(tg_id)) for tg_id in range(1, 200)}
    assert len(seen) > 1


def test_pair_key_is_symmetric(monkeypatch):
    _with_variants(monkeypatch, "compat")
    assert texts.pair_key(5, 9) == texts.pair_key(9, 5)
    assert texts.text(7, "compat", texts.pair_key(5, 9)) == texts.text(7, "compat", texts.pair_key(9, 5))


def test_variants_in_bot_output(monkeypatch):
    from datetime import date

    from bot.handlers.matrix import teaser_blocks
    from bot.services.matrix_calc import calculate

    _with_variants(monkeypatch)
    m = calculate(date(1994, 3, 7))  # личность — 7
    key = texts.user_key(42)
    assert texts.text(7, "teaser", key) in teaser_blocks(m, key)[0]
