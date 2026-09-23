from datetime import date

import pandas as pd
import pytest

from engine.load import Dataset

AS_OF = pd.Timestamp('2026-09-22')
SKUS = {'IEK': '001_', 'Systeme Electric': '002_'}      # коды 1С у поставщиков не пересекаются
DATA_NAME = {'IEK': 'ИЭК', 'Systeme Electric': 'Systeme Electric'}


@pytest.fixture
def dataset():
    """Два товара по 300 шт./мес. с 01.2024, остаток на складе 0 — как в спеке Алдияра,
    но в формате engine/load.py: 2024 — месячный отчёт, 2025+ — строки накладных."""
    months = pd.date_range('2024-01-01', '2026-09-01', freq='MS')
    items = pd.DataFrame([{'code': sku, 'name': 'Тестовый товар', 'unit': 'шт', 'supplier': DATA_NAME[s],
                           'moq': 10.0, 'moq_known': True, 'article': 'A1', 'unit_cost': None,
                           'category': '', 'category_source': 'test', 'group': '001', 'group_name': 'Тестовый'}
                          for s, sku in SKUS.items()])
    lines = pd.DataFrame([{'date': m + pd.Timedelta(days=4), 'doc': f'D{m:%Y%m}', 'code': sku, 'qty': 300.0}
                          for sku in SKUS.values() for m in months if m.year >= 2025])
    monthly = pd.DataFrame([{'code': sku, 'month': m, 'qty': 300.0}
                            for sku in SKUS.values() for m in months if m.year == 2024])
    stock = pd.DataFrame([{'code': sku, 'month': m, 'stock': 1000.0} for sku in SKUS.values() for m in months])
    transit = pd.DataFrame({'code': pd.Series(dtype=str), 'doc': pd.Series(dtype=str), 'qty': pd.Series(dtype=float),
                            'order_date': pd.Series(dtype='datetime64[ns]'),
                            'arrival_date': pd.Series(dtype='datetime64[ns]')})
    flat = pd.Series(1.0, index=range(1, 13))
    return Dataset(lines=lines, items=items, monthly_2024=monthly, stock=stock,
                   stock_current=pd.Series(0.0, index=list(SKUS.values())), transit=transit,
                   lead_default={'ИЭК': (30, 'допущение'), 'Systeme Electric': (45, 'допущение')},
                   supplier_season={'ИЭК': flat, 'Systeme Electric': flat.copy()}, as_of=AS_OF)


def set_lines(ds, sku, months, qty):
    """Заменить продажи товара в указанных месяцах (строки накладных) на qty (0 — продаж нет)."""
    months = pd.to_datetime(list(months))
    in_months = ds.lines['date'].dt.to_period('M').dt.to_timestamp().isin(months)
    ds.lines = ds.lines[~((ds.lines['code'] == sku) & in_months)]
    if qty:
        extra = pd.DataFrame([{'date': m + pd.Timedelta(days=4), 'doc': f'X{m:%Y%m}', 'code': sku, 'qty': float(qty)}
                              for m in months])
        ds.lines = pd.concat([ds.lines, extra], ignore_index=True)
