<#
.SYNOPSIS
    Starts the PrismML llama.cpp fork (llama-server) serving the local BYOK model on loopback
    only, with an OpenAI-compatible endpoint for the orchestrator's `local` profile.

.DESCRIPTION
    Task 16 (live BYOK smoke test): the orchestrator's `local` profile in orchestrator.config.json
    points at http://127.0.0.1:8080/v1. This script starts that server. It never binds to
    anything but 127.0.0.1, never downloads anything, and fails clearly (non-zero exit, no
    process started) if either the model file or the server executable is missing.

    Run it as a background process from the caller (e.g. `Start-Process` or a background shell
    job) and stop it when done — this script does not daemonize or detach itself. It runs
    llama-server in the foreground of whatever process invokes it.

.PARAMETER Model
    Path to the GGUF model file.

.PARAMETER Server
    Path to the llama-server executable (PrismML CUDA build).

.PARAMETER Port
    TCP port to listen on, loopback only.

.PARAMETER Context
    Context window size in tokens (-c).

.PARAMETER CacheTypeK
    Optional. KV-cache quantization for K, passed through as llama-server's `-ctk`. Not passed by
    default (server uses its own default, f16). Validated against a short allow-list of types this
    project has actually tried: f16, q8_0, q4_0.

.PARAMETER CacheTypeV
    Optional. KV-cache quantization for V, passed through as llama-server's `-ctv`. Same allow-list
    and default-not-passed behavior as -CacheTypeK.

.EXAMPLE
    pwsh -File scripts/dev/serve_model.ps1
    Starts the server with every default from the Task 16 brief.

.EXAMPLE
    pwsh -File scripts/dev/serve_model.ps1 -Context 65536 -CacheTypeK q8_0 -CacheTypeV q8_0
    Starts the server with a larger context window and a quantized KV cache (Task 16 live retest,
    larger-context experiment).
#>
[CmdletBinding()]
param(
    [string]$Model = (Join-Path $env:USERPROFILE "models\Ternary-Bonsai-2-27B\Ternary-Bonsai-2-27B-PQ2_0.gguf"),
    [string]$Server = (Join-Path $env:USERPROFILE "tools\llama-prism\llama-server.exe"),
    [int]$Port = 8080,
    [int]$Context = 32768,
    [string]$CacheTypeK = "",
    [string]$CacheTypeV = ""
)

$ErrorActionPreference = "Stop"

# Short, deliberate allow-list: only cache types this project has actually exercised. Extend it
# only after trying a new value and recording what happened, same as any other policy allow-list
# in this repo.
$AllowedCacheTypes = @("f16", "q8_0", "q4_0")

if (-not (Test-Path -LiteralPath $Model -PathType Leaf)) {
    Write-Error "serve_model.ps1: model file not found: $Model"
    exit 1
}

if (-not (Test-Path -LiteralPath $Server -PathType Leaf)) {
    Write-Error "serve_model.ps1: llama-server executable not found: $Server"
    exit 1
}

if ($CacheTypeK -and ($AllowedCacheTypes -notcontains $CacheTypeK)) {
    Write-Error "serve_model.ps1: invalid -CacheTypeK '$CacheTypeK' (allowed: $($AllowedCacheTypes -join ', '))"
    exit 1
}

if ($CacheTypeV -and ($AllowedCacheTypes -notcontains $CacheTypeV)) {
    Write-Error "serve_model.ps1: invalid -CacheTypeV '$CacheTypeV' (allowed: $($AllowedCacheTypes -join ', '))"
    exit 1
}

$serverArgs = @(
    "-m", $Model,
    "--host", "127.0.0.1",
    "--port", $Port,
    "-ngl", "99",
    "-fa", "on",
    "-c", $Context,
    "--jinja",
    "--alias", "ternary-bonsai-2-27b"
)

if ($CacheTypeK) {
    $serverArgs += @("-ctk", $CacheTypeK)
}

if ($CacheTypeV) {
    $serverArgs += @("-ctv", $CacheTypeV)
}

Write-Host "serve_model.ps1: starting llama-server on 127.0.0.1:$Port (loopback only)"
Write-Host "serve_model.ps1: model=$Model"
Write-Host "serve_model.ps1: server=$Server"
Write-Host "serve_model.ps1: $Server $($serverArgs -join ' ')"

& $Server @serverArgs
exit $LASTEXITCODE
