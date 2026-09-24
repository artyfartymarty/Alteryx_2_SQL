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

    Live hardening Task L7 fix round 1 (R-c, R-d): the defaults below (-Context 262144, -Parallel 2,
    -CacheTypeK/-CacheTypeV q4_0) are the exact setup verified live on a 16 GB GPU, and they are the
    ONLY setup the committed orchestrator.config.json's `profiles.local.provider.maxPromptTokens`
    (100000) is sized against. Starting this script with its plain defaults -- the first .EXAMPLE
    below -- now matches that committed value; it did not before this fix round (the old default was
    -Context 32768, four times smaller than what 100000 needs). If you change -Context or -Parallel
    for a different GPU, change maxPromptTokens to match: this build reports (below, every run) the
    per-slot budget `n_ctx_slot = Context / Parallel`, and a maxPromptTokens that fits is about 77%
    of that (100000 / 131072 ≈ 76.3%) -- room for the output plus this build's own tokenizer-undercount
    margin (the wf_0007 live probe: a request reached 131095 tokens against a 131072 per-slot limit,
    with maxPromptTokens set to 120000 and ZERO compactions). Fix round 2 (L7-m4): the SDK's token
    estimate was undercounting by roughly 15-37% there, not merely "at least 9%" as fix round 1 first
    said -- see README §3 for the corrected arithmetic. 100000 is a conservative pull-back from
    120000 under that range, not a value proven safe by any single percentage.

.PARAMETER Model
    Path to the GGUF model file.

.PARAMETER Server
    Path to the llama-server executable (PrismML CUDA build).

.PARAMETER Port
    TCP port to listen on, loopback only.

.PARAMETER Context
    Context window size in tokens (-c). Default 262144 -- the verified 16 GB GPU setup; see
    .DESCRIPTION and README §3 for what this must stay paired with.

.PARAMETER Parallel
    Number of parallel request slots (-np), always passed explicitly (never omitted) -- default 2.
    Live hardening Task L7 (R1): the SDK runs a background context-compaction request alongside the
    main conversation, so at least 2 slots are needed even for one agent session at a time. On this
    build, -c 262144 -np 2 reports `n_slots=2, n_ctx_slot=131072, kv_unified=false`: passing -np AT
    ALL splits -Context evenly, a fixed, independent per-slot allocation, not one shared pool -- so
    the conversation and the concurrent compaction request each get their own budget instead of
    competing for one. Must be a positive integer (validated below): 0 or a negative value would
    make llama-server refuse to start, or (fix round 1 review, M1) worse, could be silently accepted
    by an older build.

    Live hardening fix round 1 (M1 correction): the original live overflow (task-L7-brief.md,
    "Context size has been exceeded" at n_tokens = 98490) happened at the default 4 slots -- but it
    is OMITTING -np, not "how many slots", that put this build into its unified, shared-cache mode
    (the build's own help text: "use single unified KV buffer … default: enabled if number of slots
    is auto"). An explicit `-np 4` would split 65536 per slot, same as `-np 2` splits 131072; this
    script always passes -np explicitly, so it is never in that auto/unified mode at any -Parallel
    value.

.PARAMETER CacheTypeK
    KV-cache quantization for K, passed through as llama-server's `-ctk`. Default q4_0 -- part of
    the verified 16 GB GPU setup (a 262144-token context does not fit that GPU at the server's own
    default, f16). Validated against a short allow-list of types this project has actually tried:
    f16, q8_0, q4_0.

.PARAMETER CacheTypeV
    KV-cache quantization for V, passed through as llama-server's `-ctv`. Default q4_0, same
    allow-list as -CacheTypeK.

.EXAMPLE
    pwsh -File scripts/dev/serve_model.ps1
    Starts the server with every default: the verified 16 GB GPU setup this build's committed
    orchestrator.config.json (`maxPromptTokens: 100000`) is sized against.

.EXAMPLE
    pwsh -File scripts/dev/serve_model.ps1 -Context 65536 -Parallel 2 -CacheTypeK q8_0 -CacheTypeV q8_0
    A smaller GPU: a quarter of the default context, at a less aggressive KV-cache quantization.
    n_ctx_slot becomes 32768; orchestrator.config.json's maxPromptTokens would need lowering to
    match (about 77% of 32768 ≈ 25000) -- the script's own printed suggestion says the exact number
    for whatever -Context/-Parallel you actually pass.
#>
[CmdletBinding()]
param(
    [string]$Model = (Join-Path $env:USERPROFILE "models\Ternary-Bonsai-2-27B\Ternary-Bonsai-2-27B-PQ2_0.gguf"),
    [string]$Server = (Join-Path $env:USERPROFILE "tools\llama-prism\llama-server.exe"),
    [int]$Port = 8080,
    [int]$Context = 262144,
    [int]$Parallel = 2,
    [string]$CacheTypeK = "q4_0",
    [string]$CacheTypeV = "q4_0"
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

# Fix round 1 review (M1): -Parallel was not validated at all -- 0, a negative value, or a value an
# older build reads as "auto" (unified, shared cache -- the exact mode this whole round exists to
# get out of) all went through silently. -np is always passed explicitly by this script (see
# .PARAMETER Parallel), so only a positive slot count is ever a legitimate value here.
if ($Parallel -le 0) {
    Write-Error "serve_model.ps1: invalid -Parallel '$Parallel' (must be a positive integer)"
    exit 1
}

# Fix round 1 (R-c): the per-slot budget this run's -Context/-Parallel actually gives the SDK, and a
# maxPromptTokens that fits it -- printed on every run, not just the default one, so a non-default
# -Context/-Parallel is never silently left unpaired with the committed config's own number.
$nCtxSlot = [math]::Floor($Context / $Parallel)
# Fix round 1 (R-d, wf_0007 live probe), reasoning corrected in fix round 2 (L7-m4): ~77%, not the
# ~90% the review's own I1 first suggested. 120000 against a 131072 slot reached 131095 tokens with
# ZERO compactions -- if the SDK's own background-compaction threshold (80% of maxPromptTokens) and
# blocking threshold (95%) are both relative to maxPromptTokens, as this repo assumes, a 131095-token
# request with no compaction means the SDK's own token estimate was under roughly 114000: at least
# ~15% below the real count, or, if compaction never even started, as much as ~37% below it. 100000
# is not PROVEN safe by that arithmetic alone -- it is a plausible, conservative pull-back from
# 120000, not a measured bound (its own ratio to n_ctx_slot is ~76.3%; 77% is used for the formula so
# the committed value itself does not print a false warning against its own rounding). The real check
# is `CopilotRunner`'s own log line (this run's actual maxPromptTokens/maxOutputTokens/model) together
# with `assistant.usage`'s peakInputTokens (recorded per role in a workflow's manifest.json,
# metrics.<role>.peakInputTokens) on the next long session: that ratio is the real number this
# script's 77% only estimates.
$suggestedMaxPromptTokens = [math]::Floor($nCtxSlot * 0.77)
Write-Host "serve_model.ps1: n_ctx_slot = Context / Parallel = $Context / $Parallel = $nCtxSlot"
Write-Host ("serve_model.ps1: a maxPromptTokens that fits this run: about $suggestedMaxPromptTokens " +
    "(~77% of n_ctx_slot -- room for the output and this build's own tokenizer-undercount margin, " +
    "task-L7-brief.md's wf_0007 probe)")

# Fix round 1 (R-c): "if the launcher can read orchestrator.config.json, warn when the configured
# value exceeds the per-slot fit; else say why not" -- every branch below prints something, so a
# silent script is never the answer to "did this get checked". Fix round 2 (L7-m3): the fit is
# $suggestedMaxPromptTokens (about 77% of n_ctx_slot), not n_ctx_slot itself -- comparing against the
# raw slot size let the pre-fix-round-1 committed value, 120000, print "fits within n_ctx_slot
# (131072)" even though the wf_0007 probe is exactly what proved 120000 overflows in practice.
$repoConfigPath = Join-Path $PSScriptRoot "..\..\orchestrator.config.json"
if (-not (Test-Path -LiteralPath $repoConfigPath -PathType Leaf)) {
    Write-Host "serve_model.ps1: orchestrator.config.json not found at $repoConfigPath -- cannot check its maxPromptTokens against the fit"
} else {
    try {
        $repoConfig = Get-Content -LiteralPath $repoConfigPath -Raw | ConvertFrom-Json
        $configuredMaxPromptTokens = $repoConfig.profiles.local.provider.maxPromptTokens
        if ($null -eq $configuredMaxPromptTokens) {
            Write-Host "serve_model.ps1: orchestrator.config.json sets no profiles.local.provider.maxPromptTokens -- nothing to check against the fit"
        } elseif ($configuredMaxPromptTokens -gt $suggestedMaxPromptTokens) {
            Write-Warning ("serve_model.ps1: orchestrator.config.json's profiles.local.provider.maxPromptTokens " +
                "($configuredMaxPromptTokens) EXCEEDS this run's own fit ($suggestedMaxPromptTokens, about 77% of " +
                "the $nCtxSlot-token per-slot budget) -- the wf_0007 probe found the SDK compacts too late to stay " +
                "under a per-slot limit at this margin. Lower -Context/-Parallel apart, or lower maxPromptTokens to fit.")
        } else {
            Write-Host "serve_model.ps1: orchestrator.config.json's maxPromptTokens ($configuredMaxPromptTokens) fits within this run's own fit ($suggestedMaxPromptTokens)"
        }
    } catch {
        # A malformed or unreadable config is not this script's job to diagnose -- orchestrate.ts's
        # own loadConfig already does that, loudly, the next time anyone runs it. Say why not and move on.
        Write-Host "serve_model.ps1: could not check orchestrator.config.json's maxPromptTokens against the fit: $($_.Exception.Message)"
    }
}

$serverArgs = @(
    "-m", $Model,
    "--host", "127.0.0.1",
    "--port", $Port,
    "-ngl", "99",
    "-fa", "on",
    "-c", $Context,
    "-np", $Parallel,
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
