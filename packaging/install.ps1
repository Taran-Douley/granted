# Install Granted for this folder on Windows. Started by double-clicking
# "Install Granted (Windows).bat" in the Granted folder. Run it once on each
# computer that will use the folder; running it again is safe.
#
# The program and its libraries go in %LOCALAPPDATA%\Granted, not in this
# folder, so a folder synced through OneDrive or SharePoint stays small. The
# key goes there too: a shared folder is the wrong place for it.
#
# Unattended (for IT, or for testing): set GRANTED_INSTALL_YES=1 and
# GRANTED_INSTALL_NAME, plus ANTHROPIC_API_KEY, or AWS_ACCESS_KEY_ID and
# AWS_SECRET_ACCESS_KEY. GRANTED_INSTALL_ARCHIVE and GRANTED_INSTALL_SCHEDULE=1
# are optional.
#
# Plain ASCII on purpose: Windows PowerShell 5.1 reads a script without a
# byte-order mark in the local code page, and any other character garbles.

$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $PSScriptRoot
$App = Join-Path $PSScriptRoot 'app'
$Base = Join-Path $env:LOCALAPPDATA 'Granted'
$Venv = if ($env:GRANTED_VENV) { $env:GRANTED_VENV } else { Join-Path $Base 'venv' }
$Creds = if ($env:GRANTED_CREDENTIALS) { $env:GRANTED_CREDENTIALS } else { Join-Path $Base 'credentials.env' }
$Yes = [bool]$env:GRANTED_INSTALL_YES

function Say([string]$Text) { Write-Host ''; Write-Host "  $Text" }

function Ask([string]$Question, [string]$Default) {
    if ($Yes) { return $Default }
    $answer = Read-Host "  $Question"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer.Trim()
}

function Read-Secret([string]$Prompt) {
    $secure = Read-Host "  $Prompt" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Save-Credentials([string[]]$Lines) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Creds) | Out-Null
    # ASCII, not UTF8: Windows PowerShell writes UTF8 with a byte-order mark.
    Set-Content -Path $Creds -Value $Lines -Encoding ASCII
}

function Find-Python {
    # The "python" that ships with Windows is a shortcut to the Store that
    # exits with an error, so every candidate is asked for its version.
    foreach ($candidate in @(@('py', '-3'), @('python'), @('python3'))) {
        if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
        $rest = @($candidate | Select-Object -Skip 1)
        try {
            & $candidate[0] @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return ,$candidate }
        } catch { }
    }
    return $null
}

Write-Host ''
Write-Host "  Granted: setting up $Here"

# 1. Python 3.11 or newer --------------------------------------------------
$python = Find-Python
if (-not $python) {
    Say 'Granted needs Python 3.11 or newer, and this computer does not have it.'
    if ((-not $Yes) -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        $go = Ask 'Install Python 3.12 now? It takes a few minutes. [Y/n]' 'y'
        if ($go -notmatch '^[Nn]') {
            winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            Say 'Python is installed. Close this window, then double-click the installer again.'
            exit 0
        }
    }
    Say 'Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this installer again.'
    exit 1
}
$pyExe = $python[0]
$pyArgs = @($python | Select-Object -Skip 1)

# 2. How Granted reaches the AI model -------------------------------------
$provider = ''
if (Test-Path $Creds) {
    $keep = Ask 'This computer already has a key saved for Granted. Keep it? [Y/n]' 'y'
    if ($keep -notmatch '^[Nn]') { $provider = 'keep' }
}
if (-not $provider) {
    if ($env:ANTHROPIC_API_KEY) { $provider = 'anthropic' }
    elseif ($env:AWS_ACCESS_KEY_ID -and $env:AWS_SECRET_ACCESS_KEY) { $provider = 'bedrock' }
    elseif ($Yes) {
        Say 'GRANTED_INSTALL_YES needs ANTHROPIC_API_KEY, or AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.'
        exit 1
    }
    else {
        Say 'Granted uses an AI model to read funding calls and draft answers. How should it reach one?'
        Write-Host '    1  An Anthropic API key (simplest: console.anthropic.com)'
        Write-Host '    2  An AWS account with Amazon Bedrock'
        $choice = Ask 'Choose 1 or 2 [1]:' '1'
        if ($choice -eq '2') { $provider = 'bedrock' } else { $provider = 'anthropic' }
    }
}

# 3. The program, in its own environment ----------------------------------
Say 'Installing the program (a minute or two the first time)...'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Venv) | Out-Null
$venvPython = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    & $pyExe @pyArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Say 'Python could not create the environment Granted needs.'; exit 1 }
}
$extra = ''
if ($provider -eq 'anthropic' -or ($provider -eq 'keep' -and (Select-String -Path $Creds -Pattern '^GRANTED_PROVIDER=anthropic' -Quiet))) {
    $extra = '[anthropic]'
}
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet ($App + $extra)
if ($LASTEXITCODE -ne 0) { Say 'The install did not finish. Check the internet connection and run this again.'; exit 1 }

if ($provider -eq 'anthropic') {
    $key = $env:ANTHROPIC_API_KEY
    if (-not $key) { $key = Read-Secret 'Paste your Anthropic API key (it will not show)' }
    Save-Credentials @('# Granted: how this computer reaches the model. Keep this file private.',
                       'GRANTED_PROVIDER=anthropic', "ANTHROPIC_API_KEY=$key")
}
elseif ($provider -eq 'bedrock') {
    $accessKey = $env:AWS_ACCESS_KEY_ID
    $secretKey = $env:AWS_SECRET_ACCESS_KEY
    $region = if ($env:AWS_REGION) { $env:AWS_REGION } else { 'us-west-2' }
    if (-not ($accessKey -and $secretKey)) {
        $accessKey = Ask 'AWS access key id:' ''
        $secretKey = Read-Secret 'AWS secret access key (it will not show)'
        $region = Ask 'AWS region [us-west-2]:' 'us-west-2'
    }
    Save-Credentials @('# Granted: how this computer reaches the model. Keep this file private.',
                       'GRANTED_PROVIDER=bedrock', "AWS_ACCESS_KEY_ID=$accessKey",
                       "AWS_SECRET_ACCESS_KEY=$secretKey", "AWS_REGION=$region")
}

# 4. The organisation, and where its past applications are ----------------
$granted = Join-Path $Venv 'Scripts\granted.exe'
if (-not (Test-Path (Join-Path $Here 'granted.json'))) {
    $name = $env:GRANTED_INSTALL_NAME
    if (-not $name) { $name = Ask "Your organisation's name:" '' }
    if (-not $name) { Say 'An organisation name is needed.'; exit 1 }
    $archive = $env:GRANTED_INSTALL_ARCHIVE
    if (-not $archive -and -not $Yes) {
        Say "Where are your past funding applications? Press Enter to use the 'Past applications' folder in here, or paste a folder's path (in File Explorer: right-click the folder, then Copy as path)."
        $archive = (Ask 'Folder:' '').Trim('"')
    }
    $setup = @('setup', '--home', $Here, '--name', $name)
    if ($archive) { $setup += @('--archive', $archive) }
    $env:PYTHONUTF8 = '1'
    & $granted @setup
    if ($LASTEXITCODE -ne 0) { Say 'Setup did not finish; the message above says why.'; exit 1 }
}
else {
    Say 'This folder is already set up, so its settings and ORG.md were left as they are.'
}

# 5. A way to run it, and a daily schedule --------------------------------
# PYTHONUTF8: output goes to a log file, and without it Python writes that in
# the local code page and stops at the first character outside it.
$runNow = Join-Path $Here 'Run Granted now.bat'
Set-Content -Path $runNow -Encoding ASCII -Value @(
    '@echo off',
    'rem Run Granted for this folder now. The output is also kept in .granted\last-run.log',
    'cd /d "%~dp0"',
    'set PYTHONUTF8=1',
    "`"$granted`" run --home `"%~dp0.`" > `".granted\last-run.log`" 2>&1",
    'chcp 65001 >nul',
    'type ".granted\last-run.log"',
    'echo.',
    'echo Open Dashboard.html to see the result.',
    'pause'
)
$scheduled = Join-Path $PSScriptRoot 'scheduled-run.bat'
Set-Content -Path $scheduled -Encoding ASCII -Value @(
    '@echo off',
    'rem Started each morning by Windows Task Scheduler. Output: .granted\last-run.log',
    'cd /d "%~dp0.."',
    'set PYTHONUTF8=1',
    "`"$granted`" run --home `"%~dp0..`" > `"%~dp0last-run.log`" 2>&1"
)

$defaultSchedule = if ($Yes) { if ($env:GRANTED_INSTALL_SCHEDULE) { 'y' } else { 'n' } } else { 'y' }
$schedule = Ask 'Run Granted automatically every morning at 8? [Y/n]' $defaultSchedule
if ($schedule -match '^[Yy]') {
    $taskName = 'Granted - ' + (Split-Path -Leaf $Here)
    $action = New-ScheduledTaskAction -Execute $scheduled
    $trigger = New-ScheduledTaskTrigger -Daily -At 8am
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Runs Granted for $Here" -Force | Out-Null
    Say "Scheduled: every day at 08:00, or as soon as the computer is on after that. Task: $taskName"
}
else {
    Say "Not scheduled. To run it yourself, double-click 'Run Granted now.bat'."
}

Say 'Done. Open ORG.md (right-click, Open with, Notepad) and fill the gaps it lists.'
Say "Then double-click 'Run Granted now.bat', and open Dashboard.html to see what it found."
Write-Host ''
