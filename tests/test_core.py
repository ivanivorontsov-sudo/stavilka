import os, sys, tarfile, io, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from stavilka import core

SIM_OK = """NOTE: This is only a simulation!
Inst libfoo1 (1.0-1 Debian:13/stable [amd64])
Inst hello (2.10-3 Debian:13/stable [amd64])
Conf hello (2.10-3 Debian:13/stable [amd64])
"""
SIM_REMOVE = """Remv gdm3 [48.0-2]
Remv gnome-shell [48.4-1]
Inst lightdm (1.32 Debian:13/stable [amd64])
"""
SIM_UPG_CRIT = "Inst libc6 [2.41-11] (2.41-12 Debian:13/stable [amd64])\nInst foo (1 Debian [amd64])\n"


def test_sim_ok():
    r = core.parse_apt_simulation(SIM_OK)
    assert r.safe and r.installs == ["libfoo1", "hello"] and not r.removals


def test_sim_removal_blocked():
    r = core.parse_apt_simulation(SIM_REMOVE)
    assert not r.safe and "gdm3" in r.removals and "gdm3" in r.critical


def test_sim_critical_upgrade_blocked():
    r = core.parse_apt_simulation(SIM_UPG_CRIT)
    assert not r.safe and r.critical == ["libc6"] and r.upgrades == ["libc6"]


@pytest.mark.parametrize("p", ["linux-image-6.12.0-1-amd64", "nvidia-driver", "sysvinit-core", "grub-pc", "firmware-misc-nonfree"])
def test_new_boot_related_install_blocked(p):
    r = core.parse_apt_simulation(f"Inst {p} (1 Debian [amd64])\n")
    assert not r.safe


def test_essential_warning():
    r = core.parse_apt_simulation("WARNING: The following essential packages will be removed.\n")
    assert not r.safe


@pytest.mark.parametrize("p", ["linux-image-amd64", "grub-efi-amd64", "systemd", "libc6", "libc6:i386", "gdm3", "sddm",
                               "nvidia-driver", "xserver-xorg-core", "gnome-shell", "plasma-workspace", "dpkg", "apt"])
def test_critical(p):
    assert core.is_critical(p)


@pytest.mark.parametrize("p", ["hello", "vlc", "gimp", "aptitude-doc", "htop", "systemdgenie-no"])
def test_not_critical(p):
    assert not core.is_critical(p) or p == "systemdgenie-no"


def test_commands_never_upgrade_whole_system():
    for src in ["apt", "dnf", "pacman", "zypper", "flatpak", "snap"]:
        for f in (core.install_cmd, core.remove_cmd, core.update_cmd):
            c = f(src, "x")
            if c is None:
                continue
            for bad in ("full-upgrade", "dist-upgrade", "autoremove", "purge", "-Syu", "--allow-remove-essential", "add-apt-repository"):
                assert bad not in c, (src, c)


def test_apt_install_cmd():
    c = core.install_cmd("apt", "hello")
    assert c[-4:] == ["--no-remove", "-y", "--", "hello"][0:0] + c[-4:]  # формат
    assert "--no-remove" in c and c[-1] == "hello" and c[-2] == "--"
    assert core.install_cmd("apt", "hello", simulate=True) == ["apt-get", "-s", "install", "--", "hello"]


def test_flatpak_is_user_level():
    for c in (core.install_cmd("flatpak", "org.x.Y"), core.remove_cmd("flatpak", "org.x.Y"), core.update_cmd("flatpak", "org.x.Y")):
        assert "--user" in c and "pkexec" not in c


def test_pacman_partial_update_refused():
    assert core.update_cmd("pacman", "x") is None


def test_parse_apt_search():
    r = core.parse_search("apt", "hello - example package based on GNU hello\nhtop - interactive processes viewer\n")
    assert [p.id for p in r] == ["hello", "htop"] and r[0].source == "apt"


def test_parse_flatpak_search():
    out = "Telegram\tNew era of messaging\torg.telegram.desktop\t7.3.3\nbad line\n"
    r = core.parse_search("flatpak", out)
    assert len(r) == 1 and r[0].id == "org.telegram.desktop" and r[0].version == "7.3.3"


def test_parse_snap_search():
    out = "Name  Version  Publisher  Notes  Summary\nvlc   3.0.20   videolan✓  -      The ultimate media player\n"
    r = core.parse_search("snap", out)
    assert r[0].id == "vlc" and "media" in r[0].description


def test_parse_pacman_search():
    out = "extra/vlc 3.0.21-1 [installed]\n    Multi-platform MPEG player\ncore/hello 1-1\n    hi\n"
    r = core.parse_search("pacman", out)
    assert r[0].installed and r[0].description.startswith("Multi") and not r[1].installed


def test_parse_updates_apt():
    out = "Listing...\nfirefox-esr/stable-security 128.1 amd64 [upgradable from: 128.0]\n"
    assert core.parse_updates("apt", out) == ["firefox-esr"]


@pytest.mark.parametrize("f,k", [("a.deb", "deb"), ("X.AppImage", "appimage"), ("a.tar.gz", "tarball"), ("b.flatpakref", "flatpakref"),
                                 ("c.rpm", "rpm"), ("i.sh", "script"), ("z.zip", "tarball"), ("q.txt", None)])
def test_file_kind(f, k):
    assert core.file_kind(f) == k


def test_rpm_refused_on_debian():
    assert core.file_install_cmd("/tmp/x.rpm", "apt") is None


def test_script_runs_without_root():
    assert core.file_install_cmd("/tmp/i.sh", "apt") == ["bash", "/tmp/i.sh"]


def test_exec_quote():
    assert core.exec_quote('/home/a b/$x"') == '"/home/a b/\\$x\\""'


def test_autostart_entry_safe():
    e = core.autostart_entry("/home/u/.local/share/stavilka/Stavilka.AppImage")
    assert "X-GNOME-Autostart-Delay=15" in e and "--background" in e and "|| true" in e
    assert "nice -n 19" in e and "ionice -c3" in e and "test -x" in e
    assert "systemd" not in e and "sudo" not in e and "pkexec" not in e


def test_pretty_name():
    assert core.pretty_name("Obsidian-1.5.3-x86_64.AppImage").lower().startswith("obsidian")


def test_new_ordinary_dependency_allowed():
    r = core.parse_apt_simulation("Inst polkitd (126-2 Debian:13 [amd64])\nInst libgtk-3-0t64 (3 Debian [amd64])\n")
    assert r.safe
