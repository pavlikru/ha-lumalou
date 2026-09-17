"""Build a deterministic HACS release archive with integration files at root."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = PROJECT_ROOT / "custom_components" / "lumalou"
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _runtime_files() -> list[Path]:
    """Return sorted runtime files, excluding local Python caches."""
    return [
        path
        for path in sorted(COMPONENT_ROOT.rglob("*"))
        if path.is_file()
        and not path.is_symlink()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    ]


def build_archive(destination: Path) -> None:
    """Write one byte-reproducible HACS archive."""
    destination = destination.resolve()
    if destination.is_relative_to(COMPONENT_ROOT):
        raise ValueError("Archive destination must be outside the component tree")
    files = _runtime_files()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(
        destination, "w", compression=ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in files:
            name = path.relative_to(COMPONENT_ROOT).as_posix()
            info = ZipInfo(name, date_time=FIXED_TIMESTAMP)
            info.create_system = 3
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)


def main() -> None:
    """Parse the destination and build the archive."""
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    build_archive(args.destination)


if __name__ == "__main__":
    main()
