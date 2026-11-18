fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["close_completed_window"])),
    )
    .expect("desktop configuration must be valid");
}
