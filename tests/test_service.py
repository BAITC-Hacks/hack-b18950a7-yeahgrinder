from io import BytesIO

from openpyxl import load_workbook
import pytest

from engine.models import Params
from service import ProcurementService


def test_persistence_approval_export_and_rule(dataset, tmp_path):
    service = ProcurementService(storage=tmp_path, dataset=dataset)
    run = service.calculate(Params())
    restored = service.get_result(run.run_id)
    assert restored.summary == run.summary
    with pytest.raises(ValueError, match='кратно'):
        service.decide(run.run_id, 'IEK', 'approve', {'001_': 11})
    approved = service.decide(run.run_id, 'IEK', 'approve', {'001_': 1000})
    book = load_workbook(BytesIO(service.export(approved)))
    assert book['Заказ']['E2'].value == 1000
    assert service.memory.get_decision(approved)['changes']['001_']['delta'] != 0
    rejected = service.decide(run.run_id, 'IEK', 'reject', reason='Проверить остаток')
    with pytest.raises(ValueError, match='утверждения'):
        service.export(rejected)
    service.save_rule(run.run_id, 'IEK', '001_', 2000)
    future = service.calculate(Params())
    assert future.orders.iloc[0].recommended_qty >= 2000
    assert 'MANAGER_RULE' in future.orders.iloc[0].reason_codes


def test_what_if_does_not_save_or_change_source(dataset, tmp_path):
    service = ProcurementService(storage=tmp_path, dataset=dataset)
    run = service.calculate(Params())
    before = len(service.memory.list_runs())
    result = service.what_if(run.run_id, supplier='IEK', sku='001_', extra_transit=500)
    assert result['changes'][0]['delta'] < 0
    assert len(service.memory.list_runs()) == before
    assert dataset.transit.empty
