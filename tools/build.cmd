@echo off
rem Offline build wrapper: local gnu rust toolchain + rust-lld (no MSVC/SDK
rem required) + vendored sources. Fully offline.
set "CARGO_HOME=F:\D2Rep_project\dsh_test\.cargo-home"
set "RUSTUP_HOME=F:\D2Rep_project\dsh_test\.rustup-home"
set "TC=F:\D2Rep_project\dsh_test\rust_toolchain_x86_64-pc-windows-gnu"
set "PATH=%TC%\bin;%TC%\lib\rustlib\x86_64-pc-windows-gnu\bin;%PATH%"
set "RUSTFLAGS=-C linker=rust-lld -C target-feature=+crt-static"
cargo build --offline --manifest-path F:\D2Rep_project\dsh_test\dota_parse\Cargo.toml %*
