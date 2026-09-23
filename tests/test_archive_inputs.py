from zipfile import ZipFile
import pytest
from scripts.fixture_archives import extract_workbooks


def archive(tmp_path, entries):
    path = tmp_path / 'sample.zip'
    with ZipFile(path, 'w') as z:
        for name, data in entries:
            z.writestr(name, data)
    return path


def test_flattens_workbooks_without_losing_duplicate_basenames(tmp_path):
    path = archive(tmp_path, [('ИЭК/a.xlsx', b'first'), ('nested/a.xlsx', b'second'),
                            ('__MACOSX/._a.xlsx', b'metadata'), ('~$lock.xlsx', b'lock'), ('note.txt', b'note')])
    output = extract_workbooks(path, tmp_path / 'inputs')
    assert [p.read_bytes() for p in output] == [b'first', b'second']
    assert all(p.parent == tmp_path / 'inputs' for p in output)


@pytest.mark.parametrize('name', ['../escape.xlsx', '/absolute.xlsx', 'C:/escape.xlsx', '..\\escape.xlsx'])
def test_rejects_unsafe_paths(tmp_path, name):
    with pytest.raises(ValueError, match='Небезопасный'):
        extract_workbooks(archive(tmp_path, [(name, b'x')]), tmp_path / 'inputs')
    assert not (tmp_path / 'inputs').exists()


def test_never_overwrites_working_data(tmp_path):
    existing = tmp_path / 'inputs'
    existing.mkdir()
    (existing / 'keep.txt').write_text('keep')
    with pytest.raises(ValueError, match='перезапись'):
        extract_workbooks(archive(tmp_path, [('a.xlsx', b'x')]), existing)
    assert (existing / 'keep.txt').read_text() == 'keep'


def test_missing_or_empty_archive_is_an_error(tmp_path):
    with pytest.raises(ValueError, match='не найден'):
        extract_workbooks(tmp_path / 'missing.zip', tmp_path / 'a')
    with pytest.raises(ValueError, match='нет Excel'):
        extract_workbooks(archive(tmp_path, [('readme.txt', b'no sheets')]), tmp_path / 'b')
