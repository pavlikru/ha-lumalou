"""HACS release archive layout and reproducibility tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

PROJECT_ROOT = Path(__file__).parents[1]
BUILDER = PROJECT_ROOT / "scripts" / "build_hacs_zip.py"
COMPONENT_ROOT = PROJECT_ROOT / "custom_components" / "lumalou"


def test_hacs_archive_is_rooted_complete_and_reproducible(tmp_path: Path) -> None:
    """CI and releases must ship the same complete root-level component."""
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    for destination in (first, second):
        subprocess.run(
            [sys.executable, str(BUILDER), str(destination)],
            cwd=PROJECT_ROOT,
            check=True,
        )

    assert first.read_bytes() == second.read_bytes()
    expected = {
        path.relative_to(COMPONENT_ROOT).as_posix()
        for path in COMPONENT_ROOT.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    }
    with ZipFile(first) as archive:
        names = archive.namelist()
        assert set(names) == expected
        assert names == sorted(names)
        assert {"manifest.json", "__init__.py", "brand/icon.png"} <= set(names)
        assert not any(name.startswith("custom_components/") for name in names)
        assert not any("/__pycache__/" in f"/{name}" for name in names)
        assert not any(name.endswith((".pyc", ".pyo")) for name in names)
        assert all(
            info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()
        )
        assert all(info.create_system == 3 for info in archive.infolist())
        assert all(
            (info.external_attr >> 16) == 0o100644 for info in archive.infolist()
        )
