from copy import deepcopy
from datetime import date

import pandas as pd
import pytest

from engine.models import Params
from engine.run import compute


def row(result, supplier='IEK'):
    return result.orders[result.orders.supplier == supplier].iloc[0]


def test_transit_reduces_order(dataset):
    before = row(compute(dataset, Params()))
    dataset.transit = pd.DataFrame([{'supplier': 'IEK', 'sku': '001_', 'order_id': 'T1', 'qty': 500, 'eta': pd.Timestamp('2026-10-01')}])
    after = row(compute(dataset, Params()))
    assert abs((before.recommended_qty - after.recommended_qty) - 500) <= after.moq


def test_stockout_restores_demand(dataset):
    mask = (dataset.sales_monthly.supplier == 'IEK') & dataset.sales_monthly.month.between('2026-05-01', '2026-08-01')
    dataset.sales_monthly.loc[mask, 'qty_raw'] = 0.0
    dataset.stock_monthly.loc[mask, ['opening', 'closing']] = 0.0
    raw = row(compute(dataset, Params(restore_stockouts=False)))
    clean = row(compute(dataset, Params()))
    assert clean.base_monthly > raw.base_monthly
    assert clean.recommended_qty > raw.recommended_qty
    assert 'STOCKOUT_RESTORED' in clean.reason_codes


def test_one_off_spike_ignored(dataset):
    baseline = row(compute(dataset, Params()))
    mask = (dataset.sales_monthly.supplier == 'IEK') & dataset.sales_monthly.month.eq(pd.Timestamp('2026-06-01'))
    dataset.sales_monthly.loc[mask, 'qty_raw'] += 3000.0
    dataset.sales_lines = pd.concat([dataset.sales_lines, pd.DataFrame([{'supplier': 'IEK', 'sku': '001_',
        'date': pd.Timestamp('2026-06-09'), 'qty': 3000, 'doc_no': 'SPIKE'}])], ignore_index=True)
    result = row(compute(dataset, Params()))
    assert abs(result.recommended_qty - baseline.recommended_qty) <= baseline.recommended_qty * 0.1
    assert 'ONE_OFF_INVOICE' in result.reason_codes
    assert result.excluded_events[0]['doc_no'] == 'SPIKE'


def test_seasonality_peak(dataset):
    flat = row(compute(dataset, Params()))
    dataset.seasonality.loc[(dataset.seasonality.supplier == 'IEK') & (dataset.seasonality.month_num == 10), 'coef'] = 1.24
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
    dataset.sales_monthly.loc[dataset.sales_monthly.is_partial, 'qty_raw'] = 1000000.0
    assert compute(dataset).orders.recommended_qty.tolist() == before


def test_transit_after_horizon_is_not_deducted(dataset):
    before = row(compute(dataset))
    dataset.transit = pd.DataFrame([{'supplier': 'IEK', 'sku': '001_', 'order_id': 'late', 'qty': 9999, 'eta': pd.Timestamp('2027-01-01')}])
    after = row(compute(dataset))
    assert after.recommended_qty == before.recommended_qty
    assert after.in_transit_later == 9999


def test_sustained_growth_is_not_cleaned(dataset):
    dataset.sales_monthly.loc[dataset.sales_monthly.month.between('2026-06-01', '2026-08-01'), 'qty_raw'] = 1500.0
    result = row(compute(dataset))
    assert not result.excluded_events
    assert result.base_monthly > 300


def test_no_demand_order_zero_even_with_manager_rule(dataset):
    dataset.sales_monthly['qty_raw'] = 0.0
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
    # Mostly zero months with a few regular sales: none of them is a one-off spike.
    dataset.sales_monthly['qty_raw'] = 0.0
    sales = dataset.sales_monthly.month.isin(pd.to_datetime(['2025-11-01', '2026-02-01', '2026-05-01', '2026-08-01']))
    dataset.sales_monthly.loc[sales, 'qty_raw'] = 25.0
    result = row(compute(dataset))
    assert not result.excluded_events
    assert result.base_monthly > 0
