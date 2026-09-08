"""Build script for ZOUWUCODE — packages as a portable Windows executable.

Uses PyInstaller to create a single .exe file with embedded Python,
so it does NOT depend on the system Python installation and does
NOT write to the C drive (all data lives in a sibling folder).

Usage:
    python build.py          # Build the executable
    python build.py --clean  # Clean build artifacts
"""

import os
import sys
import shutil
import subprocess
import argparse
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.resolve()
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
SPEC_FILE = PROJECT_ROOT / "zouwucode.spec"
APP_NAME = "ZOUWUCODE"
VERSION = "1.0.0"


def clean():
    """Remove build artifacts."""
    print("  Cleaning build artifacts...")
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    if SPEC_FILE.exists():
        SPEC_FILE.unlink()
    print("  Done.")


def build():
    """Build the executable using PyInstaller."""
    print(f"\n  Building {APP_NAME} v{VERSION}...\n")

    # Ensure PyInstaller is installed
    try:
        import PyInstaller  # noqa
    except ImportError:
        print("  Installing PyInstaller...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
        )

    # Build command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onefile",                    # Single executable
        "--console",                    # Console application
        "--clean",                      # Clean cache
        "--noconfirm",                  # Overwrite without asking
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        "--specpath", str(PROJECT_ROOT),
        # Add data files
        "--add-data", f"{PROJECT_ROOT / 'zouwucode'}{os.pathsep}zouwucode",
        "--add-data", f"{PROJECT_ROOT / 'hello_my_zouwucode'}{os.pathsep}hello_my_zouwucode",
        # Hidden imports (for modules discovered at runtime)
        "--hidden-import", "httpx",
        "--hidden-import", "pydantic",
        "--hidden-import", "yaml",
        "--hidden-import", "bs4",
        # Exclude unnecessary modules to reduce size
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "PIL",
        "--exclude-module", "cv2",
        # Version info
        "--version-file", str(PROJECT_ROOT / "version.txt"),
        # Icon (if available)
        # "--icon", str(PROJECT_ROOT / "assets" / "icon.ico"),
        # Entry point
        str(PROJECT_ROOT / "zouwucode" / "__main__.py"),
    ]

    # Create version file
    with open(PROJECT_ROOT / "version.txt", "w") as f:
        f.write(f"VSVersionInfo(\n")
        f.write(f"  ffi=FixedFileInfo(\n")
        f.write(f"    filevers=({VERSION.replace('.', ',')},0),\n")
        f.write(f"    prodvers=({VERSION.replace('.', ',')},0),\n")
        f.write(f"    mask=0x3f,\n")
        f.write(f"    flags=0x0,\n")
        f.write(f"    OS=0x40004,\n")
        f.write(f"    fileType=0x1,\n")
        f.write(f"    subtype=0x0,\n")
        f.write(f"    date=(0,0)\n")
        f.write(f"  ),\n")
        f.write(f"  kids=[\n")
        f.write(f"    StringFileInfo([\n")
        f.write(f"      StringTable(\n")
        f.write(f"        '080404b0',\n")
        f.write(f"        [StringStruct('CompanyName', '走戊工作室 (Zouwu Studio)'),\n")
        f.write(f"         StringStruct('FileDescription', 'ZOUWUCODE - AI Coding Agent'),\n")
        f.write(f"         StringStruct('FileVersion', '{VERSION}'),\n")
        f.write(f"         StringStruct('InternalName', '{APP_NAME}'),\n")
        f.write(f"         StringStruct('LegalCopyright', 'MIT License'),\n")
        f.write(f"         StringStruct('OriginalFilename', '{APP_NAME}.exe'),\n")
        f.write(f"         StringStruct('ProductName', '{APP_NAME}'),\n")
        f.write(f"         StringStruct('ProductVersion', '{VERSION}')])\n")
        f.write(f"    ]),\n")
        f.write(f"    VarFileInfo([VarStruct('Translation', [2052, 1200])])\n")
        f.write(f"  ]\n")
        f.write(f")\n")

    print(f"  Running: {' '.join(cmd)}\n")
    subprocess.check_call(cmd)

    # Verify the output
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\n  ✓ Build complete!")
        print(f"  📦 {exe_path}")
        print(f"  📏 Size: {size_mb:.1f} MB")
        print(f"\n  Run: {exe_path} --help")
    else:
        print(f"\n  ✗ Build failed: {exe_path} not found")


def main():
    parser = argparse.ArgumentParser(description="Build ZOUWUCODE executable")
    parser.add_argument("--clean", action="store_true", help="Clean build artifacts")
    args = parser.parse_args()

    if args.clean:
        clean()
    else:
        # Clean first, then build
        clean()
        build()


if __name__ == "__main__":
    main()