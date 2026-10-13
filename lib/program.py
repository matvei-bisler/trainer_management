"""
Настройки программы — всё, чем один тренажёр отличается от другого.

Читаются из program.toml в корне приложения; код у всех тренажёров одинаковый.
"""

import re
import tomllib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

with open(BASE_DIR / "program.toml", "rb") as f:
    _cfg = tomllib.load(f)

CODE = _cfg["code"]                      # «38.03.02» — в заголовке приложения
NAME = _cfg.get("name", "")              # направление и профиль — подпись в боковой панели
EXCEL_PATH = BASE_DIR / _cfg["excel"]    # банк вопросов (листы assessment, disciplines)
PLAN_PATH = BASE_DIR / _cfg["plan"]      # учебный план в длинном формате (семестры и курсы)

# Префикс листов в общей Google-таблице: у каждого тренажёра свои reviews/tests/attempts.
# Пустой — листы без префикса (так хранит данные первый тренажёр, 50.04.01).
TABLE_PREFIX = _cfg.get("table_prefix", "")
if not re.fullmatch(r"[a-z_]*", TABLE_PREFIX):
    raise ValueError("table_prefix в program.toml: только строчные латинские буквы и «_»")
