"""
Тренажёр и тесты по ФОС. Программа задаётся в program.toml.

Запуск: streamlit run app.py
Студент по ссылке ?test=<id> видит только форму теста.
"""

import sys
from pathlib import Path

import streamlit as st

# После обновления кода на Streamlit Cloud (git pull) страницы выполняются заново,
# а уже импортированные модули lib.* остаются старыми. Если файлы lib/ изменились —
# выгружаем их, чтобы импортировались свежие версии.
_stamp = tuple(sorted((p.name, p.stat().st_mtime_ns) for p in (Path(__file__).parent / "lib").glob("*.py")))
# (метки нет, а lib уже загружен — значит, его загрузила версия приложения без этой проверки)
if "lib" in sys.modules and getattr(sys, "_trainer_lib_stamp", None) != _stamp:
    for name in [m for m in sys.modules if m == "lib" or m.startswith("lib.")]:
        del sys.modules[name]
    st.cache_resource.clear()
    st.cache_data.clear()
sys._trainer_lib_stamp = _stamp

from lib.program import CODE, NAME  # noqa: E402 — после проверки свежести модулей
from lib.storage import get_storage  # noqa: E402

st.set_page_config(page_title=f"Тренажёр {CODE}", page_icon="📝", layout="centered")

if st.query_params.get("test"):
    pg = st.navigation([st.Page("views/take_test.py", title="Тест", icon="📝")], position="hidden")
else:
    pg = st.navigation({
        "Студентам": [
            st.Page("views/trainer.py", title="Тренажёр", icon="🎯", default=True),
            st.Page("views/bank.py", title="Банк вопросов", icon="📚"),
        ],
        "Эксперт": [
            st.Page("views/review.py", title="Разметка вопросов", icon="🚦"),
            st.Page("views/builder.py", title="Конструктор тестов", icon="🛠️"),
            st.Page("views/results.py", title="Результаты", icon="📊"),
        ],
    })
    with st.sidebar:
        st.markdown(f"**{CODE}**" + (f"  \n{NAME}" if NAME else ""))
        st.caption(f"Хранилище: {get_storage().name}")
        logged_in = [k for k in st.session_state if k.startswith("auth_") and st.session_state[k]]
        if logged_in and st.button("Выйти"):
            for k in logged_in:
                st.session_state[k] = False
            st.rerun()

pg.run()
