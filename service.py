"""Shared application service for direct Streamlit calls and the HTTP API."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import pandas as pd

from agent.memory import Memory, json_dump
from engine.loader import Dataset, TABLES, load
from engine.models import ROOT, OrderLine, Params
from engine.run import Result, compute
from export.excel import export_xlsx

JSON_COLUMNS = ['reason_codes', 'flags', 'excluded_events', 'restored_events', 'forecast_months']


class ProcurementService:
    def __init__(self, paths=None, storage=None, dataset=None):
        self.paths = paths
        self.storage = Path(storage or ROOT)
        self.runs = self.storage / 'runs'
        self.runs.mkdir(parents=True, exist_ok=True)
        self.memory = Memory(self.storage / 'data/procurement.sqlite')
        self.dataset = dataset

    def data(self, params):
        if self.dataset is not None:
            if self.dataset.as_of != params.as_of:
                raise ValueError("Дата тестового снимка отличается от параметров")
            return self.dataset
        return load(paths=self.paths, as_of=params.as_of)

    def calculate(self, params=None, user_id='local'):
        if params is None:
            settings = Params.from_yaml().model_dump(mode='json')
            settings.update(self.memory.preferences(user_id))
            params = Params.model_validate(settings)
        ds = self.data(params)
        result = compute(ds, params, self.memory.rules(user_id))
        run_id = uuid4().hex
        folder = self.runs / run_id
        folder.mkdir()
        # Immutable input snapshot: what-if and audit never read newer Excel files.
        for name, frame in ds.tables().items():
            frame.to_parquet(folder / f'{name}.parquet', index=False)
        frame = result.orders.copy()
        for col in JSON_COLUMNS:
            frame[col] = frame[col].map(json_dump)
        frame.to_parquet(folder / 'orders.parquet', index=False)
        result.history.to_parquet(folder / 'history.parquet', index=False)
        params_json = params.model_dump(mode='json')
        config_hash = sha256(json.dumps(params_json, sort_keys=True).encode()).hexdigest()
        self.memory.add_run(run_id, params_json, config_hash, ds.fingerprint, result.warnings)
        result.run_id = run_id
        return result

    def get_result(self, run_id):
        metadata = self.memory.get_run(run_id)  # Validate opaque ID before constructing a path.
        folder = self.runs / metadata['run_id']
        orders = pd.read_parquet(folder / 'orders.parquet')
        for col in JSON_COLUMNS:
            orders[col] = orders[col].map(json.loads)
        history = pd.read_parquet(folder / 'history.parquet')
        return Result(orders, history, Params.model_validate(metadata['params']), metadata['warnings'], run_id)

    def snapshot(self, run_id):
        metadata = self.memory.get_run(run_id)
        folder = self.runs / metadata['run_id']
        tables = {name: pd.read_parquet(folder / f'{name}.parquet') for name in TABLES}
        tables['sku_master']['flags'] = tables['sku_master']['flags'].map(list)
        return Dataset(**tables, as_of=Params.model_validate(metadata['params']).as_of,
                       fingerprint=metadata['data_hash'], warnings=metadata['warnings'])

    def decide(self, run_id, supplier, decision, edits=None, reason='', user_id='local'):
        if decision not in {'approve', 'edit', 'reject'}:
            raise ValueError("Неизвестное решение")
        result = self.get_result(run_id)
        part = result.orders[result.orders.supplier == supplier]
        if part.empty:
            raise ValueError("В расчёте нет этого поставщика")
        edits = edits or {}
        if set(edits) - set(part.sku):
            raise ValueError("Правка относится к неизвестному SKU")
        rows, changes = [], {}
        for row in part.to_dict('records'):
            row = {k: None if isinstance(v, float) and v != v else v for k, v in row.items()}
            original = row['recommended_qty']
            if row['sku'] in edits:
                qty = edits[row['sku']]
                if type(qty) is not int or qty < 0 or qty % row['moq']:
                    raise ValueError(f"{row['sku']}: количество должно быть целым, ≥0 и кратно {row['moq']}")
                if qty != original:
                    row['recommended_qty'] = qty
                    row['reason_codes'].append('MANAGER_EDIT')
                    row['reason_text'] += f" Менеджер изменил заказ: {original} → {qty}. {reason}".rstrip()
                    changes[row['sku']] = {'recommended': original, 'approved': qty, 'delta': qty - original}
            rows.append(OrderLine.model_validate(row).model_dump())
        if decision == 'reject' and not reason.strip():
            raise ValueError("Укажите причину отклонения")
        return self.memory.add_decision(run_id, supplier, user_id, decision, reason, changes, rows)

    def export(self, approval_id):
        approval = self.memory.get_decision(approval_id)
        if approval['decision'] != 'approve':
            raise ValueError("Экспорт доступен только после утверждения менеджером")
        run = self.memory.get_run(approval['run_id'])
        return export_xlsx(approval['rows'], run['params'], run['warnings'], approval_id)

    def save_rule(self, run_id, supplier, sku, min_qty, user_id='local'):
        result = self.get_result(run_id)
        row = result.orders[(result.orders.supplier == supplier) & (result.orders.sku == sku)]
        if row.empty:
            raise ValueError("SKU не найден")
        if type(min_qty) is not int or min_qty < 0 or min_qty % int(row.iloc[0].moq):
            raise ValueError("Правило должно задавать целое количество, кратное MOQ")
        self.memory.save_rule(user_id, supplier, sku, min_qty)

    def what_if(self, run_id, supplier=None, sku=None, lead_time_days=None, review_days=None, service_z=None, extra_transit=None):
        base = self.get_result(run_id)
        ds = self.snapshot(run_id)
        settings = base.params.model_dump()
        if supplier:
            settings['suppliers'] = [supplier]
        if lead_time_days is not None:
            for s in ([supplier] if supplier else settings['lead_time_days']):
                settings['lead_time_days'][s] = lead_time_days
        for name, value in [('review_days', review_days), ('service_z', service_z)]:
            if value is not None:
                settings[name] = value
        params = Params.model_validate(settings)
        targets = ds.sku_master
        if supplier:
            targets = targets[targets.supplier == supplier]
        if sku:
            targets = targets[targets.sku == sku]
        if targets.empty:
            raise ValueError("Товар/поставщик не найден")
        if sku:
            ds.sku_master = targets.copy()
        if extra_transit is not None:
            if type(extra_transit) is not int or extra_transit < 0:
                raise ValueError("Дополнительный транзит должен быть целым и ≥0")
            if not sku or len(targets) != 1:
                raise ValueError("Для добавления транзита укажите однозначный SKU и поставщика")
            item = targets.iloc[0]
            extra = pd.DataFrame([{'supplier': item.supplier, 'sku': item.sku, 'order_id': 'what-if',
                                    'qty': extra_transit, 'eta': pd.Timestamp(params.as_of) + pd.Timedelta(days=1)}])
            ds.transit = pd.concat([ds.transit, extra], ignore_index=True)
        # Preserve effective manager rules from the original run, not current memory.
        rules = {(r.supplier, r.sku): int(r.recommended_qty) for r in base.orders.itertuples()
                 if 'MANAGER_RULE' in r.reason_codes}
        candidate = compute(ds, params, rules)
        diff = base.orders[['supplier', 'sku', 'recommended_qty']].merge(
            candidate.orders[['supplier', 'sku', 'recommended_qty']], on=['supplier', 'sku'], suffixes=('_before', '_after'))
        diff['delta'] = diff.recommended_qty_after - diff.recommended_qty_before
        return {'saved': False, 'summary': candidate.summary, 'changes': diff.to_dict('records')}
