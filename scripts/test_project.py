"""One command for teammates: isolated pytest + Node module checks + optional real ZIPs."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description='Проверка SupplyAI без ключей AI и изменения рабочих данных')
    parser.add_argument('--iek-zip', type=Path, help='Путь к архиву IEK')
    parser.add_argument('--se-zip', type=Path, help='Путь к архиву Systeme Electric')
    parser.add_argument('--report', type=Path, default=ROOT / 'reports' / 'pytest.xml', help='JUnit XML отчёт')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error('Нужен Python 3.11+ (рекомендуется 3.12).')
    if not shutil.which('node'):
        parser.error('Нужен Node.js 18+ (рекомендуется 20+) для проверки JS-модулей и XLSX export.')
    version = subprocess.run(['node', '--version'], capture_output=True, text=True, check=True).stdout.strip()
    if int(version.lstrip('v').split('.')[0]) < 18:
        parser.error(f'Нужен Node.js 18+; установлен {version}.')
    if bool(args.iek_zip) != bool(args.se_zip):
        parser.error('Укажите оба архива: --iek-zip и --se-zip.')
    for path in (args.iek_zip, args.se_zip):
        if path and not path.is_file():
            parser.error(f'Архив не найден: {path}')
    env = os.environ.copy()
    # Empty values also prevent load_dotenv from restoring paid API credentials.
    for key in ('OPENAI_API_KEY', 'NVIDIA_API_KEY', 'LANGSMITH_API_KEY'):
        env[key] = ''
    env['LANGCHAIN_TRACING_V2'] = env['LANGSMITH_TRACING'] = 'false'
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'pytest', '-ra', '--tb=short', f'--junitxml={report}']
    if args.iek_zip:
        command += [f'--iek-zip={args.iek_zip.resolve()}', f'--se-zip={args.se_zip.resolve()}']
    print('SupplyAI: ' + ('полный прогон с двумя архивами' if args.iek_zip else 'быстрый прогон; ZIP-тесты будут пропущены'), flush=True)
    result = subprocess.run(command, cwd=ROOT, env=env)
    print(f'Отчёт: {report}', flush=True)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
