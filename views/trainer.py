import random

import streamlit as st

from lib.ui import (filter_widgets, get_bank, prepare_quiz, render_review, run_quiz, score_answers,
                    score_summary, usable_pool)

# у каждой попытки свои ключи виджетов, чтобы новая тренировка начиналась с чистой формы
NS = f"trq{st.session_state.setdefault('tr_attempt', 0)}"

st.title("🎯 Тренажёр")
bank, df = get_bank()
stage = st.session_state.get("tr_stage", "setup")

if stage == "setup":
    st.write("Выберите, по чему тренироваться. Ответы проверяются автоматически, разбор — в конце.")
    # вопросы, отмеченные экспертом 🟡/🔴 или сломанные в Excel, в тренажёр не попадают
    sub = filter_widgets(usable_pool(df), "trf")

    if sub.empty:
        st.warning("Под выбранные фильтры нет вопросов.")
        st.stop()
    # у slider минимум не может совпадать с максимумом
    n = st.slider("Сколько вопросов", 1, len(sub), min(10, len(sub))) if len(sub) > 1 else 1
    st.caption(f"Доступно вопросов: {len(sub)}")
    if st.button("Начать", type="primary"):
        prepare_quiz(NS, random.sample(sub["qid"].tolist(), n), shuffle=True)
        st.session_state["tr_stage"] = "quiz"
        st.rerun()

elif stage == "quiz":
    answers = run_quiz(NS)
    if answers is not None:
        st.session_state["tr_results"] = score_answers(answers, partial=True)
        st.session_state["tr_stage"] = "done"
        st.rerun()
    if st.button("Отменить"):
        st.session_state["tr_attempt"] += 1
        st.session_state["tr_stage"] = "setup"
        st.rerun()

else:
    results = st.session_state["tr_results"]
    total, n, pct = score_summary(results)
    c1, c2 = st.columns(2)
    c1.metric("Баллы", f"{total:g} из {n}")
    c2.metric("Результат", f"{pct:g}%")
    if st.button("Новая тренировка", type="primary"):
        st.session_state["tr_attempt"] += 1
        st.session_state["tr_stage"] = "setup"
        st.rerun()
    st.subheader("Разбор")
    render_review(results)
