import io
import json

import pandas as pd
import streamlit as st

from lib.bank import QTYPE_NAMES
from lib.storage import read_table
from lib.ui import get_bank, render_review, require_admin

st.title("📊 Результаты")
require_admin("results")

bank, _ = get_bank()
try:
    tests = read_table("tests")
    attempts = read_table("attempts")
except Exception as e:
    st.error(f"Не удалось прочитать таблицу, нажмите «Обновить» через минуту. {e}")
    if st.button("Обновить"):
        read_table.clear()
        st.rerun()
    st.stop()

if tests.empty:
    st.info("Тестов пока нет — создайте его в конструкторе.")
    st.stop()

tests = tests.iloc[::-1]
titles = dict(zip(tests["test_id"], tests["title"] + " · " + tests["created_at"]))
test_id = st.selectbox("Тест", list(titles), format_func=titles.get)
if st.button("Обновить"):
    read_table.clear()
    st.rerun()

rows = attempts[attempts["test_id"] == test_id].copy() if not attempts.empty else attempts
if rows.empty:
    st.info("По этому тесту ещё нет попыток.")
    st.stop()

for c in ["score", "max_score", "percent", "duration_sec"]:
    rows[c] = pd.to_numeric(rows[c], errors="coerce")
rows["минут"] = (rows["duration_sec"] / 60).round(1)

c1, c2, c3 = st.columns(3)
c1.metric("Попыток", len(rows))
c2.metric("Средний результат", f"{rows['percent'].mean():.1f}%")
c3.metric("Медиана", f"{rows['percent'].median():.1f}%")

table = rows[["submitted_at", "fio", "direction", "score", "max_score", "percent", "минут"]].rename(columns={
    "submitted_at": "Время", "fio": "ФИО", "direction": "Направление",
    "score": "Баллы", "max_score": "Макс.", "percent": "%",
})
st.dataframe(table, hide_index=True, width="stretch")

# длинная таблица ответов и статистика по вопросам
long = []
for _, r in rows.iterrows():
    for d in json.loads(r["details"]):
        q = bank.get(d["qid"])
        if q is None:
            version = "удалён из банка"
        elif d.get("f") and d["f"] != q.fingerprint:
            version = "исправлен после сдачи"
        else:
            version = ""
        long.append({
            "ФИО": r["fio"], "Направление": r["direction"], "ID": q.group_id if q else "",
            "Тип": QTYPE_NAMES[q.qtype] if q else "", "Вопрос": q.text if q else f"(удалён) {d['qid']}",
            "Ответ": d["answer"], "Ключ": q.raw_answer if q else "", "Балл": d["score"],
            "Версия вопроса": version, "qid": d["qid"],
        })
long = pd.DataFrame(long)
changed_n = long.loc[long["Версия вопроса"] != "", "qid"].nunique()
if changed_n:
    st.info(f"Вопросов, исправленных или удалённых после сдачи: {changed_n}. "
            "Баллы остались как были начислены; в выгрузке это видно в столбце «Версия вопроса».")
stats = (long.groupby(["qid", "ID", "Тип", "Вопрос"], as_index=False)
         .agg(**{"Средний балл": ("Балл", "mean"), "Ответов": ("Балл", "size")})
         .sort_values("Средний балл"))

st.subheader("Статистика по вопросам")
st.caption("Сверху — самые трудные")
st.dataframe(stats.drop(columns="qid"), hide_index=True, width="stretch",
             column_config={"Средний балл": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f")})

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as xw:
    table.to_excel(xw, sheet_name="Результаты", index=False)
    long.drop(columns="qid").to_excel(xw, sheet_name="Ответы", index=False)
    stats.drop(columns="qid").to_excel(xw, sheet_name="По вопросам", index=False)
st.download_button("Скачать Excel", buf.getvalue(), f"results_{test_id}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.subheader("Разбор попытки")
labels = {i: f"{r['fio']} · {r['percent']:g}% · {r['submitted_at']}" for i, r in rows.iterrows()}
pick = st.selectbox("Студент", list(labels), format_func=labels.get)
render_review(json.loads(rows.loc[pick, "details"]))
