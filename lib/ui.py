"""Общие элементы интерфейса: загрузка банка, фильтры, отрисовка вопросов и разбора."""

import hmac
import json
import random
import re

import streamlit as st

from lib.bank import (EXCEL_PATH, PLAN_PATH, QTYPE_NAMES, bank_frame, filter_frame, load_bank, make_qid,
                      semester_courses, snapshot_fingerprint)
from lib.grading import format_answer, grade
from lib.storage import read_table

STATUS_ICONS = {"green": "🟢", "yellow": "🟡", "red": "🔴", "changed": "🔁", "": "⚪"}
STATUS_NAMES = {"green": "Хороший", "yellow": "Нужны правки", "red": "Плохой",
                "changed": "Изменён после оценки", "": "Не размечен"}
# в тренажёр и новые тесты не идут: отмеченные экспертом и сломанные в Excel
EXCLUDED_STATUSES = {"yellow", "red"}

HINTS = {
    "single": "Выберите один вариант",
    "multi": "Выберите все верные варианты",
    "order": "Расставьте элементы в правильном порядке",
    "match": "Сопоставьте элементы левого и правого столбцов",
    "open": "Впишите одно слово",
}


def esc(s) -> str:
    """Экранирует markdown: «___» в вопросах иначе превращается в разметку."""
    return re.sub(r"([\\`*_{}\[\]<>#~$|])", r"\\\1", str(s))


@st.cache_resource(show_spinner="Загружаю банк вопросов…", max_entries=2)
def _load(files_stamp: tuple):
    questions, issues = load_bank()
    return {q.qid: q for q in questions}, bank_frame(questions), issues


def _files_stamp() -> tuple:
    # кэш привязан к времени изменения файлов: новый Excel после пуша подхватывается сам
    return tuple(p.stat().st_mtime_ns for p in (EXCEL_PATH, PLAN_PATH))


def get_bank():
    bank, df, _ = _load(_files_stamp())
    return bank, df


def bank_issues() -> list:
    return _load(_files_stamp())[2]


def effective_status(review: dict, q) -> str:
    """Статус с учётом правок: если вопрос изменили после оценки — «changed»."""
    status = review.get("status", "") if review else ""
    if not status or q is None:
        return status
    if review.get("snapshot"):
        if snapshot_fingerprint(review["snapshot"]) != q.fingerprint:
            return "changed"
    elif make_qid(q.text, q.raw_opt) != q.qid:
        # оценка поставлена до появления снимков: тогда qid был хэшем текста и вариантов,
        # так что их правку видно по расхождению с qid (правку одного ключа — нет)
        return "changed"
    return status


def review_statuses() -> dict:
    """{qid: действующий статус} по данным разметки."""
    try:
        df = read_table("reviews")
    except Exception:  # без связи тренажёр работает, просто без фильтра по светофору
        return {}
    bank, _ = get_bank()
    return {r["qid"]: effective_status(r, bank.get(r["qid"])) for r in df.to_dict("records")}


def usable_pool(df):
    """Вопросы для тренажёра и новых тестов: без сломанных и без 🟡/🔴."""
    statuses = review_statuses()
    return df[~df["broken"] & ~df["qid"].map(statuses.get).isin(EXCLUDED_STATUSES)]


def show_bank_issues() -> None:
    """Для эксперта: проблемы в Excel, найденные при загрузке банка."""
    issues = bank_issues()
    if not issues:
        return
    errors = sum(i["level"] == "error" for i in issues)
    title = (f"В банке вопросов проблем: {len(issues)}, из них ошибок: {errors}. "
             "Вопросы с ошибками не выдаются в тренажёр и новые тесты.")
    with st.expander(("❗ " if errors else "⚠️ ") + title):
        st.dataframe(
            [{"Строка Excel": i["row"], "qid": i["qid"], "": "ошибка" if i["level"] == "error" else "предупр.",
              "Проблема": i["message"]} for i in issues],
            hide_index=True, width="stretch",
        )
        st.caption("Исправьте Excel, проверьте командой `python check_bank.py` и отправьте в репозиторий.")


# ---------- доступ ----------

PAGE_TITLES = {"review": "Разметка вопросов", "builder": "Конструктор тестов", "results": "Результаты"}


def _secret(*path):
    try:
        value = st.secrets
        for key in path:
            value = value.get(key)
        return str(value) if value else ""
    except Exception:
        return ""


def require_admin(page: str) -> None:
    """У каждой страницы эксперта свой пароль ([passwords] в секретах).
    admin_password, если задан, — общий мастер-пароль, открывает все страницы."""
    if st.session_state.get(f"auth_{page}"):
        return
    accepted = [p for p in (_secret("passwords", page), _secret("admin_password")) if p]
    if not accepted:
        st.error(f"Пароль не задан: добавьте `{page}` в раздел `[passwords]` секретов.")
        st.stop()
    with st.form(f"login_{page}"):
        pwd = st.text_input(f"Пароль для раздела «{PAGE_TITLES[page]}»", type="password")
        if st.form_submit_button("Войти"):
            if any(hmac.compare_digest(pwd.encode(), p.encode()) for p in accepted):
                st.session_state[f"auth_{page}"] = True
                st.rerun()
            st.error("Неверный пароль")
    st.stop()


# ---------- фильтры ----------

@st.cache_data(show_spinner=False)
def _semester_courses() -> dict:
    return semester_courses()


def _multiselect(container, label: str, options: list, key: str, **kwargs) -> list:
    """multiselect, который молча убирает из выбора значения, пропавшие из вариантов
    (например, семестры 3–4 после выбора 1-го курса)."""
    if key in st.session_state:
        st.session_state[key] = [v for v in st.session_state[key] if v in options]
    return container.multiselect(label, options, key=key, placeholder="Все", **kwargs)


def filter_widgets(df, key: str, with_types: bool = True):
    """Каскадные фильтры курс ⇄ семестр → дисциплина → компетенция → тип.

    Курс и семестр связаны по учебному плану (1–2 семестр — 1 курс, 3–4 — 2 курс):
    выбор курса оставляет только его семестры, выбор семестра проставляет его курс,
    а вопрос подходит, если у его дисциплины есть занятия в выбранных семестрах.
    """
    sem_course = _semester_courses()
    present = sorted({s for xs in df["semesters"] for s in xs if s in sem_course})

    # выбрали семестр при пустом курсе — курс проставляется сам (3 семестр → 2 курс)
    ck, sk = f"{key}_courses", f"{key}_sems"
    if st.session_state.get(sk) and not st.session_state.get(ck):
        st.session_state[ck] = sorted({sem_course[s] for s in st.session_state[sk] if s in sem_course})

    c1, c2 = st.columns(2)
    courses = _multiselect(c1, "Курс", sorted({sem_course[s] for s in present}), f"{key}_courses",
                           format_func=lambda c: f"{c} курс")
    sem_options = [s for s in present if not courses or sem_course[s] in courses]
    semesters = _multiselect(c2, "Семестр", sem_options, f"{key}_sems",
                             format_func=lambda s: f"{s} семестр ({sem_course[s]} курс)")
    sub = df
    if courses or semesters:
        allowed = set(semesters or sem_options)
        sub = df[df["semesters"].map(lambda xs: bool(allowed & set(xs)))]

    disc = sub.drop_duplicates("code").sort_values("code").set_index("code")
    codes = _multiselect(st, "Дисциплина", disc.index.tolist(), f"{key}_codes",
                         format_func=lambda c: f"{c} · {disc.at[c, 'discipline']}")
    sub = filter_frame(sub, codes=codes)

    c3, c4 = st.columns(2)
    comps = _multiselect(c3, "Компетенция", sorted(sub["competence"].unique()), f"{key}_comps")
    sub = filter_frame(sub, competences=comps)
    if with_types:
        types = _multiselect(c4, "Тип задания", list(QTYPE_NAMES), f"{key}_types", format_func=QTYPE_NAMES.get)
        sub = filter_frame(sub, qtypes=types)
    return sub


# ---------- прохождение ----------

def _perm(rng: random.Random, n: int, shuffle: bool) -> list:
    idx = list(range(n))
    if shuffle:
        rng.shuffle(idx)
    return idx


def prepare_quiz(ns: str, qids: list, shuffle: bool) -> None:
    """Фиксирует порядок вопросов и вариантов на всю попытку (иначе он меняется при каждом rerun)."""
    bank, _ = get_bank()
    rng = random.Random()
    qids = [q for q in qids if q in bank]
    if shuffle:
        rng.shuffle(qids)
    layout = {}
    for qid in qids:
        q = bank[qid]
        layout[qid] = {
            "opts": _perm(rng, len(q.options), shuffle),
            "right": _perm(rng, len(q.right), shuffle),
        }
    st.session_state[f"{ns}_quiz"] = {"qids": qids, "layout": layout}


def render_question(q, i: int, ns: str, layout: dict):
    key = f"{ns}_{q.qid}"
    st.markdown(f"**{i}.** {esc(q.text)}")
    st.caption(HINTS[q.qtype])
    opts = [q.options[j] for j in layout["opts"]]
    text_of = dict(q.options)

    if q.qtype == "single":
        return st.radio("Ответ", [l for l, _ in opts], index=None, key=key,
                        format_func=lambda l: esc(text_of[l]), label_visibility="collapsed")

    if q.qtype == "multi":
        return {l for l, t in opts if st.checkbox(esc(t), key=f"{key}_{l}")}

    if q.qtype == "order":
        labels = [l for l, _ in opts]
        answer = [
            st.selectbox(f"Позиция {p}", labels, index=None, key=f"{key}_{p}",
                         format_func=lambda l: text_of[l], placeholder="Выберите…")
            for p in range(1, len(labels) + 1)
        ]
        return answer

    if q.qtype == "match":
        right = [q.right[j] for j in layout["right"]]
        right_text = dict(q.right)
        answer = {}
        for n, t in opts:
            answer[n] = st.selectbox(esc(t), [l for l, _ in right], index=None, key=f"{key}_{n}",
                                     format_func=lambda l: right_text[l], placeholder="Выберите…")
        return answer

    return st.text_input("Ответ", key=key, label_visibility="collapsed",
                         placeholder="Ваш ответ").strip()


def run_quiz(ns: str, submit_label: str = "Завершить и проверить"):
    """Рисует форму с вопросами. Возвращает {qid: ответ} после отправки, иначе None."""
    bank, _ = get_bank()
    quiz = st.session_state[f"{ns}_quiz"]
    # Enter в поле открытого ответа не должен отправлять весь тест
    with st.form(f"{ns}_form", enter_to_submit=False):
        answers = {}
        for i, qid in enumerate(quiz["qids"], 1):
            with st.container(border=True):
                answers[qid] = render_question(bank[qid], i, ns, quiz["layout"][qid])
        submitted = st.form_submit_button(submit_label, type="primary", width="stretch")
    return answers if submitted else None


def is_blank(a) -> bool:
    if a in (None, ""):
        return True
    if isinstance(a, (set, list)):
        return all(x in (None, "") for x in a)
    if isinstance(a, dict):
        return all(x in (None, "") for x in a.values())
    return False


def score_answers(answers: dict, partial: bool) -> list:
    bank, _ = get_bank()
    # только то, чего нет в банке: ячейка Google Sheets вмещает 50 000 символов.
    # f — версия вопроса, чтобы в результатах было видно, если его потом исправили
    return [{
        "qid": qid,
        "f": bank[qid].fingerprint,
        "answer": "—" if is_blank(a) else format_answer(bank[qid], a),
        "score": round(grade(bank[qid], a, partial=partial), 3),
    } for qid, a in answers.items()]


def details_json(results: list) -> str:
    return json.dumps(results, ensure_ascii=False)


# ---------- разбор ----------

def _answer_lines(q, answer_str: str, show_key: bool) -> str:
    text_of = dict(q.options)
    right_of = dict(q.right)

    def describe(s: str) -> str:
        if s in ("—", ""):
            return "_нет ответа_"
        if q.qtype == "open":
            return esc(s)
        if q.qtype in ("single", "multi"):
            return "; ".join(esc(text_of.get(l.strip(), l)) for l in s.split(";"))
        if q.qtype == "order":
            return " → ".join(esc(text_of.get(l.strip(), "?")) for l in s.split("→"))
        pairs = re.findall(r"(\d+)\s*[—–-]\s*([А-ЯЁA-Z?])", s)
        return "<br>".join(f"{esc(text_of.get(n, n))} → {esc(right_of.get(l, '?'))}" for n, l in pairs)

    lines = f"**Ваш ответ:** {describe(answer_str)}"
    if show_key:
        lines += f"  \n**Правильный ответ:** {describe(q.raw_answer)}"
    return lines


def render_review(results: list, show_key: bool = True) -> None:
    bank, _ = get_bank()
    for i, r in enumerate(results, 1):
        q = bank.get(r["qid"])
        s = float(r["score"])
        icon = "✅" if s >= 1 else ("🟨" if s > 0 else "❌")
        with st.container(border=True):
            if q is None:
                st.markdown(f"{icon} **{i}.** _вопрос удалён из банка_ (`{r['qid']}`)")
                st.caption(f"балл {s:g} · ответ: {esc(r['answer'])}")
                continue
            st.markdown(f"{icon} **{i}.** {esc(q.text)}")
            st.caption(f"{q.code} · {q.discipline} · {QTYPE_NAMES[q.qtype]} · балл {s:g}")
            if r.get("f") and r["f"] != q.fingerprint:
                st.caption("⚠️ Вопрос исправлен после прохождения: показана текущая версия, "
                           "балл — как был начислен при сдаче.")
            st.markdown(_answer_lines(q, r["answer"], show_key), unsafe_allow_html=True)


def score_summary(results: list) -> tuple:
    total = sum(float(r["score"]) for r in results)
    n = len(results)
    return round(total, 2), n, round(100 * total / n, 1) if n else 0.0
