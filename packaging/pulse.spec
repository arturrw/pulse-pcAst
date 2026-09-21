# Build: pyinstaller packaging/pulse.spec --noconfirm   ->   dist/pulse/{pulse.exe, pulsew.exe}
# pulse.exe has a console (used by the desktop shell to read the address the UI prints); pulsew.exe has none
# (used by the scheduled background jobs so no window flashes).
from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis([str(root / "packaging" / "entry.py")], pathex=[str(root / "src")],
             datas=[(str(root / "scripts" / "autostart.ps1"), "scripts"), (str(root / "scripts" / "record_presentmon.ps1"), "scripts")],
             hiddenimports=["pynvml"], excludes=["tkinter", "matplotlib", "numpy", "pandas"])
pyz = PYZ(a.pure)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="pulse", console=True)
win = EXE(pyz, a.scripts, [], exclude_binaries=True, name="pulsew", console=False)
COLLECT(cli, win, a.binaries, a.datas, name="pulse")
