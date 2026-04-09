// Основная логика Tauri 2 шелла:
// - запускает sidecar nastya-backend и nastya-worker
// - close-to-tray (по крестику окно прячется, бэкенд продолжает жить)
// - tray-меню: Show / Hide / Restart Backend / Quit
// - Restart Backend убивает CommandChild бэкенда и спавнит заново

use std::sync::Mutex;
#[cfg(windows)]
use std::os::windows::process::CommandExt;

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
    opera_proxy: Mutex<Option<CommandChild>>,
}

/// Спавнит sidecar по имени (без platform-suffix — Tauri добавит сам).
/// Возвращает CommandChild чтобы потом можно было kill().
///
/// При неожиданном завершении процесса автоматически спавнит новый
/// (max 5 рестартов с экспоненциальной задержкой — защита от loop).
fn spawn_sidecar(app: &AppHandle, name: &str) -> Result<CommandChild, String> {
    spawn_sidecar_with_args(app, name, Vec::<String>::new())
}

/// То же что spawn_sidecar, но с дополнительными аргументами для процесса.
fn spawn_sidecar_with_args<I, S>(
    app: &AppHandle,
    name: &str,
    args: I,
) -> Result<CommandChild, String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let args_vec: Vec<String> = args.into_iter().map(|s| s.as_ref().to_string()).collect();
    spawn_sidecar_inner(app, name, args_vec, 0)
}

/// Внутренняя реализация со счётчиком рестартов.
fn spawn_sidecar_inner(
    app: &AppHandle,
    name: &str,
    args: Vec<String>,
    restart_count: u32,
) -> Result<CommandChild, String> {
    let sidecar = app
        .shell()
        .sidecar(name)
        .map_err(|e| format!("sidecar({name}) lookup failed: {e}"))?;

    let sidecar = if args.is_empty() {
        sidecar
    } else {
        sidecar.args(args.clone())
    };

    let (mut rx, child) = sidecar
        .spawn()
        .map_err(|e| format!("sidecar({name}) spawn failed: {e}"))?;

    // Логируем stdout/stderr и ловим Terminated для auto-restart.
    let tag = name.to_string();
    let app_handle = app.clone();
    let args_for_restart = args.clone();
    tauri::async_runtime::spawn(async move {
        let mut terminated = false;
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
                    terminated = true;
                    break;
                }
                CommandEvent::Error(err) => {
                    log::error!("[{tag}] error: {err}");
                    terminated = true;
                    break;
                }
                _ => {}
            }
        }

        if !terminated {
            return;
        }

        // Auto-restart: не дольше 5 попыток, экспоненциальная задержка.
        // Tray Quit выставляет флаг _SHUTTING_DOWN — тогда рестарт не делается.
        if SHUTTING_DOWN.load(std::sync::atomic::Ordering::Acquire) {
            log::info!("[{tag}] shutdown in progress, skipping auto-restart");
            return;
        }

        if restart_count >= 5 {
            log::error!(
                "[{tag}] exceeded max restart attempts ({}), giving up",
                restart_count
            );
            return;
        }

        let delay_ms = 500u64 * (1 << restart_count.min(5));
        log::warn!(
            "[{tag}] auto-restarting (attempt {}/5) after {}ms",
            restart_count + 1,
            delay_ms
        );
        tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;

        match spawn_sidecar_inner(&app_handle, &tag, args_for_restart, restart_count + 1) {
            Ok(new_child) => {
                log::info!("[{tag}] respawned successfully");
                // Сохраняем новый child в SidecarState по имени
                let state = app_handle.state::<SidecarState>();
                let slot = match tag.as_str() {
                    "nastya-backend" => &state.backend,
                    "nastya-worker" => &state.worker,
                    "opera-proxy" => &state.opera_proxy,
                    _ => return,
                };
                *slot.lock().unwrap() = Some(new_child);
            }
            Err(e) => log::error!("[{tag}] respawn failed: {}", e),
        }
    });

    Ok(child)
}

/// Глобальный флаг: выставляется перед shutdown_sidecars() чтобы auto-restart
/// не оживлял только что убитые процессы.
static SHUTTING_DOWN: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

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
    // Блокируем auto-restart — иначе при kill() respawn попытается воскресить
    SHUTTING_DOWN.store(true, std::sync::atomic::Ordering::Release);

    let state = app.state::<SidecarState>();
    let backend_child = state.backend.lock().unwrap().take();
    let worker_child = state.worker.lock().unwrap().take();
    let opera_child = state.opera_proxy.lock().unwrap().take();
    if let Some(child) = backend_child {
        let _ = child.kill();
    }
    if let Some(child) = worker_child {
        let _ = child.kill();
    }
    if let Some(child) = opera_child {
        let _ = child.kill();
    }

    // Страховка — если CommandChild.kill() не убил процесс (или есть
    // остатки от прошлых запусков), прибиваем всё по имени через taskkill.
    // На Windows /T убивает дерево процессов, /F — принудительно.
    #[cfg(windows)]
    {
        use std::process::Command;
        // В bundled NSIS установке sidecar-ы называются без platform-suffix
        // (Tauri переименовывает: opera-proxy-*.exe → opera-proxy.exe).
        // В dev-режиме через cargo tauri dev имя сохраняется с suffix.
        // Убиваем оба варианта имени чтобы покрыть обе ситуации.
        for name in &[
            "nastya-backend.exe",
            "nastya-worker.exe",
            "opera-proxy.exe",
            "opera-proxy-x86_64-pc-windows-msvc.exe",
        ] {
            let _ = Command::new("taskkill")
                .args(["/F", "/T", "/IM", name])
                .creation_flags(0x08000000) // CREATE_NO_WINDOW
                .output();
        }
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

            // 0. Opera VPN proxy — встроенный обход блокировок для всего исходящего.
            // Outbound через корп-прокси (Дима + Настя сидят за корп-шлюзом),
            // далее opera-proxy идёт в Opera VPN API и туннелирует трафик через EU.
            // Локально слушает 127.0.0.1:18080, backend/worker используют его как HTTPS_PROXY.
            let opera_args = vec![
                "-bind-address".to_string(),
                "127.0.0.1:18080".to_string(),
                "-proxy".to_string(),
                "http://user393678:a6g7ln@94.103.191.13:3528".to_string(),
                "-country".to_string(),
                "EU".to_string(),
            ];
            match spawn_sidecar_with_args(&handle, "opera-proxy", opera_args) {
                Ok(child) => {
                    *handle.state::<SidecarState>().opera_proxy.lock().unwrap() = Some(child);
                    log::info!("opera-proxy sidecar started on 127.0.0.1:18080");
                }
                Err(e) => log::error!("opera-proxy sidecar failed: {e}"),
            }

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
