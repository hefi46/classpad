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
