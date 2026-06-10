# 提交前检查：lint + 格式 + 测试（-Fix 自动修复，-Types 附带 mypy）
param(
    [switch]$Fix,
    [switch]$Types
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if ($Fix) {
    uv run ruff check src tests --fix
    if ($LASTEXITCODE -ne 0) { exit 1 }
    uv run ruff format src tests
} else {
    uv run ruff check src tests
    if ($LASTEXITCODE -ne 0) { exit 1 }
    uv run ruff format src tests --check
}
if ($LASTEXITCODE -ne 0) { exit 1 }

if ($Types) {
    uv run mypy src
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

uv run pytest -q
exit $LASTEXITCODE
