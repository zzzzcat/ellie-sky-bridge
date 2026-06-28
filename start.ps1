param(
    [switch]$Live
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $projectRoot 'config.json'
$envPath = Join-Path $projectRoot '.env'

if (-not (Test-Path -LiteralPath $configPath)) {
    throw 'config.json is missing.'
}

if ((-not $env:OFOX_API_KEY) -and (Test-Path -LiteralPath $envPath)) {
    foreach ($line in Get-Content -LiteralPath $envPath) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) {
            continue
        }
        $parts = $trimmed.Split('=', 2)
        if ($parts.Count -eq 2 -and $parts[0].Trim() -eq 'OFOX_API_KEY') {
            $env:OFOX_API_KEY = $parts[1].Trim().Trim('"').Trim("'")
            break
        }
    }
}

if (-not $env:OFOX_API_KEY) {
    $secureKey = Read-Host 'OFOX API key' -AsSecureString
    $keyPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try {
        $env:OFOX_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPtr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPtr)
    }
}

if ($Live) {
    $config = Get-Content -Raw -LiteralPath $configPath -Encoding UTF8 | ConvertFrom-Json
    $config.safety.dry_run = $false
    $liveConfig = Join-Path $env:TEMP 'ellie-sky-live-config.json'
    $config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $liveConfig -Encoding UTF8
    python (Join-Path $projectRoot 'bridge.py') --config $liveConfig
}
else {
    python (Join-Path $projectRoot 'bridge.py') --config $configPath
}
