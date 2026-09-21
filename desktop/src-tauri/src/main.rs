// Thin shell: starts the Python backend (`pulse ui --no-browser --idle 0 --json-ready`), reads the JSON ready line
// it prints, shows the local page in a window, keeps a tray icon, and stops the backend on exit.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_updater::UpdaterExt;

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct Backend(Mutex<Option<Child>>);
/// The page a `pulse://<page>` link asked for while the backend was still starting.
struct PendingTab(Mutex<Option<String>>);

const PAGES: [&str; 6] = ["overview", "ask", "findings", "timeline", "games", "setup"];

/// The page named by a `pulse://<page>` argument (a clicked notification starts the app with one). Only the app's own
/// page names are accepted, whatever else the link says.
fn tab_from_args<I: IntoIterator<Item = String>>(args: I) -> Option<String> {
    args.into_iter().find_map(|a| {
        let rest = a.strip_prefix("pulse://")?;
        let name = rest.split(|c| c == '/' || c == '?' || c == '#').next()?.to_ascii_lowercase();
        PAGES.contains(&name.as_str()).then_some(name)
    })
}

/// Switch the window to a page of the app (the page defines `go`).
fn open_tab(app: &AppHandle, tab: &str) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.eval(&format!("if (typeof go === 'function') go('{tab}')"));
    }
}

/// Put the backend into a job object that kills its members when the shell dies for any reason (crash, End task),
/// so a killed shell never leaves the backend running.
fn bind_to_kill_job(child: &Child) {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::JobObjects::*;
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return;
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(job, JobObjectExtendedLimitInformation, &info as *const _ as *const _,
                                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32);
        AssignProcessToJobObject(job, child.as_raw_handle() as _);
        // the job handle is intentionally never closed: it lives as long as the shell process
    }
}

/// The frozen backend: bundled next to the app (installed), or the repo build output (development).
fn backend_exe(app: &AppHandle) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(dir) = app.path().resource_dir() {
        candidates.push(dir.join("backend").join("pulse.exe"));
    }
    candidates.push(PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../../build/dist/pulse/pulse.exe")));
    candidates.into_iter().find(|p| p.exists())
}

fn start_backend(app: &AppHandle) -> Result<(Child, String), String> {
    let exe = backend_exe(app).ok_or("backend (pulse.exe) not found")?;
    let mut child = Command::new(&exe)
        .args(["ui", "--no-browser", "--idle", "0", "--json-ready"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(|e| format!("could not start the backend: {e}"))?;
    bind_to_kill_job(&child);
    let out = child.stdout.take().ok_or("no stdout from the backend")?;
    let mut line = String::new();
    BufReader::new(out).read_line(&mut line).map_err(|e| format!("no ready line: {e}"))?;
    let v: serde_json::Value = serde_json::from_str(line.trim()).map_err(|e| format!("bad ready line: {e}"))?;
    let url = v["url"].as_str().ok_or("ready line has no url")?.to_string();
    Ok((child, url))
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn stop_backend(app: &AppHandle) {
    if let Some(state) = app.try_state::<Backend>() {
        if let Some(mut child) = state.0.lock().unwrap().take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[derive(serde::Serialize)]
struct UpdateInfo {
    version: String,
    notes: Option<String>,
}

/// The newer signed release, if there is one (the answer is `null` when this is the latest).
#[tauri::command]
async fn check_update(app: AppHandle) -> Result<Option<UpdateInfo>, String> {
    let update = app.updater().map_err(|e| e.to_string())?.check().await.map_err(|e| e.to_string())?;
    Ok(update.map(|u| UpdateInfo { version: u.version, notes: u.body }))
}

/// Download the newer release (its signature is checked against the key built into the app), stop the backend and run
/// the installer, which restarts Pulse when it is done.
#[tauri::command]
async fn install_update(app: AppHandle) -> Result<(), String> {
    let update = app.updater().map_err(|e| e.to_string())?.check().await.map_err(|e| e.to_string())?;
    let Some(update) = update else { return Ok(()) };
    let bytes = update.download(|_, _| {}, || {}).await.map_err(|e| e.to_string())?;
    stop_backend(&app);
    update.install(bytes).map_err(|e| e.to_string())?;
    app.restart()
}

fn main() {
    let app = tauri::Builder::default()
        // First: a second start (a clicked notification, a shortcut) hands over to the running app instead of opening another.
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            show_main(app);
            if let Some(tab) = tab_from_args(argv) {
                *app.state::<PendingTab>().0.lock().unwrap() = Some(tab.clone());
                open_tab(app, &tab);
            }
        }))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![check_update, install_update])
        .manage(Backend(Mutex::new(None)))
        .manage(PendingTab(Mutex::new(tab_from_args(std::env::args().skip(1)))))
        .setup(|app| {
            let handle = app.handle().clone();

            let open = MenuItem::with_id(app, "open", "Open Pulse", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &quit])?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("Pulse")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "open" => show_main(app),
                    "quit" => {
                        stop_backend(app);
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                        show_main(tray.app_handle());
                    }
                })
                .build(app)?;

            // Start the backend off the UI thread; the window shows "Starting…" until the address is known.
            std::thread::spawn(move || match start_backend(&handle) {
                Ok((child, url)) => {
                    *handle.state::<Backend>().0.lock().unwrap() = Some(child);
                    if let Some(w) = handle.get_webview_window("main") {
                        let tab = handle.state::<PendingTab>().0.lock().unwrap().take();
                        let target = match tab {
                            Some(t) => format!("{url}#{t}"),
                            None => url.clone(),
                        };
                        if let Ok(u) = target.parse() {
                            let _ = w.navigate(u);
                        }
                    }
                }
                Err(e) => {
                    if let Some(w) = handle.get_webview_window("main") {
                        let msg = e.replace('\\', "/").replace('"', "'");
                        let _ = w.eval(&format!("document.body.innerText = \"Pulse could not start: {msg}\""));
                    }
                }
            });
            Ok(())
        })
        // Closing the window hides it to the tray; "Quit" in the tray really exits.
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building the app");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_backend(handle);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::tab_from_args;

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn a_link_opens_only_a_page_of_the_app() {
        assert_eq!(tab_from_args(args(&["pulse://findings"])), Some("findings".into()));
        assert_eq!(tab_from_args(args(&["pulse-desktop.exe", "pulse://Games/"])), Some("games".into()));
        assert_eq!(tab_from_args(args(&["pulse://timeline?when=14:03"])), Some("timeline".into()));
        assert_eq!(tab_from_args(args(&["pulse://setup#x"])), Some("setup".into()));
        for bad in ["pulse://", "pulse://evil", "pulse://findings.exe", "pulse:findings", "http://findings", "findings", "pulse://'); alert(1)//"] {
            assert_eq!(tab_from_args(args(&[bad])), None, "{bad}");
        }
        assert_eq!(tab_from_args(Vec::<String>::new()), None);
    }
}
