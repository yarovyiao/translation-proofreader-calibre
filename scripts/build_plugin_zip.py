"""Build an installable Calibre plugin ZIP from this repository.

The archive deliberately has __init__.py at its root. GitHub's automatic
source archives have an enclosing directory and are not installable plugins.
"""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import argparse


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_FILES = (
    '__init__.py',
    'api.py',
    'config.py',
    'dialogs.py',
    'epub.py',
    'language_scan.py',
    'localization.py',
    'terminology.py',
    'transfer.py',
    'ui.py',
    'images/icon.png',
    'plugin-import-name-translation_proofreader.txt',
    'README.md',
    'LICENSE',
    'CHANGELOG.md',
    'docs/USER_GUIDE.md',
)


def build(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in PLUGIN_FILES:
            source = ROOT / relative
            if not source.is_file():
                raise FileNotFoundError(source)
            archive.write(source, relative)

    with ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Plugin ZIP failed its integrity check')
        if '__init__.py' not in archive.namelist():
            raise RuntimeError('Plugin ZIP has no root __init__.py')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    build(args.destination)
    print(args.destination)
