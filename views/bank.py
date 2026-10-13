import html
import random

import streamlit as st

from lib.bank import QTYPE_NAMES
from lib.ui import filter_widgets, get_bank, prepare_quiz, usable_pool

PAGE_SIZE = 15


def answers_allowed() -> bool:
    """Показ ответов можно выключить в секретах: [student_bank] show_answers = false."""
    try:
        return bool(st.secrets.get("student_bank", {}).get("show_answers", True))
    except Exception:
        return True


def norm(s: str) -> str:
    return str(s).lower().replace("ё", "е")


def question_body(q) -> str:
    """Вопрос без ответа: порядок и пары не выдают ключ."""
    e = html.escape
    parts = [f"<p><b>{e(q.text)}</b></p>"]
    if q.qtype in ("single", "multi", "order"):
        sep = ")" if q.qtype != "order" else "."
        parts.append("<br>".join(f"{e(l)}{sep} {e(t)}" for l, t in q.options))
    elif q.qtype == "match":
        left = "<br>".join(f"{e(n)}. {e(t)}" for n, t in q.options)
        right = "<br>".join(f"{e(l)}. {e(t)}" for l, t in q.right)
        parts.append("<div style='display:flex;gap:2rem;flex-wrap:wrap'>"
                     f"<div style='flex:1;min-width:14rem'>{left}</div>"
                     f"<div style='flex:1;min-width:14rem'>{right}</div></div>")
    return "".join(parts)


def answer_html(q) -> str:
    return ("<div style='border-left:3px solid #1a7f37;padding:.2rem 0 .2rem .7rem;margin-top:.4rem'>"
            f"<span style='color:#1a7f37;font-weight:600'>Ответ</span><br>{_answer(q)}</div>")


def _answer(q) -> str:
    e = html.escape
    text_of = dict(q.options)
    if q.qtype == "open":
        return f"<b>{e(q.key)}</b>"
    if q.qtype in ("single", "multi"):
        return "<br>".join(f"✔ {e(l)}) {e(text_of.get(l, ''))}" for l, _ in q.options if l in q.key)
    if q.qtype == "order":
        return "<br>".join(f"{i}. {e(text_of.get(l, '?'))}" for i, l in enumerate(q.key, 1))
    right = dict(q.right)
    return "<br>".join(f"{e(text_of.get(n, n))} → {e(right.get(l, '?'))}" for n, l in q.key.items())


st.title("📚 Банк вопросов")
bank, df = get_bank()
show_answers = answers_allowed()

with st.expander("Фильтры", expanded=True):
    sub = filter_widgets(usable_pool(df), "bk")
    query = st.text_input("Поиск по тексту", placeholder="Слово или фраза из вопроса, вариантов или ответа",
                          key="bk_query").strip()
if query:
    needle = norm(query)
    sub = sub[(sub["Q"] + " " + sub["Q_opt"] + " " + sub["A"]).map(norm).str.contains(needle, regex=False)]

c1, c2 = st.columns([3, 2])
c1.caption(f"Найдено вопросов: {len(sub)}")
reveal_all = c2.toggle("Показать все ответы", key="bk_reveal") if show_answers else False

if sub.empty:
    st.info("Под выбранные фильтры вопросов нет.")
    st.stop()

train_n = min(len(sub), 20)
if st.button(f"🎯 Потренироваться на этих вопросах"
             + (f" ({train_n} случайных из {len(sub)})" if len(sub) > train_n else f" ({len(sub)})")):
    # передаём подборку в тренажёр: новая попытка сразу в режиме прохождения
    st.session_state["tr_attempt"] = st.session_state.get("tr_attempt", 0) + 1
    prepare_quiz(f"trq{st.session_state['tr_attempt']}",
                 random.sample(sub["qid"].tolist(), train_n), shuffle=True)
    st.session_state["tr_stage"] = "quiz"
    st.switch_page("views/trainer.py")

pages = (len(sub) - 1) // PAGE_SIZE + 1
# сменились фильтры или поиск — начинаем с первой страницы
signature = hash(tuple(sub["qid"]))
if st.session_state.get("bk_sig") != signature:
    st.session_state["bk_sig"], st.session_state["bk_page"] = signature, 1
page = min(st.session_state.get("bk_page", 1), pages)
if pages > 1:
    jump = st.selectbox("Страница", range(1, pages + 1), index=page - 1,
                        format_func=lambda p: f"Страница {p} из {pages}", label_visibility="collapsed",
                        key=f"bk_jump_{page}_{signature}")
    if jump != page:
        st.session_state["bk_page"] = jump
        st.rerun()

rows = sub.sort_values(["code", "competence"]).iloc[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
last_code = None
for i, qid in enumerate(rows["qid"], (page - 1) * PAGE_SIZE + 1):
    q = bank[qid]
    if q.code != last_code:
        st.subheader(f"{q.code} · {q.discipline}")
        last_code = q.code
    with st.container(border=True):
        st.caption(f"№ {i} · {q.competence} · {QTYPE_NAMES[q.qtype]}")
        st.markdown(question_body(q), unsafe_allow_html=True)
        if reveal_all:
            st.markdown(answer_html(q), unsafe_allow_html=True)
        elif show_answers:
            with st.expander("Показать ответ"):
                st.markdown(answer_html(q), unsafe_allow_html=True)

if pages > 1:
    c1, c2, c3 = st.columns([1, 2, 1])
    if c1.button("◀ Назад", disabled=page == 1, width="stretch"):
        st.session_state["bk_page"] = page - 1
        st.rerun()
    c2.markdown(f"<p style='text-align:center;margin-top:.4rem'>Страница {page} из {pages}</p>",
                unsafe_allow_html=True)
    if c3.button("Вперёд ▶", disabled=page == pages, width="stretch"):
        st.session_state["bk_page"] = page + 1
        st.rerun()
