<#
.SYNOPSIS
    Windows/PowerShell counterpart to scripts/build-provisioning-usb.sh --
    preps an already-written Debian netinst USB stick for unattended
    classpad provisioning. See that script's header comment for the full
    design rationale (Rufus "ISO Image mode" assumption, why this isn't a
    rebuilt ISO, boot-parameter ordering, etc.) -- this file mirrors its
    steps exactly, just expressed in PowerShell for the WSL2/Windows server
    dev machine (see CLAUDE.md "Development Environment").

    Two things this version does differently from the bash script, and why:

    1. Repo content is staged via `git archive` + `Expand-Archive`, never
       `Copy-Item` from the working tree. A Windows checkout commonly has
       core.autocrlf=true, which would put CRLF into every .sh file --
       install.sh would then fail on the 11e with "bad interpreter:
       /bin/bash^M". `git archive` always emits the blob's real (LF)
       content regardless of the working tree's line-ending config.

    2. Every file this script *writes itself* (preseed.cfg, isolinux.cfg,
       grub.cfg) is written with [IO.File]::WriteAllText using explicit "`n"
       line endings, never Set-Content/Out-File (which write the platform
       default, CRLF, on Windows). CRLF in preseed.cfg would silently break
       the late_command's trailing-backslash line continuations (`\` +
       "`r`n" is not a continuation to d-i's parser); CRLF in isolinux.cfg
       is a similar risk for syslinux.

.PARAMETER TargetDrive
    Drive letter/path of the already-written USB stick, e.g. "E:\".

.PARAMETER PreseedName
    Name of the preseed under preseeds/<name>/preseed.cfg. Defaults to
    "classpad".

.EXAMPLE
    $env:CLASSPAD_USER_PASSWORD_CRYPTED = '$6$...'
    .\build-provisioning-usb.ps1 -TargetDrive E:\
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TargetDrive,

    [string]$PreseedName = "classpad"
)

$ErrorActionPreference = "Stop"

function Fail($Message) {
    Write-Error "build-provisioning-usb.ps1: $Message"
    exit 1
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Target = $TargetDrive.TrimEnd('\')

if (-not (Test-Path $Target)) {
    Fail "no such path: $Target"
}

$PreseedTemplate = Join-Path $RepoRoot "preseeds\$PreseedName\preseed.cfg"
if (-not (Test-Path $PreseedTemplate)) {
    Fail "no such preseed 'preseeds/$PreseedName/preseed.cfg'"
}

# See preseeds/classpad/preseed.cfg's header: the committed template must
# never carry a real crypted password, so it's substituted in at USB-write
# time from an env var instead. Generate one with: mkpasswd -m sha-512
# (Linux/WSL2 -- there's no Windows-native equivalent of mkpasswd).
$UserPasswordCrypted = $env:CLASSPAD_USER_PASSWORD_CRYPTED
if ([string]::IsNullOrEmpty($UserPasswordCrypted)) {
    Fail "`$env:CLASSPAD_USER_PASSWORD_CRYPTED is not set. Generate one with: mkpasswd -m sha-512 (in WSL2)"
}

foreach ($cmd in @("git")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Fail "required tool '$cmd' not found on PATH"
    }
}

# Reject a DD-mode (ISO9660/UDF, read-only) stick with a clear message
# rather than letting every write below fail confusingly one at a time.
$DriveLetter = $Target.TrimEnd(':')
$Volume = Get-Volume -DriveLetter $DriveLetter -ErrorAction SilentlyContinue
if (-not $Volume) {
    Fail "could not find a volume for drive $Target"
}
if ($Volume.FileSystemType -notin @("FAT32", "exFAT")) {
    Write-Host "build-provisioning-usb.ps1: $Target is $($Volume.FileSystemType), not FAT32/exFAT." -ForegroundColor Red
    Write-Host "  This looks like a stick written with Rufus 'DD Image mode' (read-only ISO9660)." -ForegroundColor Red
    Write-Host "  Re-write it with Rufus using 'ISO Image mode' instead, then re-run this script." -ForegroundColor Red
    exit 1
}

# Confirm this actually looks like a Debian installer tree before writing
# anything into it, and find the kernel/initrd path (install.amd on 11e's
# x86_64 target, but don't hardcode the arch directory name).
$IsolinuxCfg = Join-Path $Target "isolinux\isolinux.cfg"
if (-not (Test-Path $IsolinuxCfg)) {
    Fail "$IsolinuxCfg not found -- is this really a Debian netinst stick?"
}
$VmlinuzCandidates = @(Get-ChildItem -Path (Join-Path $Target "install.*\vmlinuz") -ErrorAction SilentlyContinue)
if ($VmlinuzCandidates.Count -eq 0) {
    Fail "no install.*\vmlinuz found under $Target"
} elseif ($VmlinuzCandidates.Count -gt 1) {
    Fail "multiple install.*\vmlinuz found under $Target, expected exactly one: $($VmlinuzCandidates.FullName -join ', ')"
}
$InstallDir = $VmlinuzCandidates[0].Directory.Name
$KernelPath = "/$InstallDir/vmlinuz"
$InitrdPath = "/$InstallDir/initrd.gz"

# Always LF, never CRLF -- see the file header comment for why.
function Write-LFText($Path, $Content) {
    $normalized = $Content -replace "`r`n", "`n"
    [IO.File]::WriteAllText($Path, $normalized, [Text.UTF8Encoding]::new($false))
}

Write-Host "== 1/4: Writing preseed.cfg (preseed=$PreseedName) =="
# .Replace(), not -replace: -replace's *replacement* argument interprets
# `$` specially ($1, $&, ... are capture-group references), and a
# sha-512-crypt hash looks like $6$salt$hash -- "$6" would be swallowed
# as a backreference instead of inserted literally. .Replace() is a plain
# literal string substitution with no such special characters.
$PreseedContent = (Get-Content -Raw $PreseedTemplate).Replace('@@USER_PASSWORD_CRYPTED@@', $UserPasswordCrypted)
Write-LFText (Join-Path $Target "preseed.cfg") $PreseedContent

Write-Host "== 2/4: Staging repo content (classpad-src/) =="
Push-Location $RepoRoot
try {
    $RepoCommit = (git rev-parse --short HEAD).Trim()
    git diff --quiet HEAD 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "working tree has uncommitted changes -- bundling committed HEAD ($RepoCommit) only, not your local edits"
    }
    $StagePath = Join-Path $Target "classpad-src"
    if (Test-Path $StagePath) { Remove-Item -Recurse -Force $StagePath }
    New-Item -ItemType Directory -Path $StagePath | Out-Null
    $ArchiveZip = Join-Path ([IO.Path]::GetTempPath()) "classpad-src-$([Guid]::NewGuid()).zip"
    git archive HEAD --format=zip -o $ArchiveZip
    Expand-Archive -Path $ArchiveZip -DestinationPath $StagePath -Force
    Remove-Item $ArchiveZip
} finally {
    Pop-Location
}

# Boot-parameter order matters: everything preseed-related must come
# before the `---` separator (params after it go to the installed
# system's own kernel cmdline, not the installer). file=/cdrom/preseed.cfg
# relies on d-i mounting the boot medium at /cdrom regardless of the
# underlying filesystem (FAT32 here, not actually a CD) -- this is
# standard d-i behaviour for any boot device, not specific to real optical
# media. Kept identical to the bash script's $AppendLine on purpose --
# see that script's own comment for why this can't just be a shared file
# (two different bootloader syntaxes, two different scripting languages).
$AppendLine = "auto=true priority=critical file=/cdrom/preseed.cfg vga=788 --- quiet"

Write-Host "== 3/4: Rewriting isolinux.cfg (BIOS boot) for auto-preseed =="
$IsolinuxOrig = Join-Path $Target "isolinux\isolinux.cfg.orig"
if (-not (Test-Path $IsolinuxOrig)) {
    Copy-Item $IsolinuxCfg $IsolinuxOrig
}
# prompt 1 + a real (not near-instant) timeout: this stick reformats
# whatever disk it boots against with zero further confirmation
# (partman/confirm_nooverwrite is preseeded true). If it's left inserted
# and the machine reboots with USB ahead of the internal disk in boot
# order -- either at the end of this same install, or any time later --
# it will silently re-provision itself. A near-zero timeout gives no
# realistic window to notice and interrupt; 10s at least gives a bench
# operator a chance. There is no substitute for actually removing the
# stick once the install starts -- see this script's final printed
# reminder.
$IsolinuxContent = @"
prompt 1
default classpad_auto
timeout 100
label classpad_auto
  menu label ^Automated Classpad install
  kernel $KernelPath
  append initrd=$InitrdPath $AppendLine
"@
Write-LFText $IsolinuxCfg $IsolinuxContent

Write-Host "== 4/4: Rewriting boot/grub/grub.cfg (UEFI boot) for auto-preseed =="
$GrubCfg = Join-Path $Target "boot\grub\grub.cfg"
if (Test-Path $GrubCfg) {
    $GrubOrig = Join-Path $Target "boot\grub\grub.cfg.orig"
    if (-not (Test-Path $GrubOrig)) {
        Copy-Item $GrubCfg $GrubOrig
    }
    $GrubContent = @"
set default=0
set timeout=10
menuentry 'Automated Classpad install' {
    linux  $KernelPath $AppendLine
    initrd $InitrdPath
}
"@
    Write-LFText $GrubCfg $GrubContent
} else {
    Write-Warning "no boot\grub\grub.cfg found -- UEFI boot won't auto-preseed (BIOS/isolinux path still does)"
}

# The stick's own md5sum.txt (from the original ISO) is now stale, since
# we just edited two of the files it covers. Harmless in this flow -- it
# only affects the installer's optional "Check disc for defects" menu
# item, which nothing here uses -- but left as-is rather than
# regenerated, so a future reader isn't puzzled by a missing step.
Write-Host "build-provisioning-usb.ps1: done. $Target\md5sum.txt is now stale (harmless -- see comment in this script)."
Write-Host "build-provisioning-usb.ps1: USB ready at $Target (preseed=$PreseedName, repo pinned at $RepoCommit)."
Write-Warning "remove the USB stick once the install starts booting."
Write-Warning "  This preseed reformats whatever disk it boots against with no further confirmation."
Write-Warning "  Leaving the stick in for the post-install reboot re-provisions the machine again."
