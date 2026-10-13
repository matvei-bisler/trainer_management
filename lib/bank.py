"""
Банк вопросов: чтение Excel + учебного плана, разбор форматов, проверка.

Лист assessment: Q, Q_opt, A, ID, qid
- qid — постоянный идентификатор вопроса. На него ссылаются разметка, тесты и попытки,
  поэтому текст вопроса можно править, не теряя связей. Пустой qid у нового вопроса
  заполняется командой `python check_bank.py --fill`.

Форматы Q_opt / A:
- open:   Q_opt пусто, A — одно слово
- single: Q_opt «А) ...; Б) ...», A — одна буква
- multi:  Q_opt «А) ...; Б) ...», A — «Б; Г; Д»
- order:  Q_opt «1. ...; 2. ...», A — «2 → 1 → 3 → 4»
- match:  Q_opt «1. ...; 2. ... | А. ...; Б. ...», A — «1 — Б; 2 — А»
"""

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from lib.program import EXCEL_PATH, PLAN_PATH
SHEET = "assessment"

QTYPE_NAMES = {
    "single": "Один ответ",
    "multi": "Несколько ответов",
    "order": "Последовательность",
    "match": "Соответствие",
    "open": "Открытый ответ",
}

_LETTER_OPT = re.compile(r";\s*(?=[А-ЯЁA-Z]\)\s)")
_NUM_ITEM = re.compile(r";\s*(?=\d+\.\s)")
_LETTER_ITEM = re.compile(r";\s*(?=[А-ЯЁA-Z]\.\s)")
_QID_OK = re.compile(r"^[0-9a-f]{10}(-\d+)?$")  # как генерирует check_bank.py


@dataclass
class Question:
    qid: str
    group_id: str
    code: str
    discipline: str
    competence: str
    qtype: str
    text: str
    raw_opt: str
    raw_answer: str
    row: int = 0  # строка в Excel — для сообщений об ошибках
    options: list = field(default_factory=list)  # [(label, text)] для single/multi/order/match-left
    right: list = field(default_factory=list)  # [(label, text)] правая колонка match
    key: object = None  # set / list / dict / str
    semesters: list = field(default_factory=list)
    courses: list = field(default_factory=list)
    problems: list = field(default_factory=list)  # ошибки формата: вопрос не выдаётся в тренажёр и новые тесты

    @property
    def broken(self) -> bool:
        return bool(self.problems)

    @property
    def snapshot(self) -> dict:
        """Содержимое вопроса — с ним сравнивается разметка и попытки, чтобы заметить правку."""
        return {"Q": self.text, "Q_opt": self.raw_opt, "A": self.raw_answer}

    @property
    def fingerprint(self) -> str:
        return content_hash(self.text, self.raw_opt, self.raw_answer)


def make_qid(text: str, opt: str) -> str:
    """qid по умолчанию: хэш текста. Им же заполнены qid существующих вопросов."""
    return hashlib.sha1(f"{text}\n{opt}".encode("utf-8")).hexdigest()[:10]


def content_hash(text: str, opt: str, answer: str) -> str:
    return hashlib.sha1(f"{text}\n{opt}\n{answer}".encode("utf-8")).hexdigest()[:8]


def snapshot_fingerprint(snapshot) -> str:
    """Отпечаток сохранённого снимка (dict или JSON-строка). Пусто — снимка нет."""
    if not snapshot:
        return ""
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except ValueError:
            return ""
    return content_hash(snapshot.get("Q", ""), snapshot.get("Q_opt", ""), snapshot.get("A", ""))


def _split_labeled(s: str, splitter: re.Pattern, label_re: str) -> list:
    items = []
    for part in splitter.split(s.strip()):
        part = part.strip().rstrip(";").strip()
        m = re.match(label_re, part)
        if m:
            items.append((m.group(1), part[m.end():].strip()))
        else:
            items.append((str(len(items) + 1), part))
    return items


def _cell(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _check(q: Question) -> list:
    """Ошибки, из-за которых вопрос нельзя корректно показать или проверить."""
    p = []
    if not q.text:
        p.append("пустой текст вопроса (Q)")
    if not q.raw_answer:
        p.append("пустой ключ (A)")
    if q.qtype in ("single", "multi"):
        labels = [l for l, _ in q.options]
        if len(labels) < 2:
            p.append("меньше двух вариантов — проверьте формат «А) …; Б) …»")
        if len(set(labels)) != len(labels):
            p.append(f"повторяются буквы вариантов: {', '.join(labels)}")
        missing = sorted(q.key - set(labels))
        if missing:
            p.append(f"в ключе буквы, которых нет среди вариантов: {', '.join(missing)}")
    elif q.qtype == "order":
        labels = [l for l, _ in q.options]
        if sorted(q.key) != sorted(labels):
            p.append(f"ключ «{q.raw_answer}» не совпадает с номерами элементов {', '.join(labels)}")
    elif q.qtype == "match":
        left = {l for l, _ in q.options}
        right = {l for l, _ in q.right}
        if set(q.key) != left:
            p.append(f"в ключе не все пункты левого столбца ({', '.join(sorted(left))}) — формат «1 — Б; 2 — А»")
        bad = sorted(set(q.key.values()) - right)
        if bad:
            p.append(f"в ключе буквы, которых нет в правом столбце: {', '.join(bad)}")
    return p


def parse_question(row, disciplines: dict, plan: dict, excel_row: int = 0) -> Question:
    text, opt, ans, gid = _cell(row.get("Q")), _cell(row.get("Q_opt")), _cell(row.get("A")), _cell(row.get("ID"))
    code, _, comp = gid.partition("_")
    q = Question(
        qid=_cell(row.get("qid")) or make_qid(text, opt),
        group_id=gid,
        code=code,
        discipline=disciplines.get(code, code),
        competence=comp,
        qtype="open",
        text=text,
        raw_opt=opt,
        raw_answer=ans,
        row=excel_row,
        semesters=plan.get(code, {}).get("semesters", []),
        courses=plan.get(code, {}).get("courses", []),
    )

    if not opt:
        q.qtype, q.key = "open", ans
    elif "|" in opt:
        left, right = opt.split("|", 1)
        q.qtype = "match"
        q.options = _split_labeled(left, _NUM_ITEM, r"(\d+)\.\s*")
        q.right = _split_labeled(right, _LETTER_ITEM, r"([А-ЯЁA-Z])\.\s*")
        q.key = {n: l for n, l in re.findall(r"(\d+)\s*[—–-]\s*([А-ЯЁA-Z])", ans)}
    elif "→" in ans:
        q.qtype = "order"
        q.options = _split_labeled(opt, _NUM_ITEM, r"(\d+)\.\s*")
        q.key = [x.strip() for x in ans.split("→")]
    else:
        q.options = _split_labeled(opt, _LETTER_OPT, r"([А-ЯЁA-Z])\)\s*")
        letters = [x.strip() for x in ans.split(";") if x.strip()]
        q.qtype = "multi" if len(letters) > 1 else "single"
        q.key = set(letters)
    q.problems = _check(q)
    return q


def load_plan(path: Path = PLAN_PATH) -> dict:
    df = pd.read_csv(path)
    out = {}
    for code, g in df.groupby("discipline_code"):
        out[code] = {
            "semesters": sorted(int(s) for s in g["semester"].unique()),
            "courses": sorted(int(c) for c in g["grade_year"].unique()),
            "discipline": g["discipline"].iloc[0],
        }
    return out


def semester_courses(path: Path = PLAN_PATH) -> dict:
    """{семестр: курс} по учебному плану: 1, 2 → 1 курс; 3, 4 → 2 курс."""
    df = pd.read_csv(path)
    return {int(s): int(c) for s, c in df.groupby("semester")["grade_year"].first().items()}


def _read_with_header(excel_path: Path, sheet: str, first_column: str) -> pd.DataFrame:
    """Лист, у которого над заголовком может быть строка-подпись (в разных Excel по-разному)."""
    raw = pd.read_excel(excel_path, sheet_name=sheet, header=None, nrows=5)
    header = next((i for i, v in enumerate(raw.iloc[:, 0].astype(str)) if v.strip() == first_column), 0)
    return pd.read_excel(excel_path, sheet_name=sheet, header=header)


def read_assessment(excel_path: Path = EXCEL_PATH) -> pd.DataFrame:
    # qid читается как текст: иначе «1234e5678» станет числом
    df = pd.read_excel(excel_path, sheet_name=SHEET, dtype={"qid": str})
    if "qid" not in df.columns:
        df["qid"] = ""
    return df


def load_bank(excel_path: Path = EXCEL_PATH, plan_path: Path = PLAN_PATH) -> tuple:
    """Возвращает (вопросы, проблемы банка). Проблема: {row, qid, level, message}."""
    plan = load_plan(plan_path)
    disc = _read_with_header(excel_path, "disciplines", "discipline_code")
    disciplines = dict(zip(disc["discipline_code"].astype(str), disc["discipline"].astype(str)))
    for code, info in plan.items():
        disciplines.setdefault(code, info["discipline"])

    df = read_assessment(excel_path)
    questions, issues = [], []

    def issue(row, qid, level, message):
        issues.append({"row": row, "qid": qid, "level": level, "message": message})

    for i, r in df.iterrows():
        excel_row = i + 2
        if all(_cell(r.get(c)) == "" for c in ("Q", "Q_opt", "A", "ID")):
            continue  # пустая строка
        try:
            q = parse_question(r, disciplines, plan, excel_row)
        except Exception as e:  # строка не ломает приложение, а попадает в список проблем
            issue(excel_row, _cell(r.get("qid")), "error", f"не удалось разобрать: {e}")
            continue
        for p in q.problems:
            issue(excel_row, q.qid, "error", p)
        raw_qid = _cell(r.get("qid"))
        if not raw_qid:
            issue(excel_row, q.qid, "warning", "пустой qid — запустите `python check_bank.py --fill`")
        elif not _QID_OK.match(raw_qid):
            issue(excel_row, raw_qid, "error", "qid испорчен (похоже, Excel превратил его в число) — "
                                              "восстановите прежнее значение")
        if not q.group_id or "_" not in q.group_id:
            issue(excel_row, q.qid, "error", f"ID «{q.group_id}» не в формате «код_компетенция»")
        elif q.code not in plan:
            issue(excel_row, q.qid, "warning", f"дисциплины {q.code} нет в учебном плане — "
                                               "вопрос не найдётся по курсу и семестру")
        if q.qtype == "open" and len(q.raw_answer.split()) > 1:
            issue(excel_row, q.qid, "warning", "в ключе открытого вопроса несколько слов, "
                                               "а студенту подсказано «впишите одно слово»")
        questions.append(q)

    for qid, n in Counter(q.qid for q in questions).items():
        if n > 1:
            rows = [q.row for q in questions if q.qid == qid]
            for r in rows:
                issue(r, qid, "error", f"qid повторяется в строках {', '.join(map(str, rows))} — "
                                       "у скопированной строки очистите qid")
    for text, n in Counter(q.text for q in questions if q.text).items():
        if n > 1:
            rows = [q.row for q in questions if q.text == text]
            issue(rows[0], "", "warning", f"одинаковый текст вопроса в строках {', '.join(map(str, rows))}")

    # при дублях qid в банке остаётся первая строка, остальные исключаются
    seen, unique = set(), []
    for q in questions:
        if q.qid not in seen:
            seen.add(q.qid)
            unique.append(q)
    return unique, sorted(issues, key=lambda x: (x["level"] != "error", x["row"]))


def bank_frame(questions: list) -> pd.DataFrame:
    """Плоская таблица для фильтров и выгрузок."""
    return pd.DataFrame([{
        "qid": q.qid,
        "ID": q.group_id,
        "code": q.code,
        "discipline": q.discipline,
        "competence": q.competence,
        "qtype": q.qtype,
        "type_name": QTYPE_NAMES[q.qtype],
        "semesters": q.semesters,
        "courses": q.courses,
        "broken": q.broken,
        "Q": q.text,
        "Q_opt": q.raw_opt,
        "A": q.raw_answer,
    } for q in questions])


def filter_frame(df: pd.DataFrame, courses=None, semesters=None, codes=None,
                 competences=None, qtypes=None) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    if courses:
        mask &= df["courses"].map(lambda xs: bool(set(xs) & set(courses)))
    if semesters:
        mask &= df["semesters"].map(lambda xs: bool(set(xs) & set(semesters)))
    if codes:
        mask &= df["code"].isin(codes)
    if competences:
        mask &= df["competence"].isin(competences)
    if qtypes:
        mask &= df["qtype"].isin(qtypes)
    return df[mask]
