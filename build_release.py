"""Release builder — produces a standalone ``dist/lrc-maker.exe`` via PyInstaller.

Usage::

    python build_release.py          # build only
    python build_release.py --zip    # build and create a .zip for distribution

After a successful build the executable lives at ``dist/lrc-maker.exe``.
Distribute the entire ``dist/lrc-maker/`` folder (the .exe needs its
sibling DLLs / plugins).
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "lrc-maker.spec"
DIST = ROOT / "dist"


def check_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit(
            "PyInstaller is not installed.\n"
            "Run: pip install pyinstaller>=6.0.0"
        )


def clean() -> None:
    """Remove previous build artefacts."""
    for folder in (DIST, ROOT / "build"):
        if folder.exists():
            shutil.rmtree(folder)
    for pattern in ("*.pyc", "__pycache__"):
        for p in ROOT.rglob(pattern):
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()


def build() -> Path:
    """Run PyInstaller with the project .spec file."""
    print("Building with PyInstaller ...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC)],
        cwd=str(ROOT),
        capture_output=False,
    )
    if result.returncode != 0:
        sys.exit("PyInstaller build failed.")
    exe = DIST / "lrc-maker.exe"
    if not exe.exists():
        sys.exit(f"Build completed but {exe} was not found.")
    print(f"Done: {exe}")
    return exe


def make_zip(exe: Path) -> Path:
    """Bundle the dist folder into a .zip for distribution."""
    zip_path = DIST / "lrc-maker-release.zip"
    # The dist folder contains the exe and the internal folder with DLLs
    print(f"Creating {zip_path.name} ...")
    base = shutil.make_archive(
        str(zip_path.with_suffix("")),
        "zip",
        root_dir=str(DIST),
        base_dir="lrc-maker",
    )
    print(f"Done: {base}")
    return Path(base)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build LRC Maker release")
    parser.add_argument(
        "--zip", action="store_true",
        help="Also create a .zip archive for distribution",
    )
    args = parser.parse_args()

    check_pyinstaller()
    clean()
    exe = build()
    if args.zip:
        make_zip(exe)


if __name__ == "__main__":
    main()
