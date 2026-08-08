import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "build-provisioning-usb.sh"
REPO_ROOT = Path(__file__).parent.parent

VALID_PASSWORD = "$6$fakehash$notarealpasswordhash"


def make_fake_bin(path: Path, name: str, body: str) -> None:
    fake = path / name
    fake.write_text(f"#!/bin/bash\n{body}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def make_fake_findmnt(fakebin: Path, fstype: str) -> None:
    # Real findmnt takes --target <path>, prints just the fstype with -no FSTYPE.
    make_fake_bin(fakebin, "findmnt", f'echo "{fstype}"')


def make_usb_tree(root: Path, *, with_grub: bool = True) -> None:
    (root / "isolinux").mkdir(parents=True)
    (root / "isolinux" / "isolinux.cfg").write_text("default install\ntimeout 0\n")
    (root / "install.amd").mkdir()
    (root / "install.amd" / "vmlinuz").write_text("fake-kernel")
    (root / "install.amd" / "initrd.gz").write_text("fake-initrd")
    if with_grub:
        (root / "boot" / "grub").mkdir(parents=True)
        (root / "boot" / "grub" / "grub.cfg").write_text("menuentry 'Install' {}\n")


def run(target: Path, fakebin: Path, env_extra=None, args=None):
    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    env.setdefault("CLASSPAD_USER_PASSWORD_CRYPTED", VALID_PASSWORD)
    if env_extra:
        env.update(env_extra)
    argv = ["bash", str(SCRIPT), str(target)] + (args or [])
    return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)


def test_rejects_non_fat_filesystem(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "iso9660")
    usb = tmp_path / "usb"
    make_usb_tree(usb)

    result = run(usb, fakebin)

    assert result.returncode != 0
    assert "DD Image mode" in result.stderr
    assert not (usb / "preseed.cfg").exists()


def test_fails_when_password_env_missing(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb)

    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    env.pop("CLASSPAD_USER_PASSWORD_CRYPTED", None)
    result = subprocess.run(
        ["bash", str(SCRIPT), str(usb)], env=env, capture_output=True, text=True, timeout=60
    )

    assert result.returncode != 0
    assert "CLASSPAD_USER_PASSWORD_CRYPTED" in result.stderr
    assert not (usb / "preseed.cfg").exists()


def test_fails_when_isolinux_cfg_missing(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    usb.mkdir()

    result = run(usb, fakebin)

    assert result.returncode != 0
    assert "isolinux.cfg" in result.stderr


def test_fails_when_vmlinuz_missing(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    (usb / "isolinux").mkdir(parents=True)
    (usb / "isolinux" / "isolinux.cfg").write_text("default install\n")

    result = run(usb, fakebin)

    assert result.returncode != 0
    assert "vmlinuz" in result.stderr


def test_fails_when_vmlinuz_ambiguous(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb)
    (usb / "install.arm64").mkdir()
    (usb / "install.arm64" / "vmlinuz").write_text("fake-kernel-2")

    result = run(usb, fakebin)

    assert result.returncode != 0
    assert "multiple" in result.stderr


def test_writes_preseed_with_password_substituted(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb)

    result = run(usb, fakebin)

    assert result.returncode == 0, result.stderr
    preseed = (usb / "preseed.cfg").read_text()
    assert VALID_PASSWORD in preseed
    assert "@@USER_PASSWORD_CRYPTED@@" not in preseed


def test_writes_preseed_with_password_containing_slash(tmp_path):
    # Regression test: a sha-512-crypt hash's alphabet (./0-9A-Za-z$) can
    # legally contain "/", which broke an earlier version of this script
    # that used "/" as sed's own delimiter.
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb)
    slashy_password = "$6$abc/def$gh/ij.klMN0123456789/OP"

    result = run(usb, fakebin, env_extra={"CLASSPAD_USER_PASSWORD_CRYPTED": slashy_password})

    assert result.returncode == 0, result.stderr
    preseed = (usb / "preseed.cfg").read_text()
    assert slashy_password in preseed
    assert "@@USER_PASSWORD_CRYPTED@@" not in preseed


def test_hashes_plaintext_password_when_crypted_not_set(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb)

    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    env.pop("CLASSPAD_USER_PASSWORD_CRYPTED", None)
    env["CLASSPAD_USER_PASSWORD"] = "hunter2"
    result = subprocess.run(
        ["bash", str(SCRIPT), str(usb)], env=env, capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 0, result.stderr
    assert "hunter2" not in result.stdout
    assert "hunter2" not in result.stderr
    preseed = (usb / "preseed.cfg").read_text()
    assert "hunter2" not in preseed
    assert "$6$" in preseed  # SHA-512 crypt, same format as CLASSPAD_USER_PASSWORD_CRYPTED


def test_fails_when_neither_password_var_set(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb)

    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    env.pop("CLASSPAD_USER_PASSWORD_CRYPTED", None)
    env.pop("CLASSPAD_USER_PASSWORD", None)
    result = subprocess.run(
        ["bash", str(SCRIPT), str(usb)], env=env, capture_output=True, text=True, timeout=60
    )

    assert result.returncode != 0
    assert "CLASSPAD_USER_PASSWORD" in result.stderr
    assert not (usb / "preseed.cfg").exists()


def test_accepts_wsl_drvfs_mount_reporting_fat32(tmp_path):
    # Regression test: under WSL2, a USB stick mounted in Windows (e.g. as
    # E:) is reachable at /mnt/e via DrvFs, but findmnt reports the mount
    # itself as fstype "9p" no matter what's actually on the drive. The
    # script must ask Windows (via a fake powershell.exe here) for the
    # real filesystem instead of rejecting a good FAT32 stick outright.
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "9p")
    make_fake_bin(fakebin, "powershell.exe", 'echo "FAT32"')
    mnt_root = tmp_path / "mnt"
    usb = mnt_root / "e"
    make_usb_tree(usb)

    result = run(usb, fakebin)

    assert result.returncode == 0, result.stderr
    assert (usb / "preseed.cfg").exists()


def test_rejects_wsl_drvfs_mount_reporting_ntfs(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "9p")
    make_fake_bin(fakebin, "powershell.exe", 'echo "NTFS"')
    mnt_root = tmp_path / "mnt"
    usb = mnt_root / "e"
    make_usb_tree(usb)

    result = run(usb, fakebin)

    assert result.returncode != 0
    assert "NTFS" in result.stderr
    assert not (usb / "preseed.cfg").exists()


def test_stages_repo_source(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb)

    result = run(usb, fakebin)

    assert result.returncode == 0, result.stderr
    assert (usb / "classpad-src" / "scripts" / "install.sh").exists()
    assert (usb / "classpad-src" / "CLAUDE.md").exists()


def test_rewrites_isolinux_cfg_for_auto_preseed_and_backs_up_original(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb)
    original = (usb / "isolinux" / "isolinux.cfg").read_text()

    result = run(usb, fakebin)

    assert result.returncode == 0, result.stderr
    new_cfg = (usb / "isolinux" / "isolinux.cfg").read_text()
    assert "classpad_auto" in new_cfg
    assert "auto=true priority=critical file=/cdrom/preseed.cfg" in new_cfg
    assert "/install.amd/vmlinuz" in new_cfg
    assert "---" in new_cfg
    assert (usb / "isolinux" / "isolinux.cfg.orig").read_text() == original


def test_rewrites_grub_cfg_for_auto_preseed_and_backs_up_original(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb)
    original = (usb / "boot" / "grub" / "grub.cfg").read_text()

    result = run(usb, fakebin)

    assert result.returncode == 0, result.stderr
    new_cfg = (usb / "boot" / "grub" / "grub.cfg").read_text()
    assert "auto=true priority=critical file=/cdrom/preseed.cfg" in new_cfg
    assert "initrd /install.amd/initrd.gz" in new_cfg
    assert (usb / "boot" / "grub" / "grub.cfg.orig").read_text() == original


def test_warns_but_succeeds_when_grub_cfg_absent(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb, with_grub=False)

    result = run(usb, fakebin)

    assert result.returncode == 0, result.stderr
    assert "no boot/grub/grub.cfg found" in result.stderr
    assert (usb / "preseed.cfg").exists()


def test_rerunning_does_not_overwrite_the_orig_backup(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb)

    assert run(usb, fakebin).returncode == 0
    first_orig = (usb / "isolinux" / "isolinux.cfg.orig").read_text()
    # Second run should be a no-op on the already-rewritten cfg -- the
    # .orig it backs up should still be the *original* installer cfg, not
    # the classpad_auto version from the first run.
    assert run(usb, fakebin).returncode == 0
    second_orig = (usb / "isolinux" / "isolinux.cfg.orig").read_text()
    assert first_orig == second_orig
    assert "classpad_auto" not in second_orig


def test_verification_step_reports_all_checks_pass(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb)

    result = run(usb, fakebin)

    assert result.returncode == 0, result.stderr
    assert "== 5/5: Verifying written files ==" in result.stdout
    assert "[FAIL]" not in result.stdout and "[FAIL]" not in result.stderr
    assert result.stdout.count("[OK]") >= 12  # 8 always-run checks + 4 grub checks (grub.cfg present here)
    assert "verification passed (" in result.stdout
    # The write steps (1-4) must run before verification (5) reports on them.
    assert result.stdout.index("== 5/5") > result.stdout.index("== 4/5")


def test_verification_skips_grub_checks_when_grub_cfg_absent(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb, with_grub=False)

    result = run(usb, fakebin)

    assert result.returncode == 0, result.stderr
    assert "grub.cfg:" not in result.stdout
    assert "[FAIL]" not in result.stdout and "[FAIL]" not in result.stderr
    assert "verification passed (" in result.stdout


def test_verification_failure_blocks_the_ready_message_and_exits_nonzero(tmp_path):
    # Simulate a write step silently producing wrong content -- e.g. sed
    # matching but a stray shell quoting bug swallowing the password --
    # by pre-seeding a preseed template that can never satisfy the
    # "placeholder was substituted" check (it has no placeholder to begin
    # with, so grep -qF finds the literal @@USER_PASSWORD_CRYPTED@@
    # sentinel is never present, and file_lacks alone won't catch it, but
    # the substitution then also never inserts the crypted password --
    # so the "crypted password hash present" check fails instead).
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_findmnt(fakebin, "vfat")
    usb = tmp_path / "usb"
    make_usb_tree(usb)

    preseeds_dir = REPO_ROOT / "preseeds" / "_test_broken"
    preseeds_dir.mkdir(exist_ok=True)
    (preseeds_dir / "preseed.cfg").write_text("d-i passwd/user-password-crypted password NOT_A_PLACEHOLDER\n")
    try:
        result = run(usb, fakebin, args=["_test_broken"])

        assert result.returncode != 0
        assert "[FAIL]" in result.stderr
        assert "crypted password hash present" in result.stderr
        assert "verification FAILED" in result.stderr
        assert "USB stick is NOT ready" in result.stderr
        # The failure summary must come before -- and instead of -- the
        # normal success/ready messages.
        assert "USB ready at" not in result.stdout
    finally:
        (preseeds_dir / "preseed.cfg").unlink()
        preseeds_dir.rmdir()


def test_findmnt_dash_f_used_to_avoid_stacked_mount_multiline_bug(tmp_path):
    # Regression test: under WSL2, a stale DrvFs mount left behind after a
    # drive is reattached, with a fresh one stacked on top by a manual
    # remount, makes plain `findmnt --target` print one line per stacked
    # mount -- turning $FSTYPE into a multi-line string that breaks every
    # comparison against it. The script must pass -f/--first-only to
    # collapse this to a single match. This fake findmnt simulates exactly
    # that: it prints two lines unless -f is present on argv.
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_bin(
        fakebin,
        "findmnt",
        'if [[ "$*" == *-f* ]]; then echo "vfat"; else printf "vfat\\nvfat\\n"; fi',
    )
    usb = tmp_path / "usb"
    make_usb_tree(usb)

    result = run(usb, fakebin)

    assert result.returncode == 0, result.stderr
    assert (usb / "preseed.cfg").exists()
