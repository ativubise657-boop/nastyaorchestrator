// Точка входа Windows-приложения. Прячем консоль в release-сборке.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    nastya_orchestrator_lib::run();
}
