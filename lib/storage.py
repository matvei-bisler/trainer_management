"""
Хранилище: Google Sheets через Apps Script (если задан [apps_script] в секретах)
или локальные CSV в ./data.

Листы / файлы:
- reviews  — разметка вопросов экспертом (светофор); snapshot — вопрос на момент оценки
- tests    — сформированные тесты
- attempts — попытки прохождения тестов
"""

import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from lib.program import TABLE_PREFIX

SCHEMAS = {
    "reviews": ["qid", "group_id", "status", "parts", "fragment", "comment", "suggestion", "updated_at",
                "snapshot"],
    "tests": ["test_id", "title", "description", "created_at", "question_ids", "shuffle",
              "scoring", "show_review", "active", "versions"],
    "attempts": ["attempt_id", "test_id", "submitted_at", "fio", "direction", "score", "max_score",
                 "percent", "duration_sec", "details"],
}

KEY_COLUMN = {"reviews": "qid", "tests": "test_id", "attempts": "attempt_id"}

LOCAL_DIR = Path(__file__).resolve().parent.parent / "data"

SCRIPT_VERSION = 2  # версия apps_script/Code.gs, под которую написано приложение


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize(df: pd.DataFrame, table: str) -> pd.DataFrame:
    """Недостающие столбцы (лист создан старой версией) — пустыми; лишние сохраняются."""
    for c in SCHEMAS[table]:
        if c not in df.columns:
            df[c] = ""
    return df


class LocalStorage:
    name = "локальные CSV"

    def __init__(self, folder: Path = LOCAL_DIR):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, table: str) -> Path:
        return self.folder / f"{table}.csv"

    def read(self, table: str) -> pd.DataFrame:
        p = self._path(table)
        if not p.exists():
            return pd.DataFrame(columns=SCHEMAS[table])
        return _normalize(pd.read_csv(p, dtype=str, keep_default_na=False), table)

    def append(self, table: str, row: dict) -> None:
        df = pd.concat([self.read(table), pd.DataFrame([row])], ignore_index=True)
        df[SCHEMAS[table]].to_csv(self._path(table), index=False)

    def upsert(self, table: str, row: dict) -> None:
        key = KEY_COLUMN[table]
        df = self.read(table)
        df = df[df[key] != str(row[key])]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df[SCHEMAS[table]].to_csv(self._path(table), index=False)


class AppsScriptStorage:
    """Google-таблица через веб-приложение Apps Script (apps_script/Code.gs)."""

    name = "Google Sheets"

    def __init__(self, url: str, token: str):
        self.url, self.token = url, token
        self.version = None  # версия скрипта из последнего ответа

    @property
    def outdated(self) -> bool:
        """Скрипт в таблице старее приложения: новые поля (например, snapshot) не сохранятся."""
        return self.version is not None and self.version < SCRIPT_VERSION

    def _call(self, action: str, table: str, row: dict = None, retries: int = 3) -> dict:
        """
        Apps Script отвечает редиректом 302 на googleusercontent.com, и этот второй шаг
        у Google нестабилен: бывает 404 или чужой ответ. Поэтому ответ проверяется,
        а при сбое запрос повторяется. Повтор безопасен: все записи — upsert по ключу.
        """
        # префикс: несколько тренажёров в одной таблице, у каждого свои листы
        payload = {"token": self.token, "action": action, "table": TABLE_PREFIX + table,
                   "key": KEY_COLUMN.get(table), "row": row}
        problem = ""
        for attempt in range(retries + 1):
            if attempt:
                time.sleep(min(2 ** attempt, 8))
            try:
                r = requests.post(self.url, json=payload, timeout=40)
            except requests.RequestException as e:
                problem = f"нет связи ({type(e).__name__})"
                continue
            if r.status_code != 200:
                problem = f"HTTP {r.status_code}"
                continue
            try:
                data = r.json()
            except ValueError:
                problem = ("Apps Script вернул не JSON: проверьте, что адрес заканчивается на /exec "
                           "и в развёртывании выбран доступ «Все»")
                continue
            if not data.get("ok"):
                # ошибка самого скрипта (токен, имя листа) повтором не лечится
                raise RuntimeError(f"Apps Script: {data.get('error')}")
            expected = "rows" in data if action == "read" else ("rows" not in data and "service" not in data)
            if expected:
                self.version = int(data.get("version") or 1)
                return data
            problem = "Google вернул ответ на другой запрос"
        raise RuntimeError(f"Хранилище недоступно: {problem}")

    def read(self, table: str) -> pd.DataFrame:
        rows = self._call("read", table)["rows"]
        if len(rows) < 2:
            return pd.DataFrame(columns=SCHEMAS[table])
        return _normalize(pd.DataFrame(rows[1:], columns=rows[0]), table)

    def _row(self, table: str, row: dict) -> dict:
        return {c: "" if row.get(c) is None else str(row.get(c)) for c in SCHEMAS[table]}

    # у каждой строки уникальный ключ, поэтому и «добавление» идёт через upsert:
    # повтор после сбоя не создаст дубль
    def append(self, table: str, row: dict) -> None:
        self.upsert(table, row)

    def upsert(self, table: str, row: dict) -> None:
        self._call("upsert", table, self._row(table, row))


@st.cache_resource
def get_storage():
    try:
        cfg = st.secrets.get("apps_script")
    except Exception:
        cfg = None
    if cfg and cfg.get("url") and cfg.get("token"):
        return AppsScriptStorage(cfg["url"], cfg["token"])
    return LocalStorage()


@st.cache_data(ttl=60, show_spinner=False)
def read_table(table: str) -> pd.DataFrame:
    return get_storage().read(table)


def write(table: str, row: dict, mode: str = "append") -> None:
    storage = get_storage()
    (storage.upsert if mode == "upsert" else storage.append)(table, row)
    read_table.clear()


@st.cache_resource
def _writer() -> ThreadPoolExecutor:
    # один поток: фоновые записи уходят в таблицу строго по очереди
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="sheets-writer")


def write_background(table: str, row: dict) -> Future:
    """Upsert в фоне: интерфейс не ждёт Google. Результат — в Future."""
    storage = get_storage()
    future = _writer().submit(storage.upsert, table, row)
    future.add_done_callback(lambda _: read_table.clear())
    return future
