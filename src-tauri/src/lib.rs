// Основная логика Tauri 2 шелла:
// - запускает sidecar nastya-backend и nastya-worker
// - close-to-tray (по крестику окно прячется, бэкенд продолжает жить)
// - tray-меню: Show / Hide / Restart Backend / Quit
// - Restart Backend убивает CommandChild бэкенда и спавнит заново

use std::sync::Mutex;

use tauri::{
    menu::{Menu, MenuEvent, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, RunEvent, WindowEvent,
};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Хэндлы запущенных sidecar-процессов. None — значит ещё не стартовал или уже убит.
#[derive(Default)]
struct SidecarState {
    backend: Mutex<Option<CommandChild>>,
    worker: Mutex<Option<CommandChild>>,
}

/// Спавнит sidecar по имени (без platform-suffix — Tauri добавит сам).
/// Возвращает CommandChild чтобы потом можно было kill().
fn spawn_sidecar(app: &AppHandle, name: &str) -> Result<CommandChild, String> {
    let sidecar = app
        .shell()
        .sidecar(name)
        .map_err(|e| format!("sidecar({name}) lookup failed: {e}"))?;

    let (mut rx, child) = sidecar
        .spawn()
        .map_err(|e| format!("sidecar({name}) spawn failed: {e}"))?;

    // Логируем stdout/stderr сайдкара в общий лог приложения.
    let tag = name.to_string();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    log::info!("[{tag}] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Stderr(line) => {
                    log::warn!("[{tag}] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Terminated(payload) => {
                    log::warn!("[{tag}] terminated: {:?}", payload);
                    break;
                }
                CommandEvent::Error(err) => {
                    log::error!("[{tag}] error: {err}");
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(child)
}

/// Перезапуск backend sidecar — убить старый, поднять новый.
fn restart_backend(app: &AppHandle) {
    let state = app.state::<SidecarState>();
    {
        let mut guard = state.backend.lock().unwrap();
        if let Some(child) = guard.take() {
            let _ = child.kill();
        }
    }
    match spawn_sidecar(app, "nastya-backend") {
        Ok(child) => {
            *state.backend.lock().unwrap() = Some(child);
            log::info!("backend sidecar restarted");
        }
        Err(e) => log::error!("failed to restart backend: {e}"),
    }
}

/// Корректное завершение всех sidecar-ов перед выходом из приложения.
fn shutdown_sidecars(app: &AppHandle) {
    let state = app.state::<SidecarState>();
    // Сначала забираем оба child'а из мьютексов в локальные переменные —
    // так borrow checker не цепляется к временным от .lock().unwrap().take()
    let backend_child = state.backend.lock().unwrap().take();
    let worker_child = state.worker.lock().unwrap().take();
    if let Some(child) = backend_child {
        let _ = child.kill();
    }
    if let Some(child) = worker_child {
        let _ = child.kill();
    }
}

/// Показать главное окно (вернуть из трея).
fn show_main_window(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

/// Спрятать главное окно в трей.
fn hide_main_window(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.hide();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(SidecarState::default())
        .setup(|app| {
            let handle = app.handle().clone();

            // 1. Поднимаем backend sidecar (FastAPI :8781)
            match spawn_sidecar(&handle, "nastya-backend") {
                Ok(child) => {
                    *handle.state::<SidecarState>().backend.lock().unwrap() = Some(child);
                }
                Err(e) => log::error!("backend sidecar failed: {e}"),
            }

            // 2. Поднимаем worker sidecar
            match spawn_sidecar(&handle, "nastya-worker") {
                Ok(child) => {
                    *handle.state::<SidecarState>().worker.lock().unwrap() = Some(child);
                }
                Err(e) => log::error!("worker sidecar failed: {e}"),
            }

            // 3. Tray-меню: Show / Hide / Restart Backend / Quit
            let show_i = MenuItem::with_id(app, "show", "Show", true, None::<&str>)?;
            let hide_i = MenuItem::with_id(app, "hide", "Hide", true, None::<&str>)?;
            let restart_i = MenuItem::with_id(
                app,
                "restart_backend",
                "Restart Backend",
                true,
                None::<&str>,
            )?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_i, &hide_i, &restart_i, &quit_i])?;

            let _tray = TrayIconBuilder::with_id("main-tray")
                .tooltip("Nastya Orchestrator")
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event: MenuEvent| match event.id.as_ref() {
                    "show" => show_main_window(app),
                    "hide" => hide_main_window(app),
                    "restart_backend" => restart_backend(app),
                    "quit" => {
                        shutdown_sidecars(app);
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    // Левый клик по иконке — показать/скрыть окно
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(win) = app.get_webview_window("main") {
                            if win.is_visible().unwrap_or(false) {
                                let _ = win.hide();
                            } else {
                                show_main_window(app);
                            }
                        }
                    }
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            // Close-to-tray: при попытке закрыть окно — прячем, не выходим.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // На полный выход приложения — гарантированно гасим sidecars.
            if let RunEvent::ExitRequested { .. } = event {
                shutdown_sidecars(app_handle);
            }
        });
}
