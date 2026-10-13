import json
import random
import secrets

import pandas as pd
import streamlit as st

from lib.storage import now_str, read_table, write
from lib.ui import (STATUS_ICONS, esc, filter_widgets, get_bank, require_admin, review_statuses,
                    show_bank_issues, usable_pool)

st.title("🛠️ Конструктор тестов")
require_admin("builder")

bank, df = get_bank()
show_bank_issues()


def test_link(test_id: str) -> str:
    try:
        base = str(st.secrets.get("app_url", "")).rstrip("/")
    except Exception:
        base = ""
    return f"{base}/?test={test_id}" if base else f"?test={test_id}"


tab_new, tab_list = st.tabs(["Новый тест", "Мои тесты"])

with tab_new:
    title = st.text_input("Название теста", placeholder="Например: Входной контроль, 1 курс")
    description = st.text_area("Инструкция для студентов (необязательно)", height=80)

    st.subheader("Вопросы")
    statuses = review_statuses()
    skip_bad = st.toggle("Не брать вопросы с 🟡 и 🔴", value=True)
    # вопросы с ошибками в Excel не берутся никогда: их нельзя корректно проверить
    pool = usable_pool(df) if skip_bad else df[~df["broken"]]
    sub = filter_widgets(pool, "bd")
    st.caption(f"Подходит под фильтры: {len(sub)}")

    mode = st.radio("Как отобрать", ["Случайно", "Вручную"], horizontal=True)
    if sub.empty:
        chosen = []
    elif mode == "Случайно":
        n = st.number_input("Количество вопросов", 1, len(sub), min(20, len(sub)))
        balanced = st.checkbox("Поровну по дисциплинам", value=True)
        seed_key = "bd_seed"
        if st.button("Перемешать выборку"):
            st.session_state[seed_key] = random.random()
        rng = random.Random(st.session_state.setdefault(seed_key, random.random()))
        if balanced:
            groups = {c: g["qid"].tolist() for c, g in sub.groupby("code")}
            for g in groups.values():
                rng.shuffle(g)
            chosen = []
            while len(chosen) < n:  # по кругу берём по одному вопросу из каждой дисциплины
                for g in groups.values():
                    if g and len(chosen) < n:
                        chosen.append(g.pop())
        else:
            chosen = rng.sample(sub["qid"].tolist(), int(n))
    else:
        table = sub[["qid", "ID", "type_name", "Q"]].copy()
        table.insert(0, "✓", False)
        table.insert(1, "статус", table["qid"].map(lambda q: STATUS_ICONS[statuses.get(q, "")]))
        edited = st.data_editor(
            table, hide_index=True, width="stretch", disabled=["статус", "ID", "type_name", "Q"],
            column_config={"qid": None, "type_name": "Тип", "Q": st.column_config.TextColumn("Вопрос", width="large")},
            key=f"bd_editor_{len(sub)}",
        )
        chosen = edited.loc[edited["✓"], "qid"].tolist()

    st.subheader("Настройки")
    c1, c2 = st.columns(2)
    shuffle = c1.toggle("Перемешивать вопросы и варианты", value=True)
    show_review = c2.toggle("Показать студенту разбор после отправки", value=True)
    scoring = st.radio("Начисление баллов", ["partial", "strict"], horizontal=True, format_func={
        "partial": "Частичные баллы (доля верных элементов)",
        "strict": "Только полностью верный ответ",
    }.get)

    with st.expander(f"Предпросмотр: {len(chosen)} вопр."):
        for i, qid in enumerate(chosen, 1):
            st.markdown(f"{i}. `{bank[qid].group_id}` {esc(bank[qid].text)}")

    if st.button("Создать тест", type="primary", disabled=not (title.strip() and chosen)):
        test_id = secrets.token_hex(4)
        try:
            with st.spinner("Сохраняю тест…"):
                write("tests", {
                    "test_id": test_id, "title": title.strip(), "description": description.strip(),
                    "created_at": now_str(), "question_ids": json.dumps(chosen),
                "versions": json.dumps({q: bank[q].fingerprint for q in chosen}),
                    "shuffle": int(shuffle), "scoring": scoring, "show_review": int(show_review), "active": 1,
                })
        except Exception as e:
            st.error(f"Тест не сохранён, попробуйте ещё раз. {e}")
        else:
            st.success("Тест создан. Ссылка для студентов:")
            st.code(test_link(test_id), language=None)

with tab_list:
    try:
        tests = read_table("tests")
        attempts = read_table("attempts")
    except Exception as e:
        st.error(f"Не удалось прочитать таблицу. {e}")
        st.stop()
    if tests.empty:
        st.info("Тестов пока нет.")
    for _, t in tests.iloc[::-1].iterrows():
        qids = json.loads(t["question_ids"])
        versions = json.loads(t["versions"]) if t.get("versions") else {}
        n_a = int((attempts["test_id"] == t["test_id"]).sum()) if not attempts.empty else 0
        active = str(t["active"]) == "1"
        gone = [q for q in qids if q not in bank]
        broken = [q for q in qids if q in bank and bank[q].broken]
        changed = [q for q in qids if q in bank and versions.get(q) and versions[q] != bank[q].fingerprint]
        with st.container(border=True):
            st.markdown(f"**{esc(t['title'])}** {'🟢 открыт' if active else '⏸️ закрыт'}")
            st.caption(f"{t['created_at']} · вопросов: {len(qids) - len(gone)} · попыток: {n_a}")
            if gone:
                st.warning(f"Удалены из банка: {len(gone)} — студенты их не увидят.")
            if broken:
                st.error(f"С ошибками в Excel: {len(broken)} — проверка может быть неверной "
                         f"(строки {', '.join(str(bank[q].row) for q in broken)}).")
            if changed:
                st.info(f"Исправлены после создания теста: {len(changed)}.")
            st.code(test_link(t["test_id"]), language=None)
            if st.button("Закрыть приём" if active else "Открыть приём", key=f"toggle_{t['test_id']}"):
                try:
                    with st.spinner("Сохраняю…"):
                        write("tests", {**t.to_dict(), "active": 0 if active else 1}, mode="upsert")
                except Exception as e:
                    st.error(f"Не сохранилось, попробуйте ещё раз. {e}")
                else:
                    st.rerun()
