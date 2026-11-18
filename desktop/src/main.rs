#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Deserialize;
use serde_json::{Value, json};
use std::io::{BufRead, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::webview::{DownloadEvent, NewWindowResponse, PageLoadEvent};
use tauri::{Manager, State, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};

#[derive(Default)]
struct CloseState(AtomicBool);

#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case")]
enum HostMessage {
    Eval { script: String },
    Activate,
    Complete { orders: Vec<Value> },
    ConfirmLoadingClose,
}

fn emit(value: Value) {
    let mut output = std::io::stdout().lock();
    let _ = writeln!(output, "{value}");
    let _ = output.flush();
}

#[tauri::command]
fn close_completed_window(
    window: WebviewWindow,
    state: State<'_, CloseState>,
) -> Result<(), String> {
    if !state.0.load(Ordering::Acquire) {
        return Err("尚未確認後端完成收尾".into());
    }
    window.close().map_err(|_| "視窗未能關閉".into())
}

fn loopback_url(value: &str) -> Result<Url, &'static str> {
    let url = Url::parse(value).map_err(|_| "invalid workbench URL")?;
    if url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1")
        || url.port().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("only the selected loopback workbench is allowed");
    }
    Ok(url)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let url = loopback_url(&args.next().ok_or("start with start.vbs")?)?;
    let profile = args.next().ok_or("missing webview data directory")?;
    let origin = url.origin();
    let initialization = format!(
        "if(location.origin==={}){{window.__GRID_DESKTOP__=true;window.gridDesktop={{closeCompletedWindow:()=>window.__TAURI_INTERNALS__.invoke('close_completed_window')}};}}",
        serde_json::to_string(&url.origin().ascii_serialization())?
    );
    tauri::Builder::default()
        .manage(CloseState::default())
        .invoke_handler(tauri::generate_handler![close_completed_window])
        .setup(move |app| {
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("美股網格工作台")
                .inner_size(1440.0, 900.0).min_inner_size(900.0, 650.0)
                .data_directory(profile.into()).incognito(true)
                .zoom_hotkeys_enabled(true).disable_drag_drop_handler()
                .initialization_script(initialization)
                .on_navigation(move |url| {
                    if url.origin() == origin { return true; }
                    if matches!(url.scheme(), "http" | "https") {
                        emit(json!({"event":"external_link","url":url.as_str()}));
                    }
                    false
                })
                .on_new_window(|url, _| {
                    if matches!(url.scheme(), "http" | "https") {
                        emit(json!({"event":"external_link","url":url.as_str()}));
                    }
                    NewWindowResponse::Deny
                })
                .on_page_load(|_, payload| {
                    if payload.event() == PageLoadEvent::Finished { emit(json!({"event":"loaded"})); }
                })
                .on_download(|webview, event| {
                    match event {
                        DownloadEvent::Requested { destination, .. } => {
                            let name = destination.file_name().unwrap_or_default().to_string_lossy();
                            let Some(path) = rfd::FileDialog::new().set_parent(&webview.window()).set_file_name(name.as_ref()).save_file() else { return false };
                            *destination = path;
                        }
                        DownloadEvent::Finished { success, .. } => {
                            emit(json!({"event":"download_finished","success":success}));
                        }
                        _ => {}
                    }
                    true
                })
                .build()?;
            let handle = app.handle().clone();
            window.on_window_event(move |event| match event {
                WindowEvent::CloseRequested { api, .. } => {
                    if !handle.state::<CloseState>().0.load(Ordering::Acquire) {
                        api.prevent_close(); emit(json!({"event":"closing"}));
                    }
                }
                WindowEvent::Resized(_) => {
                    if let Some(window) = handle.get_webview_window("main") {
                        emit(json!({"event":"visibility","hidden":window.is_minimized().unwrap_or(false)}));
                    }
                }
                _ => {}
            });
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                for line in std::io::stdin().lock().lines() {
                    let Ok(line) = line else { break };
                    let Ok(message) = serde_json::from_str::<HostMessage>(&line) else { continue };
                    let Some(window) = handle.get_webview_window("main") else { break };
                    match message {
                        HostMessage::Eval { script } => { let _ = window.eval(&script); }
                        HostMessage::Activate => {
                            let _ = window.show(); let _ = window.unminimize(); let _ = window.set_focus();
                        }
                        HostMessage::Complete { orders } => {
                            handle.state::<CloseState>().0.store(true, Ordering::Release);
                            if orders.is_empty() { let _ = window.close(); }
                            else { let _ = window.eval(&format!("window.completeDesktopShutdown({})", json!(orders))); }
                        }
                        HostMessage::ConfirmLoadingClose => {
                            if rfd::MessageDialog::new().set_title("確認結束程式")
                                .set_description("畫面仍在載入。要停止工作台並結束嗎？既有委託會保留。")
                                .set_buttons(rfd::MessageButtons::YesNo).show() == rfd::MessageDialogResult::Yes {
                                emit(json!({"event":"loading_close_confirmed"}));
                            }
                        }
                    }
                }
                // Parent death leaves a visible diagnosis; no new trading engine is spawned.
                handle.state::<CloseState>().0.store(true, Ordering::Release);
                if let Some(window) = handle.get_webview_window("main") {
                    let _ = window.eval("document.getElementById('notice').textContent='引擎連線已結束，請保留紀錄並查核。'");
                }
            });
            Ok(())
        })
        .run(tauri::generate_context!())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_selected_loopback_root_is_allowed() {
        assert!(loopback_url("http://127.0.0.1:49152/").is_ok());
        for url in [
            "http://example.com:49152/",
            "http://127.0.0.1/",
            "file:///app.html",
            "http://user@127.0.0.1:49152/",
            "http://127.0.0.1:49152/api/session",
        ] {
            assert!(loopback_url(url).is_err(), "{url}");
        }
    }
}
