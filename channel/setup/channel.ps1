# Bootstrap the metachat channel into the current project:
#   irm http://anarchy:9000/setup/channel.ps1 | iex
#
# Fetches the channel code from metachat into ~/.metachat/channel, installs
# its dependencies, then hands off to setup/install.ts for the project side.
# Base tooling (bun, python3, git, claude) must already be installed.

# Everything lives in a scriptblock so a truncated download never runs half a script.
& {
    $ErrorActionPreference = 'Stop'

    # Filled in by metachat's web server with the URL this script came from.
    $baseUrl = if ($env:METACHAT_SETUP_URL) { $env:METACHAT_SETUP_URL } else { '__METACHAT_SETUP_URL__' }
    $homeDir = if ($env:METACHAT_HOME) { $env:METACHAT_HOME } else { Join-Path $HOME '.metachat' }
    $projectDir = (Get-Location).Path

    $missing = @('bun', 'python3', 'git', 'claude', 'tar') |
        Where-Object { -not (Get-Command $_ -ErrorAction SilentlyContinue) }
    if ($missing) {
        Write-Error "metachat setup needs these on PATH first: $($missing -join ', ')"
        return
    }

    Write-Host "Fetching channel from $baseUrl ..."
    New-Item -ItemType Directory -Force $homeDir | Out-Null
    $staging = Join-Path $homeDir "channel.new.$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    $archive = "$staging.tgz"
    $live = Join-Path $homeDir 'channel'
    $old = Join-Path $homeDir 'channel.old'

    try {
        New-Item -ItemType Directory $staging | Out-Null
        Invoke-WebRequest "$baseUrl/setup/channel.tgz" -OutFile $archive -UseBasicParsing
        tar -xzf $archive -C $staging
        if ($LASTEXITCODE) { throw "tar failed ($LASTEXITCODE)" }
        Push-Location $staging
        try {
            bun install --frozen-lockfile
            if ($LASTEXITCODE) { throw "bun install failed ($LASTEXITCODE)" }
        } finally { Pop-Location }

        # Swap the fresh copy in; a failed update leaves the old install alone.
        if (Test-Path $live) {
            if (Test-Path $old) { Remove-Item -Recurse -Force $old }
            Move-Item $live $old
        }
        Move-Item $staging $live
        if (Test-Path $old) { Remove-Item -Recurse -Force $old }
    } finally {
        if (Test-Path $archive) { Remove-Item -Force $archive }
        if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
    }

    bun run (Join-Path $live 'setup/install.ts') $projectDir
}
