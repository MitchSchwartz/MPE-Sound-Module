# Fix Factory Patches - Run as Administrator
# This script replaces the minimal factory patches in ProgramData with a junction to your full collection

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  Fix Surge XT Factory Patches" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

# Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "ERROR: This script must be run as Administrator!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Right-click this script and select 'Run as Administrator'" -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

$programDataFactory = "C:\ProgramData\Surge XT\patches_factory"
$gitRepoFactory = "C:\Users\mitch\GitHub\MPE Module\assets\patches\patches_factory"

# Step 1: Check if git repo factory patches exist
Write-Host "Step 1/3: Checking git repo factory patches..." -ForegroundColor White
if (-not (Test-Path $gitRepoFactory)) {
    Write-Host "ERROR: Git repo factory patches not found at:" -ForegroundColor Red
    Write-Host "  $gitRepoFactory" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

$patchCount = (Get-ChildItem -Path $gitRepoFactory -Recurse -Filter "*.fxp" | Measure-Object).Count
Write-Host "OK Found $patchCount patches in git repo" -ForegroundColor Green
Write-Host ""

# Step 2: Backup existing ProgramData factory patches
Write-Host "Step 2/3: Backing up existing factory patches..." -ForegroundColor White
if (Test-Path $programDataFactory) {
    $backupPath = "$programDataFactory.original"

    # Check if it's already a junction
    $item = Get-Item $programDataFactory -Force
    if ($item.LinkType -eq "Junction") {
        Write-Host "Already a junction - removing old junction" -ForegroundColor Yellow
        cmd /c rmdir "$programDataFactory"
    } else {
        # Real directory - back it up
        if (Test-Path $backupPath) {
            Write-Host "Backup already exists at $backupPath" -ForegroundColor Yellow
        } else {
            Rename-Item $programDataFactory $backupPath
            Write-Host "OK Backed up to patches_factory.original" -ForegroundColor Green
        }
    }
} else {
    Write-Host "No existing factory patches folder" -ForegroundColor Yellow
}
Write-Host ""

# Step 3: Create junction
Write-Host "Step 3/3: Creating junction to git repo..." -ForegroundColor White
$result = cmd /c mklink /J "$programDataFactory" "$gitRepoFactory" 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "OK Junction created successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Junction: $programDataFactory" -ForegroundColor White
    Write-Host "Target:   $gitRepoFactory" -ForegroundColor White
} else {
    Write-Host "ERROR: Failed to create junction" -ForegroundColor Red
    Write-Host $result -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  Success!" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Factory patches have been linked to your git repo." -ForegroundColor White
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Restart Surge XT" -ForegroundColor White
Write-Host "  2. All 639 factory patches should now appear!" -ForegroundColor White
Write-Host ""

Read-Host "Press Enter to exit"
