"""Интеграция в рабочий стол. Пишет ТОЛЬКО в домашний каталог пользователя
(~/.local/share, ~/.config/autostart, ~/.config/Thunar/uca.xml между метками).
Каждый созданный файл записывается в манифест, чтобы удаление не оставляло следов."""
from __future__ import annotations
import json, os, re, shutil, subprocess
from pathlib import Path
from . import APP_ID, APP_NAME
from .core import MIME_TYPES, make_desktop_entry, autostart_entry, exec_quote

H = Path.home()
XDG_DATA = Path(os.environ.get("XDG_DATA_HOME") or H / ".local/share")
XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or H / ".config")
XDG_STATE = Path(os.environ.get("XDG_STATE_HOME") or H / ".local/state")
APPDIR = XDG_DATA / "stavilka"
STATE = XDG_STATE / "stavilka"
MANIFEST = APPDIR / "manifest.json"
SETTINGS = XDG_CONFIG / "stavilka" / "settings.json"
AUTOSTART = XDG_CONFIG / "autostart" / f"{APP_ID}.desktop"
FAILS = STATE / "autostart_fails"
INSTALLED_APPIMAGE = APPDIR / "Stavilka.AppImage"
LAUNCHER = APPDIR / "stavilka"
THUNAR_BEGIN = "<!-- stavilka-begin -->"
THUNAR_END = "<!-- stavilka-end -->"
MENU_LABEL = "Установить в Linux (Ставилка)"
EXTS = ["deb", "rpm", "AppImage", "appimage", "flatpakref", "flatpak", "sh", "run", "tar.gz", "tgz", "tar.xz", "tar.bz2", "tar.zst", "zip"]

DEFAULT_SETTINGS = {"autostart": True, "prefer_sandboxed": True, "timeshift_prompt": True, "integrated": False}


def load_settings():
    try:
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS.read_text())}
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(s):
    _mkdirs(SETTINGS.parent)
    SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=1))


def _manifest():
    try:
        return json.loads(MANIFEST.read_text())
    except Exception:
        return {"files": [], "apps": []}


def _save_manifest(m):
    if not APPDIR.exists():
        missing = []; d = APPDIR
        while not d.exists():
            missing.append(str(d)); d = d.parent
        APPDIR.mkdir(parents=True)
        m.setdefault("dirs", [])
        m["dirs"] += [x for x in reversed(missing) if x not in m["dirs"]]
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=1))


def _mkdirs(d: Path, track=True):
    missing = []
    while not d.exists():
        missing.append(d); d = d.parent
    for x in reversed(missing):
        x.mkdir()
    if track and missing:
        m = _manifest()
        m.setdefault("dirs", [])
        m["dirs"] += [str(x) for x in missing if str(x) not in m["dirs"]]
        _save_manifest(m)


def _write(path: Path, text: str, mode=0o644, track=True):
    _mkdirs(path.parent, track)
    path.write_text(text)
    os.chmod(path, mode)
    if track:
        m = _manifest()
        if str(path) not in m["files"]:
            m["files"].append(str(path))
        _save_manifest(m)


def current_executable():
    """Путь, по которому меню/автозапуск будут вызывать программу."""
    ai = os.environ.get("APPIMAGE")
    if ai:
        return str(LAUNCHER)
    if shutil.which("stavilka") and Path(shutil.which("stavilka")).resolve().parts[:2] == ("/", "usr"):
        return "/usr/bin/stavilka"
    return str(APPDIR / "stavilka-dev")


def _copy_self():
    ai = os.environ.get("APPIMAGE")
    if ai and Path(ai).resolve() != INSTALLED_APPIMAGE.resolve():
        _mkdirs(APPDIR)
        tmp = INSTALLED_APPIMAGE.with_suffix(".tmp")
        shutil.copy2(ai, tmp)
        os.chmod(tmp, 0o755)
        tmp.replace(INSTALLED_APPIMAGE)
    if ai:
        # обёртка: если FUSE нет, AppImage запускается через распаковку во временный каталог
        _write(LAUNCHER, "#!/bin/sh\n# Ставилка\nA=" + exec_quote(str(INSTALLED_APPIMAGE)) + "\n"
               "[ -x \"$A\" ] || exit 0\n"
               "if command -v fusermount3 >/dev/null 2>&1 || command -v fusermount >/dev/null 2>&1; then exec \"$A\" \"$@\"; fi\n"
               "APPIMAGE_EXTRACT_AND_RUN=1 exec \"$A\" \"$@\"\n", 0o755)
    elif not ai and not current_executable().startswith("/usr/"):
        # запуск из исходников: маленький лаунчер
        root = Path(__file__).resolve().parent.parent
        _write(APPDIR / "stavilka-dev", f"#!/bin/sh\nPYTHONPATH={exec_quote(str(root))} exec python3 -m stavilka \"$@\"\n", 0o755)


def has_nautilus_python():
    import glob
    return bool(glob.glob("/usr/lib/*/nautilus/extensions-4/libnautilus-python.so") or
                glob.glob("/usr/lib*/nautilus/extensions-4/libnautilus-python.so"))


def files_for(exe: str):
    """Возвращает {путь: (содержимое, режим)} для всех файлов интеграции."""
    q = exec_quote(exe)
    out = {}
    icon = "system-software-install"
    out[XDG_DATA / "applications" / f"{APP_ID}.desktop"] = (make_desktop_entry(
        APP_NAME, f"{q} %F", icon, "Установка любых программ для Linux",
        {"Categories": "System;PackageManager;", "MimeType": ";".join(MIME_TYPES) + ";",
         "Keywords": "install;apt;flatpak;appimage;deb;установка;программы;", "StartupWMClass": APP_ID}), 0o644)
    # Nautilus: если есть python3-nautilus — расширение, иначе пункт в «Сценарии»
    if not has_nautilus_python():
      out[XDG_DATA / "nautilus/scripts" / MENU_LABEL] = (
        f"#!/bin/sh\n# Ставилка\nexec {q} \"$@\"\n", 0o755)
    else:
      out[XDG_DATA / "nautilus-python/extensions/stavilka_nautilus.py"] = (NAUTILUS_EXT.replace("@EXE@", repr(exe)), 0o644)
    # Dolphin (KF5 и KF6)
    svc = ("[Desktop Entry]\nType=Service\nMimeType=" + ";".join(MIME_TYPES) + ";\n"
           "Actions=stavilkaInstall;\nX-KDE-ServiceTypes=KonqPopupMenu/Plugin\nX-Stavilka=true\n\n"
           f"[Desktop Action stavilkaInstall]\nName={MENU_LABEL}\nIcon={icon}\nExec={q} %F\n")
    out[XDG_DATA / "kio/servicemenus/stavilka.desktop"] = (svc, 0o755)  # KF6 требует +x
    out[XDG_DATA / "kservices5/ServiceMenus/stavilka.desktop"] = (svc, 0o644)
    # Nemo
    out[XDG_DATA / "nemo/actions/stavilka.nemo_action"] = (
        "[Nemo Action]\nActive=true\n"
        f"Name={MENU_LABEL}\nComment=Установить программу\nExec={q} %F\nIcon-Name={icon}\n"
        "Selection=notnone\nExtensions=" + ";".join(EXTS) + ";\n", 0o644)
    # Caja / file-manager-actions
    out[XDG_DATA / "file-manager/actions/stavilka.desktop"] = (
        "[Desktop Entry]\nType=Action\n"
        f"Name={MENU_LABEL}\nIcon={icon}\nProfiles=main;\n\n[X-Action-Profile main]\n"
        f"Exec={q} %F\nMimeTypes=" + ";".join(MIME_TYPES) + ";\n", 0o644)
    return out


def thunar_block(exe):
    pats = ";".join("*." + e for e in EXTS)
    return (f"{THUNAR_BEGIN}\n<action>\n\t<icon>system-software-install</icon>\n\t<name>{MENU_LABEL}</name>\n"
            f"\t<unique-id>stavilka-1</unique-id>\n\t<command>{exec_quote(exe)} %F</command>\n"
            f"\t<description>Ставилка</description>\n\t<patterns>{pats}</patterns>\n"
            "\t<other-files/>\n</action>\n" + THUNAR_END + "\n")


def _thunar_strip(text):
    return re.sub(re.escape(THUNAR_BEGIN) + r".*?" + re.escape(THUNAR_END) + r"\n?", "", text, flags=re.S)


def integrate_thunar(exe):
    uca = XDG_CONFIG / "Thunar/uca.xml"
    if not uca.parent.exists():
        return False
    text = uca.read_text() if uca.exists() else '<?xml version="1.0" encoding="UTF-8"?>\n<actions>\n</actions>\n'
    if not uca.exists():
        m = _manifest(); m.setdefault("created_uca", True); _save_manifest(m)
    else:
        bak = APPDIR / "uca.xml.bak"
        if not bak.exists():
            _mkdirs(APPDIR)
            shutil.copy2(uca, bak)
    text = _thunar_strip(text)
    text = text.replace("</actions>", thunar_block(exe) + "</actions>", 1)
    uca.write_text(text)
    return True


def set_autostart(on: bool, exe=None):
    exe = exe or current_executable()
    if on:
        _write(AUTOSTART, autostart_entry(exe))
    elif AUTOSTART.exists():
        AUTOSTART.unlink()
    s = load_settings(); s["autostart"] = on; save_settings(s)


def integrate(autostart=True):
    _copy_self()
    exe = current_executable()
    for p, (txt, mode) in files_for(exe).items():
        _write(p, txt, mode)
    integrate_thunar(exe)
    set_autostart(autostart, exe)
    s = load_settings(); s["integrated"] = True; save_settings(s)
    refresh_caches()
    return exe


def refresh_caches():
    for cmd in (["update-desktop-database", str(XDG_DATA / "applications")],):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def unintegrate(full=False):
    """Убирает всё, что создала Ставилка. full=True — ещё и настройки, журнал и копию AppImage.
    Программы, установленные через Ставилку, НЕ удаляются (их убирают отдельно)."""
    m = _manifest()
    files = list(m.get("files", []))
    if full:
        for a in m.get("apps", []):
            files += a.get("paths", [])
    for f in files:
        if Path(f).is_dir():
            shutil.rmtree(f, ignore_errors=True); continue
        try:
            Path(f).unlink()
        except FileNotFoundError:
            pass
    if AUTOSTART.exists():
        AUTOSTART.unlink()
    uca = XDG_CONFIG / "Thunar/uca.xml"
    if uca.exists():
        t = _thunar_strip(uca.read_text())
        if m.get("created_uca") and re.sub(r"\s", "", t) == '<?xmlversion="1.0"encoding="UTF-8"?><actions></actions>':
            uca.unlink()
        else:
            uca.write_text(t)
    m["files"] = []
    _save_manifest(m)
    if full:
        for d in (APPDIR, STATE, SETTINGS.parent):
            shutil.rmtree(d, ignore_errors=True)
        _cleanup_dirs(m.get("dirs", []) + [str(STATE.parent), str(SETTINGS.parent.parent)], only_created=m.get("dirs", []))
    else:
        s = load_settings(); s["integrated"] = False; s["autostart"] = False; save_settings(s)
    refresh_caches()


def _cleanup_dirs(dirs, only_created):
    """Удаляет пустые каталоги, которые создала сама Ставилка (самые глубокие — первыми)."""
    for d in sorted(set(only_created), key=lambda x: -x.count("/")):
        mc = Path(d) / "mimeinfo.cache"
        if mc.exists() and len(os.listdir(d)) == 1:
            mc.unlink()
        try:
            os.rmdir(d)
        except OSError:
            pass


# ---------- безопасный автозапуск ----------
def autostart_guard_begin():
    """Вызывается в начале --background. Возвращает False, если надо выйти
    (2 предыдущих автозапуска не завершились нормально → автозапуск отключается)."""
    _mkdirs(STATE)
    try:
        n = int(FAILS.read_text().strip() or 0)
    except Exception:
        n = 0
    if n >= 2:
        set_autostart(False)
        FAILS.write_text("0")
        (STATE / "safe_mode_triggered").write_text("1")
        return False
    FAILS.write_text(str(n + 1))
    return True


def autostart_guard_ok():
    FAILS.write_text("0")


NAUTILUS_EXT = r'''# Ставилка: пункт контекстного меню Nautilus (требует пакет python3-nautilus)
import subprocess
from urllib.parse import unquote, urlparse
from gi.repository import Nautilus, GObject
EXE = @EXE@
EXTS = (".deb", ".rpm", ".appimage", ".flatpakref", ".flatpak", ".sh", ".run", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar.zst", ".zip")

class StavilkaMenu(GObject.GObject, Nautilus.MenuProvider):
    def _act(self, item, files):
        paths = [unquote(urlparse(f.get_uri()).path) for f in files]
        subprocess.Popen([EXE] + paths, start_new_session=True)

    def get_file_items(self, *args):
        files = args[-1]
        if not files or not all(f.get_uri_scheme() == "file" and f.get_name().lower().endswith(EXTS) for f in files):
            return []
        item = Nautilus.MenuItem(name="Stavilka::install", label="Установить в Linux", icon="system-software-install")
        item.connect("activate", self._act, files)
        return [item]
'''
