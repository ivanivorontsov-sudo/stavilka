import os, sys, importlib, tarfile, io, subprocess, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture
def mods(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for v in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "APPIMAGE"):
        monkeypatch.delenv(v, raising=False)
    import stavilka.integration as I, stavilka.localapps as L
    importlib.reload(I); importlib.reload(L)
    return I, L, tmp_path


def snapshot(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_integrate_and_full_uninstall_leaves_no_traces(mods):
    I, L, home = mods
    (home / ".config/Thunar").mkdir(parents=True)
    uca = home / ".config/Thunar/uca.xml"
    uca.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<actions>\n<action><name>Мой пункт</name></action>\n</actions>\n')
    orig = uca.read_text()
    before = snapshot(home)
    I.integrate(autostart=True)
    files = snapshot(home)
    assert ".config/autostart/ru.ivanivorontsov.Stavilka.desktop" in files
    assert ".local/share/kio/servicemenus/stavilka.desktop" in files
    assert ".local/share/nemo/actions/stavilka.nemo_action" in files
    assert ".local/share/file-manager/actions/stavilka.desktop" in files
    assert "stavilka-1" in uca.read_text() and "Мой пункт" in uca.read_text()
    # ничего вне домашнего каталога не пишется — проверяем, что все пути внутри HOME
    for f in I._manifest()["files"]:
        assert f.startswith(str(home))
    if shutil.which("desktop-file-validate"):
        for f in [".local/share/applications/ru.ivanivorontsov.Stavilka.desktop", ".config/autostart/ru.ivanivorontsov.Stavilka.desktop"]:
            r = subprocess.run(["desktop-file-validate", str(home / f)], capture_output=True, text=True)
            assert r.returncode == 0, r.stdout + r.stderr
    I.unintegrate(full=True)
    assert uca.read_text() == orig
    after = [p for p in snapshot(home) if not p.startswith(".local/share/applications/mimeinfo")]
    assert after == before


def test_autostart_safe_mode(mods):
    I, L, home = mods
    I.set_autostart(True, "/nonexistent/stavilka")
    assert I.AUTOSTART.exists()
    assert I.autostart_guard_begin()      # 1-й запуск «упал» (не вызвал ok)
    assert I.autostart_guard_begin()      # 2-й тоже
    assert not I.autostart_guard_begin()  # 3-й: защитный режим
    assert not I.AUTOSTART.exists()
    assert I.load_settings()["autostart"] is False


def test_autostart_guard_resets(mods):
    I, L, home = mods
    for _ in range(5):
        assert I.autostart_guard_begin(); I.autostart_guard_ok()


def test_tarball_install_and_remove(mods, tmp_path):
    I, L, home = mods
    src = tmp_path / "src"; (src / "myapp-1.0/bin").mkdir(parents=True)
    exe = src / "myapp-1.0/bin/myapp"; exe.write_text("#!/bin/sh\necho hi\n"); exe.chmod(0o755)
    tgz = tmp_path / "myapp-1.0.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(src / "myapp-1.0", arcname="myapp-1.0")
    app_id = L.install_tarball(tgz, log=lambda *_: None)
    d = home / ".local/share/applications" / f"stavilka-{app_id}.desktop"
    assert d.exists() and "bin/myapp" in d.read_text()
    L.remove_local_app(app_id, log=lambda *_: None)
    assert not d.exists() and not (L.APPS / app_id).exists()


def test_tarball_path_traversal_refused(mods, tmp_path):
    I, L, home = mods
    tgz = tmp_path / "evil.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        data = b"x"; ti = tarfile.TarInfo("../../evil"); ti.size = 1; t.addfile(ti, io.BytesIO(data))
    with pytest.raises(ValueError):
        L.install_tarball(tgz, log=lambda *_: None)
