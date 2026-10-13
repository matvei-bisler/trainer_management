"""Автопроверка ответов. Все функции возвращают долю от 0 до 1."""

import re
import unicodedata
from functools import lru_cache

try:
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
except Exception:  # без морфологии — только точное совпадение
    _MORPH = None


# латинские буквы, неотличимые от кириллических (раскладка переключилась посреди слова)
_LAT_TO_CYR = str.maketrans("aeopcxykmtbh", "аеорсхукмтвн")
_CYR_TO_LAT = str.maketrans("аеорсхукмтвн", "aeopcxykmtbh")


def _fix_layout(word: str) -> str:
    """В слове с кириллицей латинские двойники заменяем на русские буквы."""
    return word.translate(_LAT_TO_CYR) if re.search(r"[а-я]", word) else word


def _skeleton(word: str) -> str:
    """Все буквы-двойники → латиница: «Мар» и «Map» дают одно и то же."""
    return word.translate(_CYR_TO_LAT)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or ""))
    # невидимые символы из копипаста: zero-width, мягкий перенос и т.п.
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[‐-―−]", "-", s)  # все виды тире → дефис
    s = re.sub(r"[^\w\s-]|_", " ", s)
    s = re.sub(r"(?<!\w)-+|-+(?!\w)", " ", s)  # дефис только внутри слова
    return " ".join(_fix_layout(w) for w in s.split())


@lru_cache(maxsize=4096)
def _lemmas(word: str) -> frozenset:
    if _MORPH is None:
        return frozenset({word})
    return frozenset({word} | {p.normal_form.replace("ё", "е") for p in _MORPH.parse(word)})


def check_open(user: str, key: str) -> bool:
    """Строго по ключу, но с учётом падежа/числа/рода: «Коттера» = «Коттер»."""
    u, k = _norm(user), _norm(key)
    if not u:
        return False
    if u == k:
        return True
    uw, kw = u.split(), k.split()
    if len(uw) != len(kw):
        return False
    return all(a == b or _skeleton(a) == _skeleton(b) or (_lemmas(a) & _lemmas(b))
               for a, b in zip(uw, kw))


def grade(q, answer, partial: bool = True) -> float:
    """answer: str (single/open), set (multi), list меток (order), dict (match)."""
    if answer in (None, "", set(), [], {}):
        return 0.0

    if q.qtype == "open":
        return 1.0 if check_open(answer, q.key) else 0.0

    if q.qtype == "single":
        return 1.0 if answer in q.key else 0.0

    if q.qtype == "multi":
        chosen = set(answer)
        if chosen == q.key:
            return 1.0
        if not partial:
            return 0.0
        hits, misses = len(chosen & q.key), len(chosen - q.key)
        return max(0.0, (hits - misses) / len(q.key))

    if q.qtype == "order":
        if list(answer) == q.key:
            return 1.0
        if not partial:
            return 0.0
        return sum(a == b for a, b in zip(answer, q.key)) / len(q.key)

    if q.qtype == "match":
        right = sum(answer.get(n) == l for n, l in q.key.items())
        if right == len(q.key):
            return 1.0
        return right / len(q.key) if partial else 0.0

    return 0.0


def format_answer(q, answer) -> str:
    """Человекочитаемый ответ в формате ключа из Excel."""
    if answer in (None, "", set(), [], {}):
        return "—"
    if q.qtype == "open":
        return str(answer)
    if q.qtype in ("single", "multi"):
        return "; ".join(sorted(answer if isinstance(answer, (set, list)) else [answer]))
    if q.qtype == "order":
        return " → ".join(a or "?" for a in answer)
    if q.qtype == "match":
        return "; ".join(f"{n} — {answer.get(n) or '?'}" for n, _ in q.options)
    return str(answer)
