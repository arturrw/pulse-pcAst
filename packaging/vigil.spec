# Build: pyinstaller packaging/vigil.spec --noconfirm   ->   dist/vigil/{vigil.exe, vigilw.exe}
# vigil.exe has a console (used by the desktop shell to read the address the UI prints); vigilw.exe has none
# (used by the scheduled background jobs so no window flashes).
from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis([str(root / "packaging" / "entry.py")], pathex=[str(root / "src")],
             datas=[(str(root / "scripts" / "autostart.ps1"), "scripts")],
             hiddenimports=["pynvml"], excludes=["tkinter", "matplotlib", "numpy", "pandas"])
pyz = PYZ(a.pure)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="vigil", console=True)
win = EXE(pyz, a.scripts, [], exclude_binaries=True, name="vigilw", console=False)
COLLECT(cli, win, a.binaries, a.datas, name="vigil")
