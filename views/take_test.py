import json
import time
import uuid

import streamlit as st

from lib.storage import now_str, read_table, write
from lib.ui import (details_json, esc, is_blank, prepare_quiz, render_review, run_quiz,
                    score_answers, score_summary)

test_id = st.query_params.get("test", "")
NS = f"take_{test_id}"

# тест читается один раз: сбой связи посреди прохождения не должен стереть форму с ответами
test = st.session_state.get(f"{NS}_test")
if test is None:
    try:
        tests = read_table("tests")
    except Exception:
        st.error("Сервер временно недоступен. Обновите страницу через минуту.")
        st.stop()
    match = tests[tests["test_id"] == test_id] if not tests.empty else tests
    if match.empty:
        st.error("Тест не найден. Проверьте ссылку.")
        st.stop()
    test = st.session_state[f"{NS}_test"] = match.iloc[0].to_dict()
stage = st.session_state.get(f"{NS}_stage", "intro")

st.title(test["title"])

if stage != "done" and str(test["active"]) != "1":
    st.warning("Приём ответов по этому тесту закрыт.")
    st.stop()

if stage == "intro":
    if test.get("description"):
        st.write(esc(test["description"]))
    st.caption(f"Вопросов: {len(json.loads(test['question_ids']))}. "
               "Ответы сохраняются только после нажатия «Завершить».")
    with st.form(f"{NS}_intro"):
        fio = st.text_input("ФИО")
        direction = st.text_input("Направление подготовки")
        if st.form_submit_button("Начать тест", type="primary"):
            if not fio.strip() or not direction.strip():
                st.error("Заполните ФИО и направление.")
            else:
                st.session_state[f"{NS}_who"] = {"fio": fio.strip(), "direction": direction.strip()}
                st.session_state[f"{NS}_started"] = time.time()
                prepare_quiz(NS, json.loads(test["question_ids"]), shuffle=str(test["shuffle"]) == "1")
                st.session_state[f"{NS}_stage"] = "quiz"
                st.rerun()

elif stage == "quiz":
    who = st.session_state[f"{NS}_who"]
    st.caption(f"{who['fio']} · {who['direction']}")
    answers = run_quiz(NS, submit_label="Завершить тест")
    if answers is not None:
        blank = sum(is_blank(a) for a in answers.values())
        # первое нажатие с пропусками — только предупреждение, второе отправляет
        if blank and not st.session_state.get(f"{NS}_confirm"):
            st.session_state[f"{NS}_confirm"] = True
            st.warning(f"Без ответа: {blank}. Нажмите «Завершить тест» ещё раз, чтобы отправить.")
        else:
            results = score_answers(answers, partial=test["scoring"] != "strict")
            total, n, pct = score_summary(results)
            # один id на попытку: повторная отправка после сбоя не создаст дубль
            attempt_id = st.session_state.setdefault(f"{NS}_attempt_id", uuid.uuid4().hex[:12])
            try:
                with st.spinner("Сохраняем ответы… Это может занять до 20 секунд, не закрывайте страницу."):
                    write("attempts", {
                        "attempt_id": attempt_id, "test_id": test_id, "submitted_at": now_str(),
                        "fio": who["fio"], "direction": who["direction"],
                        "score": total, "max_score": n, "percent": pct,
                        "duration_sec": int(time.time() - st.session_state[f"{NS}_started"]),
                        "details": details_json(results),
                    }, mode="upsert")
            except Exception:
                # ответы остаются в форме — можно просто нажать ещё раз
                st.error("Не удалось сохранить ответы: нет связи с сервером. "
                         "Ничего не потеряно — нажмите «Завершить тест» ещё раз.")
            else:
                st.session_state[f"{NS}_results"] = results
                st.session_state[f"{NS}_stage"] = "done"
                st.rerun()

else:
    results = st.session_state[f"{NS}_results"]
    total, n, pct = score_summary(results)
    st.success("Ответы отправлены. Спасибо!")
    c1, c2 = st.columns(2)
    c1.metric("Баллы", f"{total:g} из {n}")
    c2.metric("Результат", f"{pct:g}%")
    if str(test["show_review"]) == "1":
        st.subheader("Разбор")
        render_review(results)
