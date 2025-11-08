Param(
  [string]$EnvName,
  [switch]$DryRun,
  [switch]$Force
)

function Assert-ToolInstalled($name) {
  $tool = Get-Command $name -ErrorAction SilentlyContinue
  if (-not $tool) {
    Write-Error "Required tool '$name' not found on PATH. Install it first (npm i -g @railway/cli) and login with 'railway login'."
    exit 2
  }
}

function Read-DotEnv($path) {
  $vars = @{}
  if (-not (Test-Path $path)) { return $vars }
  Get-Content $path | ForEach-Object {
    $line = $_.Trim()
    if ($line -match '^(#|;|$)') { return }
    # allow KEY=value or KEY="value"
    if ($line -match '^(?<k>[A-Za-z_][A-Za-z0-9_]*)=(?<v>.*)$') {
      $k = $Matches.k
      $v = $Matches.v
      # strip surrounding quotes if present
      if ($v -match '^".*"$' -or $v -match "^'.*'$") { $v = $v.Substring(1, $v.Length-2) }
      $vars[$k] = $v
    }
  }
  return $vars
}

function Read-RailwayVariables {
  # Fallback to parsing 'railway variables' output lines KEY=VALUE to avoid json format dependency
  $output = & railway variables 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to read Railway variables. Ensure you're logged in (railway login) and linked (railway link)."
    exit 2
  }
  $vars = @{}
  ($output -split "`n") | ForEach-Object {
    $line = $_.Trim()
    if ($line -match '^(?<k>[A-Za-z_][A-Za-z0-9_]*)=(?<v>.*)$') {
      $vars[$Matches.k] = $Matches.v
    }
  }
  return $vars
}

Write-Host "Synchronizing Railway environment variables with .env.example (.env values take precedence for secrets)..." -ForegroundColor Cyan

Assert-ToolInstalled "railway"

if ($EnvName) {
  Write-Host "Selecting Railway environment: $EnvName" -ForegroundColor Yellow
  & railway environment use $EnvName | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to select environment '$EnvName'"
    exit 2
  }
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
$examplePath = Join-Path $repoRoot ".env.example"
$localEnvPath = Join-Path $repoRoot ".env"

if (-not (Test-Path $examplePath)) {
  Write-Error ".env.example not found at $examplePath"
  exit 2
}

$exampleVars = Read-DotEnv $examplePath
$localVars   = Read-DotEnv $localEnvPath
$railwayVars = Read-RailwayVariables

# Desired keys are the keys present in .env.example
$desiredKeys = $exampleVars.Keys

# Known obsolete keys to remove safely
$obsolete = @(
  "CODE_ASSISTANT_ID",
  "GITHUB_TOKEN",
  "SUPABASE_DB_CONNECTION_STRING",
  "PGVECTOR_CONN_STR",
  "HUB_ASSISTANT_ID",
  "SEARCH_ASSISTANT_ID"
)

$sets = @()
$deletes = @()

foreach ($key in $desiredKeys) {
  # pick a value: prefer .env value, else example default if non-empty, else keep current
  $target = $null
  if ($localVars.ContainsKey($key) -and ($localVars[$key] -ne "")) {
    $target = $localVars[$key]
  } elseif ($exampleVars.ContainsKey($key) -and ($exampleVars[$key] -ne "")) {
    $target = $exampleVars[$key]
  } else {
    # leave as-is if exists remotely; skip if absent
    if ($railwayVars.ContainsKey($key)) { continue } else { continue }
  }

  if (-not $railwayVars.ContainsKey($key) -or $railwayVars[$key] -ne $target) {
    $sets += @{ key = $key; value = $target }
  }
}

# deletions: remove known obsolete keys; with -Force, also remove any railway-only keys not in example
foreach ($k in $railwayVars.Keys) {
  if ($obsolete -contains $k) { $deletes += $k; continue }
  if ($Force -and -not ($desiredKeys -contains $k)) { $deletes += $k }
}

Write-Host "Planned changes:" -ForegroundColor Cyan
Write-Host "  Set/Update: $($sets.Count)" -ForegroundColor Green
Write-Host "  Delete:     $($deletes.Count)" -ForegroundColor Red

if ($DryRun) {
  if ($sets.Count -gt 0) {
    Write-Host "\nWould set:" -ForegroundColor Green
    $sets | ForEach-Object { Write-Host ("  {0}={1}" -f $_.key, $_.value) }
  }
  if ($deletes.Count -gt 0) {
    Write-Host "\nWould delete:" -ForegroundColor Red
    $deletes | ForEach-Object { Write-Host ("  {0}" -f $_) }
  }
  exit 0
}

# Apply sets
foreach ($s in $sets) {
  Write-Host ("Setting {0}" -f $s.key) -ForegroundColor Green
              & railway variables --set "$($s.key)=$($s.value)" | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Warning "Failed to set $($s.key)" }
}

# Apply deletes
foreach ($k in $deletes) {
  Write-Host ("Deleting {0}" -f $k) -ForegroundColor Red
  & railway variables delete $k --yes | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Warning "Failed to delete $k" }
}

Write-Host "Done." -ForegroundColor Cyan
