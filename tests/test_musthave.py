"""Проверки must-have кейса на расчёте engine/calc.py через мост engine/run.py.

Спрос в движке — из строк накладных (источник истины по спеке), поэтому сценарии меняют
накладные, а не месячный отчёт.
"""
import pandas as pd

from engine.models import Params
from engine.run import compute
from tests.conftest import set_lines


def row(result, supplier='IEK'):
    return result.orders[result.orders.supplier == supplier].iloc[0]


def test_transit_reduces_order(dataset):
    before = row(compute(dataset, Params()))
    dataset.transit = pd.DataFrame([{'code': '001_', 'doc': 'T1', 'qty': 500.0, 'order_date': pd.NaT,
                                     'arrival_date': pd.Timestamp('2026-10-01')}])
    after = row(compute(dataset, Params()))
    assert abs((before.recommended_qty - after.recommended_qty) - 500) <= after.moq


def test_stock_changes_order(dataset):
    before = row(compute(dataset, Params()))
    dataset.stock_current['001_'] = 400.0
    after = row(compute(dataset, Params()))
    assert abs((before.recommended_qty - after.recommended_qty) - 400) <= after.moq


def test_growth_plan_changes_order(dataset):
    base = row(compute(dataset, Params()))
    grown = row(compute(dataset, Params(growth_pct=20)))
    assert abs(grown.forecast_horizon / base.forecast_horizon - 1.2) < 1e-6
    assert grown.recommended_qty > base.recommended_qty


def test_category_changes_order(dataset):
    dataset.items.loc[dataset.items.code == '001_', 'category'] = '3'
    low = row(compute(dataset, Params()))
    dataset.items.loc[dataset.items.code == '001_', 'category'] = '1'
    high = row(compute(dataset, Params()))
    assert high.safety_stock >= low.safety_stock and high.recommended_qty >= low.recommended_qty
    dataset.items.loc[dataset.items.code == '001_', 'category'] = '7'
    no_auto = row(compute(dataset, Params()))
    assert no_auto.recommended_qty == 0 and 'CATEGORY_NO_AUTO' in no_auto.reason_codes


def test_stockout_restores_demand(dataset):
    months = pd.date_range('2026-05-01', '2026-08-01', freq='MS')
    set_lines(dataset, '001_', months, 0)
    dataset.stock.loc[(dataset.stock.code == '001_') & dataset.stock.month.isin(months), 'stock'] = 0.0
    raw = row(compute(dataset, Params(restore_stockouts=False)))
    clean = row(compute(dataset, Params()))
    assert clean.base_monthly > raw.base_monthly
    assert clean.recommended_qty > raw.recommended_qty
    assert 'STOCKOUT_RESTORED' in clean.reason_codes


def test_one_off_spike_ignored(dataset):
    baseline = row(compute(dataset, Params()))
    dataset.lines = pd.concat([dataset.lines, pd.DataFrame([{'date': pd.Timestamp('2026-06-09'), 'doc': 'SPIKE',
                                                              'code': '001_', 'qty': 3000.0}])], ignore_index=True)
    result = row(compute(dataset, Params()))
    assert abs(result.recommended_qty - baseline.recommended_qty) <= baseline.recommended_qty * 0.1
    assert 'ONE_OFF_INVOICE' in result.reason_codes
    assert result.excluded_events[0]['doc_no'] == 'SPIKE'


def test_regular_bulk_buyer_is_not_one_off(dataset):
    # крупные строки каждый месяц (как коробка SE) — это обычный спрос, не выброс
    for m in pd.date_range('2025-09-01', '2026-08-01', freq='MS')[::3]:
        dataset.lines = pd.concat([dataset.lines, pd.DataFrame([{'date': m + pd.Timedelta(days=10), 'doc': f'B{m:%m}',
                                                                  'code': '001_', 'qty': 3000.0}])], ignore_index=True)
    result = row(compute(dataset, Params()))
    assert not result.excluded_events


def test_seasonality_peak(dataset):
    flat = row(compute(dataset, Params()))
    dataset.monthly_2024.loc[(dataset.monthly_2024.code == '001_') & (dataset.monthly_2024.month.dt.month == 10), 'qty'] = 450.0
    set_lines(dataset, '001_', ['2025-10-01'], 450)
    result = row(compute(dataset, Params()))
    october = next(m for m in result.forecast_months if m['month'] == '2026-10')
    assert october['forecast'] / result.base_monthly > 1.2
    assert result.forecast_horizon > flat.forecast_horizon


def test_every_line_explained_and_grouped(dataset):
    result = compute(dataset)
    assert set(result.orders.supplier) == {'IEK', 'Systeme Electric'}
    assert result.orders.reason_text.str.len().min() > 0
    assert ((result.orders.recommended_qty % result.orders.moq) == 0).all()
    assert len(result.summary) == 2


def test_partial_month_excluded(dataset):
    before = compute(dataset).orders.recommended_qty.tolist()
    set_lines(dataset, '001_', ['2026-09-01'], 1_000_000)
    assert compute(dataset).orders.recommended_qty.tolist() == before


def test_transit_after_horizon_is_not_deducted(dataset):
    before = row(compute(dataset))
    dataset.transit = pd.DataFrame([{'code': '001_', 'doc': 'late', 'qty': 9999.0, 'order_date': pd.NaT,
                                     'arrival_date': pd.Timestamp('2027-01-01')}])
    after = row(compute(dataset))
    assert after.recommended_qty == before.recommended_qty
    assert after.in_transit_later == 9999


def test_sustained_growth_is_not_cleaned(dataset):
    set_lines(dataset, '001_', pd.date_range('2026-06-01', '2026-08-01', freq='MS'), 1500)
    result = row(compute(dataset))
    assert not result.excluded_events
    assert result.base_monthly > 300


def test_no_demand_order_zero_even_with_manager_rule(dataset):
    dataset.lines = dataset.lines[dataset.lines.code != '001_']
    dataset.monthly_2024['qty'] = 0.0
    result = row(compute(dataset, rules={('IEK', '001_'): 1000}))
    assert result.recommended_qty == 0
    assert 'NO_DEMAND' in result.reason_codes


def test_horizon_counts_exact_days(dataset):
    r = row(compute(dataset, Params(lead_time_days={'IEK': 45, 'Systeme Electric': 45})))
    assert sum(m['days'] for m in r.forecast_months) == 75
    assert [m['month'] for m in r.forecast_months] == ['2026-09', '2026-10', '2026-11', '2026-12']


def test_empty_selection(dataset):
    result = compute(dataset, Params(groups=['unknown']))
    assert result.orders.empty
    assert result.summary == []


def test_sparse_sku_not_zeroed(dataset):
    # Редкие регулярные продажи — не выбросы и не «нет спроса».
    dataset.lines = dataset.lines[dataset.lines.code != '001_']
    dataset.monthly_2024['qty'] = 0.0
    set_lines(dataset, '001_', ['2025-11-01', '2026-02-01', '2026-05-01', '2026-08-01'], 25)
    result = row(compute(dataset))
    assert not result.excluded_events
    assert result.base_monthly > 0
