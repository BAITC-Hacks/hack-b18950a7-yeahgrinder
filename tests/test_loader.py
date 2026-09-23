from datetime import date

import pandas as pd

from engine.loader import discover, read_lines, signature


def test_loader_drops_total_row_and_keeps_returns_separate():
    df = pd.DataFrame({'Дата': ['01.06.2026 12:00:00', '02.06.2026 12:00:00', None],
                       'Номер': ['1', '2', None], 'Код': [' 001_ ', '001_', None],
                       'Количество': [10, -2, 8]})
    lines, returns = read_lines(df, 'IEK', date(2026, 9, 22))
    assert len(lines) == 1 and lines.iloc[0].sku == '001_'
    assert len(returns) == 1 and returns.iloc[0].qty == -2


def test_discovery_ignores_filename(tmp_path):
    pd.DataFrame({'Дата': [], 'Номер': [], 'Код': [], 'Количество': []}).to_excel(tmp_path/'broken_®™.xlsx', index=False)
    assert 'sales_lines' in discover(tmp_path)


def test_stock_vs_sales_signature():
    assert signature([['№', 'Номенклатура', 'Номенклатура.Код', 'Ед.изм', 'янв. 2024']]) == ('stock_monthly', 0)
    assert signature([['Номенклатура', 'Номенклатура.Код', 'Кратность', 'янв. 2024']]) == ('sales_monthly', 0)
