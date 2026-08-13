param(
    [ValidatePattern('^[A-Za-z0-9._/-]+$')]
    [string]$Ref = "main",
    [switch]$NoUpdateShell
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$repoUrl = if ($env:AMPLIFIER_RUNTIME_REPO_URL) { $env:AMPLIFIER_RUNTIME_REPO_URL } else { "https://github.com/michaeljabbour/amplifier-runtime.git" }

function Fail([string]$Message) { throw "install failed: $Message" }
function Resolve-Uv {
    $command = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if ($command) { return $command.Path }
    Invoke-RestMethod -UseBasicParsing "https://astral.sh/uv/install.ps1" | Invoke-Expression
    $candidate = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    Fail "uv installed but uv.exe could not be found"
}
function Resolve-Commit([string]$RequestedRef) {
    if ($RequestedRef -match '^[0-9a-fA-F]{40}$') { return $RequestedRef.ToLowerInvariant() }
    $refs = & git ls-remote --exit-code $repoUrl "refs/heads/$RequestedRef" "refs/tags/$RequestedRef" "refs/tags/$RequestedRef^{}"
    if ($LASTEXITCODE -ne 0) { Fail "could not resolve '$RequestedRef'" }
    $rows = @($refs | ForEach-Object { $parts = $_ -split "`t", 2; if ($parts.Count -eq 2) { [pscustomobject]@{ Sha = $parts[0]; Name = $parts[1] } } })
    foreach ($target in @("refs/heads/$RequestedRef", "refs/tags/$RequestedRef^{}", "refs/tags/$RequestedRef")) {
        $match = $rows | Where-Object Name -eq $target | Select-Object -First 1
        if ($match -and $match.Sha -match '^[0-9a-fA-F]{40}$') { return $match.Sha.ToLowerInvariant() }
    }
    Fail "remote returned an invalid commit"
}

if ($repoUrl -notmatch '^https://') { Fail "repository URL must use https://" }
if ($repoUrl -match '^https://[^/]*@') { Fail "repository URL must not contain credentials" }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "Git for Windows is required" }
$uvBin = Resolve-Uv
$sha = Resolve-Commit $Ref
Write-Host "Installing Amplifier Runtime source commit $sha"
& $uvBin tool install --reinstall --no-config "git+$repoUrl@$sha"
if ($LASTEXITCODE -ne 0) { Fail "uv could not install Amplifier Runtime" }
$toolBin = (& $uvBin tool dir --bin).Trim()
$runtimeBin = Join-Path $toolBin "amplifier-runtime.exe"
if (-not (Test-Path -LiteralPath $runtimeBin -PathType Leaf)) { Fail "installation produced no amplifier-runtime.exe" }
$version = (& $runtimeBin --version).Trim()
if ($LASTEXITCODE -ne 0 -or -not $version) { Fail "runtime could not report its version" }
& $runtimeBin serve --help | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "runtime serve contract is unavailable" }
& $runtimeBin provider status --format json | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "runtime provider contract is unavailable" }
if (-not $NoUpdateShell) { & $uvBin tool update-shell | Out-Null }
Write-Host "Installed and verified $runtimeBin - $version"
