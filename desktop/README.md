# Vigil desktop shell (Tauri)

A thin window around `vigil ui`: starts the frozen backend, shows its local page, tray icon (open / quit),
closing the window hides it to the tray, the backend dies with the shell.

Build (Rust + Node needed):

    .venv/Scripts/python.exe -m PyInstaller packaging/vigil.spec --noconfirm --distpath build/dist --workpath build/work
    cd desktop && npm install && npx tauri build

The installer is `desktop/src-tauri/target/release/bundle/nsis/vigil_0.1.0_x64-setup.exe`.
