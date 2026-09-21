fn main() {
    // The window shows the local backend page; only these two commands may be called from it.
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["check_update", "install_update"])),
    )
    .expect("failed to run the tauri build script");
}
