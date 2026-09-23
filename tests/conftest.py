from datetime import date

import pandas as pd
import pytest

from engine.loader import Dataset, TABLES


@pytest.fixture
def dataset():
    months = pd.date_range('2024-01-01', '2026-09-01', freq='MS')
    master = pd.DataFrame([{'supplier': s, 'sku': '001_', 'name': 'Тестовый товар', 'article': 'A1',
                           'unit': 'шт', 'moq': 10, 'moq_source': 'test', 'flags': [], 'group': '001'}
                          for s in ['IEK', 'Systeme Electric']])
    sales = pd.DataFrame([{'supplier': s, 'sku': '001_', 'month': m, 'qty_raw': 300.0, 'is_partial': m == months[-1]}
                          for s in master.supplier for m in months])
    stock = sales[['supplier', 'sku', 'month']].copy().assign(opening=1000.0, closing=1000.0)
    current = master[['supplier', 'sku']].copy().assign(free_stock=0.0, reserved=0.0, source='test', as_of=pd.Timestamp('2026-09-22'))
    season = pd.DataFrame([{'supplier': s, 'month_num': m, 'coef': 1.0} for s in master.supplier for m in range(1, 13)])
    lines = pd.DataFrame([{'supplier': s, 'sku': '001_', 'date': m + pd.Timedelta(days=4), 'doc_no': f'D{m:%Y%m}', 'qty': 300.0}
                         for s in master.supplier for m in months])
    return Dataset(master, sales, lines, pd.DataFrame(columns=TABLES['returns']), stock, current,
                   pd.DataFrame(columns=TABLES['transit']), season, date(2026, 9, 22))
