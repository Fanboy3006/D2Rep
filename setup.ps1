# setup.ps1
# 用途：在新电脑/新位置展开本项目后，校正写死的绝对路径，检查离线Rust环境是否就绪。
# 使用方式：在项目根目录下运行 .\setup.ps1

$ErrorActionPreference = "Continue"

# ---------- 基础信息 ----------
$ProjectRoot = $PSScriptRoot
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  项目环境自检 / 路径校正" -ForegroundColor Cyan
Write-Host "  项目根目录: $ProjectRoot" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$issues = @()
$fixed = @()

# ---------- 1. 检查离线Rust工具链是否存在，不存在则尝试从快照解压 ----------
$ToolchainDir = Join-Path $ProjectRoot "rust_toolchain_x86_64-pc-windows-gnu"
$SnapshotFile = Join-Path $ProjectRoot "offline_env_snapshot.tar.gz"
$VendorDir = Join-Path $ProjectRoot "vendor"

Write-Host "[1/4] 检查离线 Rust 工具链..." -ForegroundColor Yellow

if (Test-Path $ToolchainDir) {
    Write-Host "  ✓ 工具链目录已存在: $ToolchainDir" -ForegroundColor Green
} elseif (Test-Path $SnapshotFile) {
    Write-Host "  ! 工具链目录缺失，但找到离线快照，尝试解压..." -ForegroundColor Yellow
    try {
        tar -xzf $SnapshotFile -C $ProjectRoot
        if (Test-Path $ToolchainDir) {
            Write-Host "  ✓ 已从快照成功解压工具链" -ForegroundColor Green
            $fixed += "从 offline_env_snapshot.tar.gz 解压了 Rust 工具链"
        } else {
            $issues += "快照解压后仍未找到 $ToolchainDir，请手动检查快照内容"
        }
    } catch {
        $issues += "解压快照失败: $($_.Exception.Message)"
    }
} else {
    $issues += "未找到工具链目录，也未找到离线快照文件（offline_env_snapshot.tar.gz）。需要手动补齐环境。"
}

# ---------- 2. 检查 vendor 依赖目录 ----------
Write-Host "[2/4] 检查 vendor 依赖目录..." -ForegroundColor Yellow

if (Test-Path $VendorDir) {
    $crateCount = (Get-ChildItem $VendorDir -Directory -ErrorAction SilentlyContinue).Count
    Write-Host "  ✓ vendor 目录存在，包含 $crateCount 个 crate" -ForegroundColor Green
} else {
    $issues += "未找到 vendor 目录: $VendorDir （如果快照已解压但仍缺失，检查快照内容是否完整）"
}

# ---------- 3. 校正 .cargo/config.toml 里写死的绝对路径 ----------
Write-Host "[3/4] 校正配置文件中的绝对路径..." -ForegroundColor Yellow

$configFiles = Get-ChildItem -Path $ProjectRoot -Recurse -Filter "config.toml" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\.cargo[\\/]config\.toml$' }

foreach ($cfg in $configFiles) {
    $content = Get-Content $cfg.FullName -Raw
    # 匹配任何指向 vendor 目录的绝对路径（形如 F:\...\vendor 或 F:/.../vendor），替换为当前项目根目录下的路径
    $pattern = '([A-Za-z]:[\\/][^"''\r\n]*?)[\\/]vendor'
    $correctVendorPath = ($VendorDir -replace '\\', '/')

    if ($content -match $pattern) {
        $oldMatch = $matches[0]
        $newContent = $content -replace [regex]::Escape($oldMatch), $correctVendorPath
        if ($newContent -ne $content) {
            Set-Content -Path $cfg.FullName -Value $newContent -NoNewline
            Write-Host "  ✓ 已更新: $($cfg.FullName)" -ForegroundColor Green
            $fixed += "校正了 $($cfg.FullName) 中的 vendor 绝对路径"
        }
    } else {
        Write-Host "  - 未发现需要校正的路径: $($cfg.FullName)" -ForegroundColor Gray
    }
}

if ($configFiles.Count -eq 0) {
    Write-Host "  - 未找到任何 .cargo/config.toml 文件" -ForegroundColor Gray
}

# ---------- 4. 验证工具链可用性 ----------
Write-Host "[4/4] 验证 cargo/rustc 是否可正常调用..." -ForegroundColor Yellow

$cargoExe = Join-Path $ToolchainDir "bin\cargo.exe"
$rustcExe = Join-Path $ToolchainDir "bin\rustc.exe"

if ((Test-Path $cargoExe) -and (Test-Path $rustcExe)) {
    $env:PATH = "$ToolchainDir\bin;" + $env:PATH
    try {
        $cargoVersion = & $cargoExe --version 2>&1
        $rustcVersion = & $rustcExe --version 2>&1
        Write-Host "  ✓ $cargoVersion" -ForegroundColor Green
        Write-Host "  ✓ $rustcVersion" -ForegroundColor Green
        Write-Host ""
        Write-Host "  提示：本次会话已将工具链临时加入 PATH。" -ForegroundColor Cyan
        Write-Host "  如需长期生效，可手动执行：" -ForegroundColor Cyan
        Write-Host "  `$env:PATH = '$ToolchainDir\bin;' + `$env:PATH" -ForegroundColor Gray
    } catch {
        $issues += "cargo/rustc 存在但无法执行: $($_.Exception.Message)"
    }
} else {
    $issues += "未找到 cargo.exe 或 rustc.exe，工具链可能不完整"
}

# ---------- 汇总报告 ----------
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  汇总报告" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

if ($fixed.Count -gt 0) {
    Write-Host ""
    Write-Host "已自动修复:" -ForegroundColor Green
    foreach ($f in $fixed) { Write-Host "  - $f" -ForegroundColor Green }
}

if ($issues.Count -gt 0) {
    Write-Host ""
    Write-Host "仍需人工处理:" -ForegroundColor Red
    foreach ($i in $issues) { Write-Host "  - $i" -ForegroundColor Red }
    Write-Host ""
    Write-Host "环境未完全就绪，请先解决以上问题。" -ForegroundColor Red
} else {
    Write-Host ""
    Write-Host "✓ 环境检查全部通过，可以开始开发。" -ForegroundColor Green
}
Write-Host ""
