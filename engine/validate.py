import pandas as pd

from engine.models import OrderLine


def validate_orders(orders):
    errors = []
    if orders.duplicated(["supplier", "sku"]).any():
        errors.append("Дубли supplier + sku")
    for row in orders.to_dict("records"):
        # A DataFrame stores None in float columns as NaN; restore the contract's None.
        row = {k: None if isinstance(v, float) and v != v else v for k, v in row.items()}
        try:
            OrderLine.model_validate(row)
        except ValueError as exc:
            errors.append(f"{row.get('supplier')}/{row.get('sku')}: {exc}")
    return errors


def summarize(orders):
    result = []
    for supplier, part in orders.groupby("supplier", sort=True):
        result.append({"supplier": supplier, "sku_count": len(part),
                       "order_count": int((part.recommended_qty > 0).sum()),
                       "critical_count": int(part.urgency.eq("CRITICAL").sum()),
                       "qty_by_unit": {str(k): int(v) for k, v in part.groupby("unit").recommended_qty.sum().items()}})
    return result
