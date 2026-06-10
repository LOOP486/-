# 一键环境部署：uv -> 依赖 -> 本机配置模板
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "未检测到 uv，使用 winget 安装..." -ForegroundColor Yellow
    winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

Write-Host "同步依赖（uv sync）..." -ForegroundColor Cyan
uv sync

$pairs = @(
    @{ src = "config/models.example.yaml"; dst = "config/models.yaml" },
    @{ src = "config/agent.example.yaml";  dst = "config/agent.yaml" },
    @{ src = ".env.example";               dst = ".env" }
)
foreach ($pair in $pairs) {
    if (-not (Test-Path $pair.dst)) {
        Copy-Item $pair.src $pair.dst
        Write-Host "已生成 $($pair.dst)（请填写）" -ForegroundColor Yellow
    }
}

Write-Host "完成。下一步：填写 config/models.yaml 与 .env，然后运行" -ForegroundColor Green
Write-Host "  uv run ue5agent check-config"
