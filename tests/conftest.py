from datetime import date

import pandas as pd
import pytest

from engine.load import Dataset

AS_OF = pd.Timestamp('2026-09-22')
SKUS = {'IEK': '001_', 'Systeme Electric': '002_'}      # коды 1С у поставщиков не пересекаются
DATA_NAME = {'IEK': 'ИЭК', 'Systeme Electric': 'Systeme Electric'}


@pytest.fixture(autouse=True)
def no_real_langfuse(monkeypatch):
    # Real keys from .env must not send test runs to the Langfuse project.
    for name in ('LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY'):
        monkeypatch.delenv(name, raising=False)


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


def pytest_addoption(parser):
    group = parser.getgroup('supplier archives')
    group.addoption('--iek-zip', help='Path to the IEK XLSX archive')
    group.addoption('--se-zip', help='Path to the Systeme Electric XLSX archive')


def pytest_configure(config):
    if bool(config.getoption('--iek-zip')) != bool(config.getoption('--se-zip')):
        raise pytest.UsageError('Для проверки реальных данных нужны оба параметра: --iek-zip и --se-zip')


@pytest.fixture(autouse=True)
def offline_ai(monkeypatch):
    """Tests must not call paid LLMs or tracing even if a teammate has a local .env."""
    for key in ('OPENAI_API_KEY', 'NVIDIA_API_KEY', 'LANGSMITH_API_KEY'):
        monkeypatch.setenv(key, '')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')


@pytest.fixture(scope='session')
def partner_inputs(request, tmp_path_factory):
    from pathlib import Path
    from scripts.fixture_archives import extract_suppliers
    paths = [request.config.getoption('--iek-zip'), request.config.getoption('--se-zip')]
    if not all(paths):
        pytest.skip('Реальные ZIP не заданы: используйте --iek-zip и --se-zip (docs/TESTING.md).')
    root = tmp_path_factory.mktemp('partner-inputs')
    try:
        extracted = extract_suppliers(*(Path(p).expanduser().resolve() for p in paths), root)
    except (ValueError, OSError) as error:
        pytest.fail(str(error))
    return root, extracted


@pytest.fixture(scope='session')
def partner_dataset(partner_inputs):
    from engine.load import load
    return load(partner_inputs[0], use_cache=False)


@pytest.fixture(scope='session')
def partner_result(partner_dataset):
    from engine.models import Params
    from engine.run import compute
    return compute(partner_dataset, Params())
