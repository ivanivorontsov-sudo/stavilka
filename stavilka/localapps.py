"""Установка AppImage и архивов в домашний каталог — без root и без изменений системы."""
from __future__ import annotations
import configparser, json, os, re, shutil, stat, subprocess, tarfile, tempfile, zipfile
from pathlib import Path
from .core import safe_name, pretty_name, make_desktop_entry, exec_quote
from . import integration as I

APPS = I.APPDIR / "apps"
APPS_DESKTOP = I.XDG_DATA / "applications"
ICONS = I.APPDIR / "icons"


def _register(rec):
    m = I._manifest()
    m["apps"] = [a for a in m.get("apps", []) if a["id"] != rec["id"]] + [rec]
    I._save_manifest(m)


def list_local_apps():
    return I._manifest().get("apps", [])


def remove_local_app(app_id, log=print):
    m = I._manifest()
    for a in m.get("apps", []):
        if a["id"] == app_id:
            for p in a.get("paths", []):
                pp = Path(p)
                if pp.is_dir():
                    shutil.rmtree(pp, ignore_errors=True)
                elif pp.exists():
                    pp.unlink()
            log(f"Удалено: {a['name']}")
    m["apps"] = [a for a in m.get("apps", []) if a["id"] != app_id]
    I._save_manifest(m)
    I.refresh_caches()


def _read_desktop(path):
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
        return dict(cp["Desktop Entry"])
    except Exception:
        return {}


def _launcher(app_id, name, exec_line, icon, comment, categories="Utility;"):
    path = APPS_DESKTOP / f"stavilka-{app_id}.desktop"
    I._mkdirs(path.parent)
    path.write_text(make_desktop_entry(name, exec_line, icon, comment, {"Categories": categories}))
    return path


def install_appimage(src, log=print):
    src = Path(src).resolve()
    app_id = safe_name(src.name).lower()
    d = APPS / app_id
    I._mkdirs(d)
    dst = d / src.name
    shutil.copy2(src, dst)
    dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log(f"AppImage скопирован в {dst}")
    name, icon, comment, cats = pretty_name(src.name), "application-x-executable", "", "Utility;"
    with tempfile.TemporaryDirectory() as t:
        try:
            subprocess.run([str(dst), "--appimage-extract", "*.desktop"], cwd=t, timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([str(dst), "--appimage-extract", ".DirIcon"], cwd=t, timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            root = Path(t) / "squashfs-root"
            desks = list(root.glob("*.desktop"))
            if desks:
                e = _read_desktop(desks[0])
                name = e.get("Name[ru]") or e.get("Name") or name
                comment = e.get("Comment[ru]") or e.get("Comment", "")
                cats = e.get("Categories", cats)
            di = root / ".DirIcon"
            if di.exists():
                real = di.resolve() if di.is_symlink() else di
                if real.exists() and real.is_file():
                    I._mkdirs(ICONS)
                    ext = ".svg" if real.read_bytes()[:200].lstrip().startswith((b"<?xml", b"<svg")) else ".png"
                    ip = ICONS / f"{app_id}{ext}"
                    shutil.copy2(real, ip)
                    icon = str(ip)
        except Exception as ex:  # нет FUSE/нестандартный AppImage — обойдёмся без иконки
            log(f"Иконку извлечь не удалось ({ex}); ярлык будет со стандартной иконкой")
    lp = _launcher(app_id, name, exec_quote(str(dst)) + " %U", icon, comment, cats)
    paths = [str(d), str(lp)] + ([icon] if icon.startswith("/") else [])
    _register({"id": app_id, "name": name, "kind": "appimage", "paths": paths, "exec": str(dst)})
    I.refresh_caches()
    log(f"Готово: «{name}» добавлен в меню приложений")
    return app_id


def _safe_members(names):
    for n in names:
        if n.startswith("/") or ".." in Path(n).parts:
            raise ValueError(f"Небезопасный путь в архиве: {n}")


def extract_archive(src, dest):
    src = str(src)
    if src.lower().endswith(".zip"):
        with zipfile.ZipFile(src) as z:
            _safe_members(z.namelist())
            z.extractall(dest)
            for info in z.infolist():  # восстановить бит исполнения
                mode = info.external_attr >> 16
                if mode & 0o111:
                    p = Path(dest) / info.filename
                    if p.is_file():
                        p.chmod(p.stat().st_mode | 0o755)
    else:
        with tarfile.open(src) as t:
            _safe_members(t.getnames())
            try:
                t.extractall(dest, filter="data")
            except TypeError:
                t.extractall(dest)


def find_entry(root: Path, hint: str):
    """Ищет .desktop внутри или исполняемый файл, похожий на имя программы."""
    desks = [p for p in root.rglob("*.desktop") if p.is_file()]
    if desks:
        return ("desktop", desks[0])
    cands = []
    for p in root.rglob("*"):
        if p.is_file() and os.access(p, os.X_OK) and not p.suffix in (".so", ".sh", ".py", ".txt") and ".so." not in p.name:
            depth = len(p.relative_to(root).parts)
            score = depth * 10 - (50 if hint.lower().split(" ")[0] in p.name.lower() else 0) - (5 if p.parent.name == "bin" else 0)
            cands.append((score, p))
    if cands:
        return ("exe", sorted(cands, key=lambda x: (x[0], str(x[1])))[0][1])
    return (None, None)


def install_tarball(src, log=print):
    src = Path(src).resolve()
    app_id = safe_name(src.name).lower()
    d = APPS / app_id
    if d.exists():
        shutil.rmtree(d)
    I._mkdirs(d)
    log(f"Распаковка в {d} …")
    extract_archive(src, d)
    name = pretty_name(src.name)
    kind, entry = find_entry(d, name)
    paths = [str(d)]
    if kind == "desktop":
        e = _read_desktop(entry)
        ex = e.get("Exec", "")
        prog = ex.split()[0] if ex else ""
        local = next((p for p in d.rglob(Path(prog).name) if p.is_file()), None) if prog else None
        if local:
            ex = exec_quote(str(local)) + " " + " ".join(ex.split()[1:])
        icon = e.get("Icon", "application-x-executable")
        ic = next((p for p in d.rglob(icon + ".*")), None) if icon and not icon.startswith("/") else None
        lp = _launcher(app_id, e.get("Name[ru]") or e.get("Name") or name, ex.strip(), str(ic) if ic else icon,
                       e.get("Comment", ""), e.get("Categories", "Utility;"))
        paths.append(str(lp))
        log(f"Ярлык создан по {entry.name}")
    elif kind == "exe":
        lp = _launcher(app_id, name, exec_quote(str(entry)), "application-x-executable", f"Из архива {src.name}")
        paths.append(str(lp))
        log(f"Ярлык создан для {entry.relative_to(d)}")
    else:
        log("Исполняемый файл не найден: архив распакован, ярлык не создан (возможно, это исходники — их нужно собирать)")
    _register({"id": app_id, "name": name, "kind": "tarball", "paths": paths})
    I.refresh_caches()
    return app_id
