import difflib
import html
import io
import json
import re

import pandas as pd
import streamlit as st

from lib.bank import QTYPE_NAMES
from lib.storage import get_storage, now_str, read_table, write_background
from lib.ui import (STATUS_ICONS, STATUS_NAMES, effective_status, esc, filter_widgets, get_bank,
                    require_admin, show_bank_issues)

st.title("🚦 Разметка вопросов")
require_admin("review")

bank, df = get_bank()
show_bank_issues()

# Сохранение идёт в фоне (Google отвечает по 2–10 с): сохранённое сразу видно из local,
# а pending — очередь записей в таблицу, которые ещё не подтверждены.
local = st.session_state.setdefault("rv_local", {})
pending = st.session_state.setdefault("rv_pending", {})

failed = {qid for qid, f in pending.items() if f.done() and f.exception() is not None}
for qid in [q for q, f in pending.items() if f.done() and f.exception() is None]:
    del pending[qid]
in_flight = len(pending) - len(failed)
if failed:
    c1, c2 = st.columns([4, 1])
    c1.warning(f"Не записалось в таблицу: {len(failed)}. Оценки сохранены на экране — повторите отправку.")
    if c2.button("Повторить", width="stretch"):
        for qid in failed:
            pending[qid] = write_background("reviews", local[qid])
        st.rerun()
elif in_flight:
    st.caption(f"⏳ Записывается в таблицу: {in_flight}")

try:
    stored = read_table("reviews")
except Exception as e:
    st.warning(f"Не удалось прочитать разметку из таблицы, показываю только текущую сессию. {e}")
    stored = pd.DataFrame(columns=["qid"])
reviews = {r["qid"]: r for r in stored.to_dict("records")} | local

if getattr(get_storage(), "outdated", False):
    st.error("Скрипт в Google-таблице устарел: отметка «изменён после оценки» не сохранится. "
             "Обновите его по разделу «Обновление скрипта» в SETUP.md.")


def review_of(qid: str) -> dict:
    return reviews.get(qid, {})


def status_of(qid: str) -> str:
    return effective_status(review_of(qid), bank.get(qid))


def mark(text: str, fragment: str) -> str:
    """Экранирует текст и подсвечивает в нём фрагмент, отмеченный экспертом."""
    safe = html.escape(text)
    frag = html.escape(fragment.strip())
    if frag and frag in safe:
        safe = safe.replace(frag, f"<mark>{frag}</mark>")
    return safe


def diff_html(old: str, new: str) -> str:
    """Пословное сравнение: удалённое зачёркнуто красным, добавленное — зелёным."""
    a, b = re.split(r"(\s+)", old), re.split(r"(\s+)", new)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape("".join(a[i1:i2])))
            continue
        if i2 > i1:
            out.append(f"<del style='background:#ffd7d5;color:#82071e'>{html.escape(''.join(a[i1:i2]))}</del>")
            if j2 > j1:
                out.append(" ")  # иначе удалённое и вставленное слипаются
        if j2 > j1:
            out.append(f"<ins style='background:#ccffd8;color:#055d20;text-decoration:none'>"
                       f"{html.escape(''.join(b[j1:j2]))}</ins>")
    return "".join(out)


def show_changes(q, prev: dict) -> None:
    try:
        old = json.loads(prev.get("snapshot") or "{}")
    except ValueError:
        old = {}
    status = prev.get("status", "")
    st.info(f"🔁 Вопрос исправлен после оценки ({prev.get('updated_at', '')}). "
            f"Прежняя оценка: {STATUS_ICONS.get(status, '')} {STATUS_NAMES.get(status, status)}. "
            "Оцените заново.")
    with st.expander("Что изменилось", expanded=True):
        if not old:
            st.caption("Оценка поставлена до того, как приложение начало запоминать текст вопроса, "
                       "поэтому прежняя версия не сохранилась. Ниже — ваши замечания при оценке.")
        for field, title in (("Q", "Вопрос"), ("Q_opt", "Варианты"), ("A", "Ключ")) if old else ():
            before, after = old.get(field, ""), q.snapshot[field]
            if before != after:
                st.markdown(f"**{title}:** {diff_html(before, after)}", unsafe_allow_html=True)
        notes = [f"**{name}:** {esc(prev[c])}" for c, name in (
            ("parts", "Что исправить"), ("fragment", "Фрагмент"), ("comment", "Комментарий"),
            ("suggestion", "Предлагаемая редакция")) if prev.get(c)]
        if notes:
            st.markdown("Ваши замечания при оценке:  \n" + "  \n".join(notes))


def show_question(q, fragment: str) -> None:
    st.markdown(f"<p style='font-size:1.1rem'><b>{mark(q.text, fragment)}</b></p>",
                unsafe_allow_html=True)
    rows = []
    if q.qtype in ("single", "multi"):
        for l, t in q.options:
            ok = "✔" if l in q.key else ""
            rows.append(f"<li><b>{l})</b> {mark(t, fragment)} <b style='color:#1a7f37'>{ok}</b></li>")
    elif q.qtype == "order":
        text_of = dict(q.options)
        for l, t in q.options:
            rows.append(f"<li><b>{l}.</b> {mark(t, fragment)}</li>")
        rows.append("<li><b>Порядок:</b> " + " → ".join(
            f"{html.escape(text_of.get(l, l))}" for l in q.key) + "</li>")
    elif q.qtype == "match":
        right = dict(q.right)
        for n, t in q.options:
            l = q.key.get(n, "?")
            rows.append(f"<li><b>{n}.</b> {mark(t, fragment)} → <b>{l}.</b> "
                        f"{mark(right.get(l, '?'), fragment)}</li>")
        unused = [l for l, _ in q.right if l not in q.key.values()]
        for l in unused:
            rows.append(f"<li><i>лишний: {l}. {mark(right[l], fragment)}</i></li>")
    if rows:
        st.markdown("<ul style='list-style:none;padding-left:0'>" + "".join(rows) + "</ul>",
                    unsafe_allow_html=True)
    st.markdown(f"**Ключ:** `{q.raw_answer}`")


tab_mark, tab_summary = st.tabs(["Разметка", "Сводка и выгрузка"])

with tab_mark:
    with st.expander("Фильтры", expanded=False):
        sub = filter_widgets(df, "rv")
        status_filter = st.pills("Статус", list(STATUS_NAMES), selection_mode="multi",
                                 format_func=lambda s: f"{STATUS_ICONS[s]} {STATUS_NAMES[s]}",
                                 key="rv_status_filter")
    if status_filter:
        sub = sub[sub["qid"].map(status_of).isin(status_filter)]
    qids = sub["qid"].tolist()

    statuses = {qid: status_of(qid) for qid in df["qid"]}
    done = sum(s not in ("", "changed") for s in statuses.values())
    changed_n = sum(s == "changed" for s in statuses.values())
    st.progress(done / len(df), text=f"Размечено {done} из {len(df)}"
                + (f" · 🔁 исправлено после оценки: {changed_n}" if changed_n else ""))

    if not qids:
        st.info("Нет вопросов под выбранные фильтры.")
    else:
        idx = min(st.session_state.get("rv_idx", 0), len(qids) - 1)
        st.session_state["rv_idx"] = idx

        def label(i: int) -> str:
            q = bank[qids[i]]
            return f"{i + 1}. {STATUS_ICONS[status_of(q.qid)]} [{q.group_id}] {q.text[:70]}"

        c1, c2, c3 = st.columns([1, 6, 1])
        if c1.button("◀", disabled=idx == 0, width="stretch"):
            st.session_state["rv_idx"] = idx - 1
            st.rerun()
        picked = c2.selectbox("Вопрос", range(len(qids)), index=idx, format_func=label,
                              label_visibility="collapsed", key=f"rv_pick_{idx}_{len(qids)}")
        if picked != idx:
            st.session_state["rv_idx"] = picked
            st.rerun()
        if c3.button("▶", disabled=idx >= len(qids) - 1, width="stretch"):
            st.session_state["rv_idx"] = idx + 1
            st.rerun()

        q = bank[qids[idx]]
        prev = review_of(q.qid)
        current = status_of(q.qid)
        k = f"rv_{q.qid}"

        if current == "changed":
            show_changes(q, prev)
        if q.broken:
            st.error(f"Ошибка в Excel (строка {q.row}): " + "; ".join(q.problems)
                     + ". Вопрос не выдаётся студентам, пока её не исправят.")

        with st.container(border=True):
            st.caption(f"{q.group_id} · {q.discipline} · {QTYPE_NAMES[q.qtype]} · "
                       f"семестр {', '.join(map(str, q.semesters))} · курс {', '.join(map(str, q.courses))}")
            fragment_now = st.session_state.get(f"{k}_fragment", prev.get("fragment", ""))
            show_question(q, fragment_now)

        status = st.segmented_control(
            "Оценка", ["green", "yellow", "red"], key=f"{k}_status",
            # исправленный вопрос оценивается заново — прежняя оценка не подставляется
            default=(prev.get("status") or None) if current != "changed" else None,
            format_func=lambda s: f"{STATUS_ICONS[s]} {STATUS_NAMES[s]}",
        )

        parts_options = ["Формулировка вопроса"]
        if q.qtype in ("single", "multi", "order", "match"):
            parts_options += [f"Вариант {l}" for l, _ in q.options]
        if q.qtype == "match":
            parts_options += [f"Вариант {l}" for l, _ in q.right]
        parts_options += ["Ключ ответа", "Тип задания", "Другое"]
        fresh = current == "changed"  # после правки старые замечания не подставляем
        prev_parts = [] if fresh else [p for p in str(prev.get("parts", "")).split("; ") if p in parts_options]

        def prev_value(field):
            return "" if fresh else prev.get(field, "")

        if status in ("yellow", "red"):
            parts = st.multiselect("Что исправить", parts_options, default=prev_parts, key=f"{k}_parts",
                                   placeholder="Выберите части вопроса")
            fragment = st.text_input("Проблемный фрагмент (скопируйте из вопроса — он подсветится)",
                                     value=prev_value("fragment"), key=f"{k}_fragment")
            comment = st.text_area("Комментарий", value=prev_value("comment"), key=f"{k}_comment")
            suggestion = st.text_area("Предлагаемая редакция (необязательно)",
                                      value=prev_value("suggestion"), key=f"{k}_suggestion")
        else:
            parts, fragment, suggestion = [], "", ""
            comment = st.text_input("Комментарий (необязательно)", value=prev_value("comment"),
                                    key=f"{k}_comment_ok")

        if st.button("Сохранить и далее", type="primary", disabled=status is None):
            row = {
                "qid": q.qid, "group_id": q.group_id, "status": status,
                "parts": "; ".join(parts), "fragment": fragment, "comment": comment,
                "suggestion": suggestion, "updated_at": now_str(),
                # снимок вопроса: по нему потом видно, что вопрос исправили после оценки
                "snapshot": json.dumps(q.snapshot, ensure_ascii=False),
            }
            local[q.qid] = row
            pending[q.qid] = write_background("reviews", row)
            # при фильтре по статусу размеченный вопрос уходит из списка — индекс не двигаем
            if not status_filter:
                st.session_state["rv_idx"] = min(idx + 1, len(qids) - 1)
            st.toast(f"{STATUS_ICONS[status]} Сохранено")
            st.rerun()

with tab_summary:
    full = df.drop(columns=["semesters", "courses"]).assign(
        semesters=df["semesters"].map(lambda xs: ", ".join(map(str, xs))),
        courses=df["courses"].map(lambda xs: ", ".join(map(str, xs))),
    )
    if reviews:
        rv = pd.DataFrame(list(reviews.values())).drop(columns=["group_id", "snapshot"], errors="ignore")
        full = full.merge(rv, on="qid", how="left")
    for c in ["status", "parts", "fragment", "comment", "suggestion", "updated_at"]:
        if c not in full:
            full[c] = ""
    full = full.fillna("")
    full["current"] = full["qid"].map(status_of)

    counts = full["current"].value_counts()
    shown = ["green", "yellow", "red", "changed", ""]
    for col, s in zip(st.columns(len(shown)), shown):
        col.metric(f"{STATUS_ICONS[s]} {STATUS_NAMES[s]}", int(counts.get(s, 0)))

    pivot = (full.assign(current=full["current"].map(lambda s: f"{STATUS_ICONS[s]} {STATUS_NAMES[s]}"))
             .pivot_table(index=["code", "discipline"], columns="current", values="qid",
                          aggfunc="count", fill_value=0))
    st.dataframe(pivot, width="stretch")

    issues = full[full["current"].isin(["yellow", "red"])]
    st.subheader(f"На доработку: {len(issues)}")
    st.dataframe(issues[["status", "ID", "Q", "parts", "fragment", "comment", "suggestion"]],
                 width="stretch", hide_index=True)

    export = full.drop(columns=["qtype", "broken"]).rename(columns={"status": "оценка", "current": "статус сейчас"})
    export["статус сейчас"] = export["статус сейчас"].map(STATUS_NAMES)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        export.to_excel(xw, sheet_name="assessment_review", index=False)
    st.download_button("Скачать Excel с разметкой", buf.getvalue(), "assessment_review.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption("В выгрузке есть qid: по нему удобно найти вопрос в основном Excel. "
               "Правьте Q, Q_opt и A, а qid не трогайте.")
