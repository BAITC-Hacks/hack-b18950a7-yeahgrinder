"""Local durable run history, preferences, manager rules and decision audit."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from engine.models import ROOT


def json_dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)


class Memory:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / "data/procurement.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, params TEXT NOT NULL,
                    config_hash TEXT NOT NULL, data_hash TEXT NOT NULL, warnings TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS preferences (
                    user_id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS overrides (
                    user_id TEXT NOT NULL, supplier TEXT NOT NULL, sku TEXT NOT NULL,
                    min_qty INTEGER NOT NULL CHECK(min_qty >= 0),
                    PRIMARY KEY(user_id, supplier, sku));
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
                    supplier TEXT NOT NULL, user_id TEXT NOT NULL, decision TEXT NOT NULL,
                    reason TEXT NOT NULL, changes TEXT NOT NULL, rows_json TEXT NOT NULL,
                    created_at TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_run(self, run_id, params, config_hash, data_hash, warnings):
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", (run_id, self.now(), json_dump(params), config_hash, data_hash, json_dump(warnings)))

    def get_run(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError("Расчёт не найден")
        result = dict(row)
        result['params'], result['warnings'] = json.loads(result['params']), json.loads(result['warnings'])
        return result

    def list_runs(self, limit=30):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT run_id,created_at,config_hash,data_hash FROM runs ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 100),))]

    def preferences(self, user_id):
        with self.connect() as db:
            row = db.execute("SELECT value FROM preferences WHERE user_id=?", (user_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_preferences(self, user_id, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO preferences VALUES(?,?)", (user_id, json_dump(value)))

    def rules(self, user_id):
        with self.connect() as db:
            return {(r['supplier'], r['sku']): r['min_qty'] for r in db.execute("SELECT * FROM overrides WHERE user_id=?", (user_id,))}

    def save_rule(self, user_id, supplier, sku, min_qty):
        if type(min_qty) is not int or min_qty < 0:
            raise ValueError("Минимум должен быть целым неотрицательным числом")
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO overrides VALUES(?,?,?,?)", (user_id, supplier, sku, min_qty))

    def add_decision(self, run_id, supplier, user_id, decision, reason, changes, rows):
        approval_id = uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?)",
                       (approval_id, run_id, supplier, user_id, decision, reason, json_dump(changes), json_dump(rows), self.now()))
        return approval_id

    def get_decision(self, approval_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError("Решение не найдено")
        result = dict(row)
        result['rows'] = json.loads(result.pop('rows_json'))
        result['changes'] = json.loads(result['changes'])
        return result

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()
