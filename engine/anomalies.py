"""Stockout restoration and robust monthly winsorization with invoice attribution."""
import numpy as np
import pandas as pd


def clean_series(history, lines, params):
    h = history.copy()
    full = ~h.is_partial
    d = h.qty / h.season
    med = float(d[full].median()) if full.any() else 0.0
    # If stockouts dominate the calendar, estimate the baseline from months with stock.
    stocked = full & (h.opening > 0) & (h.closing > 0)
    if med == 0 and stocked.any():
        med = float(d[stocked].median())
    mask = pd.Series(False, index=h.index)
    for _ in range(2):
        base = med * h.season
        low = (h.opening <= 0) | (h.closing <= 0) | (h.opening < params.stockout.low_stock_ratio * base) | (h.closing < params.stockout.low_stock_ratio * base)
        mask = full & low & (h.qty < params.stockout.sales_drop_ratio * base)
        fit = d[full & ~mask]
        if len(fit):
            med = float(fit.median())
    fit = d[full & ~mask]
    # Sparse demand (mostly zero months): the zero median would turn every sale into a spike,
    # so the spike baseline is taken from months that actually had sales.
    base_fit = fit
    if len(fit) and (fit == 0).mean() > 0.5:
        base_fit = fit[fit > 0] if (fit > 0).sum() >= 3 else fit.iloc[:0]
    spike_med = float(base_fit.median()) if len(base_fit) else 0.0
    mad = float(1.4826 * (base_fit - spike_med).abs().median()) if len(base_fit) else 0.0
    ceiling = spike_med + params.outlier.k_mad * mad
    candidate = (full & ~mask & (d > ceiling) & (d > params.outlier.min_ratio * spike_med)
                 & (h.qty >= params.outlier.min_abs) & (spike_med > 0))
    # Three consecutive exceptional months are sustained growth, not one-off demand.
    run = candidate.ne(candidate.shift()).cumsum()
    sustained = candidate & candidate.groupby(run).transform("sum").ge(3)
    spikes = candidate & ~sustained & params.clean_outliers
    h["clean_qty"] = h.qty.copy()
    h.loc[spikes, "clean_qty"] = ceiling * h.loc[spikes, "season"]
    h["excluded_qty"] = (h.qty - h.clean_qty).clip(lower=0)
    h["restored_qty"] = 0.0
    if params.restore_stockouts and mask.any():
        h.loc[mask, "clean_qty"] = np.maximum(h.loc[mask, "qty"], med * h.loc[mask, "season"])
        h.loc[mask, "restored_qty"] = h.loc[mask, "clean_qty"] - h.loc[mask, "qty"]
    h["stockout"] = mask
    h["spike"] = spikes
    h["sustained_growth"] = sustained
    events = []
    invoice = lines.copy()
    invoice["month"] = pd.to_datetime(invoice.date).dt.to_period("M").dt.to_timestamp()
    for month, row in h[spikes].iterrows():
        part = invoice[invoice.month == month]
        largest = part.loc[part.qty.idxmax()] if len(part) else None
        attributed = largest is not None and largest.qty >= params.outlier.line_share * row.qty
        events.append({"month": month.strftime("%Y-%m"), "qty": float(row.excluded_qty),
                       "raw_qty": float(row.qty), "clean_qty": float(row.clean_qty),
                       "type": "ONE_OFF_INVOICE" if attributed else "STAT_SPIKE",
                       "doc_no": str(largest.doc_no) if attributed else None,
                       "date": largest.date.strftime("%Y-%m-%d") if attributed else None,
                       "invoice_qty": float(largest.qty) if attributed else None})
    restored = [{"month": m.strftime("%Y-%m"), "raw_qty": float(r.qty),
                 "clean_qty": float(r.clean_qty), "qty": float(r.restored_qty)}
                for m, r in h[h.restored_qty > 0].iterrows()]
    return h, events, restored
