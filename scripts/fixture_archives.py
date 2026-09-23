"""Extract only workbook inputs to a fresh test directory, independent of ZIP encodings."""
from pathlib import Path, PurePosixPath
import shutil
import stat
from zipfile import ZipFile

MAX_WORKBOOK_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_BYTES = 600 * 1024 * 1024


def extract_workbooks(archive: Path, destination: Path) -> list[Path]:
    """Flatten XLSX into a new supplier directory; loader discovers files by headers.

    Never overwrite an existing destination. Original files and data/raw remain untouched.
    Metadata, lock files and unrelated files are ignored. Reject unsafe archive members.
    """
    archive, destination = Path(archive), Path(destination)
    if not archive.is_file():
        raise ValueError(f"Архив не найден: {archive}")
    if destination.exists():
        raise ValueError(f"Папка уже существует, перезапись запрещена: {destination}")
    with ZipFile(archive) as z:
        members = []
        for info in z.infolist():
            path = PurePosixPath(info.filename.replace('\\', '/'))
            if path.is_absolute() or '..' in path.parts or (path.parts and ':' in path.parts[0]):
                raise ValueError(f"Небезопасный путь в ZIP: {info.filename}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"Ссылки в ZIP не поддерживаются: {info.filename}")
            if info.is_dir() or '__MACOSX' in path.parts or path.name.startswith(('._', '~$')):
                continue
            if path.suffix.lower() != '.xlsx':
                continue
            if info.file_size > MAX_WORKBOOK_BYTES:
                raise ValueError(f"Слишком большой Excel-файл: {info.filename}")
            members.append(info)
        if not members:
            raise ValueError(f"В архиве нет Excel-файлов: {archive}")
        if sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
            raise ValueError(f"Слишком большой распакованный архив: {archive}")
        destination.mkdir(parents=True)
        output = []
        for i, info in enumerate(members, 1):
            # Neutral names deliberately exercise header-based source discovery.
            target = destination / f'input_{i:02d}.xlsx'
            with z.open(info) as source, target.open('xb') as dest:
                shutil.copyfileobj(source, dest)
            output.append(target)
        return output


def extract_suppliers(iek_zip: Path, se_zip: Path, root: Path) -> dict[str, list[Path]]:
    return {'IEK': extract_workbooks(iek_zip, root / 'IEK'),
            'Systeme Electric': extract_workbooks(se_zip, root / 'SE')}


if __name__ == '__main__':
    import argparse
    import os
    import tempfile

    parser = argparse.ArgumentParser(description='Подготовить отдельную папку данных для локального запуска QadamSupply')
    parser.add_argument('--iek-zip', type=Path, required=True)
    parser.add_argument('--se-zip', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='Новая папка, например data/raw')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error(f'Папка уже существует: {output}. Укажите новую папку; существующие данные не перезаписываются.')
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='.supplyai-import-', dir=output.parent) as temp:
            staged = Path(temp) / 'inputs'
            extracted = extract_suppliers(args.iek_zip, args.se_zip, staged)
            os.rename(staged, output)
        print(f'Готово: {sum(map(len, extracted.values()))} Excel-файлов в {output}')
    except (OSError, ValueError) as error:
        parser.exit(1, f'Ошибка импорта: {error}\n')
