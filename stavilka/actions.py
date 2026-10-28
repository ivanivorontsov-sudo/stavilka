"""Операции высокого уровня с проверками безопасности. UI передаёт log(str) и confirm(title, body, danger)->bool."""
from __future__ import annotations
import glob, os, shutil
from concurrent.futures import ThreadPoolExecutor
from . import core
from .runner import run_capture, run_stream
from . import localapps

SOURCE_LABELS = {"apt": "APT (Debian)", "dnf": "DNF", "pacman": "Pacman", "zypper": "Zypper",
                 "flatpak": "Flathub", "snap": "Snap", "appimage": "AppImage", "tarball": "Архив"}


class Backend:
    def __init__(self, which=shutil.which):
        self.which = which
        self.pm = core.detect_system_pm(which)
        self.sources = core.detect_sources(which)

    # ---- поиск ----
    def search(self, q, sources=None):
        sources = sources or self.sources
        if "flatpak" in sources:
            self.ensure_flathub()
        def one(src):
            rc, out, _ = run_capture(core.search_cmd(src, q), timeout=60)
            return core.parse_search(src, out) if rc == 0 or out else []
        with ThreadPoolExecutor(4) as ex:
            lists = list(ex.map(one, sources))
        inst = self.installed()
        res = []
        ql = q.lower()
        for lst in lists:
            for p in lst:
                p.installed = p.installed or (p.source, p.id) in inst
                res.append(p)
        # сортировка: точное совпадение, затем Flathub (песочница), затем остальное
        def key(p):
            n = p.name.lower(); i = p.id.lower()
            return (0 if ql in (n, i, i.split(".")[-1]) else 1 if n.startswith(ql) else 2,
                    0 if p.source == "flatpak" else 1, len(p.name))
        return sorted(res, key=key)[:300]

    def ensure_flathub(self):
        """Пользовательский remote Flathub + appstream (без root). Раз в сутки обновляет каталог."""
        import time
        from . import integration as I
        stamp = I.STATE / "appstream_stamp"
        if stamp.exists() and time.time() - stamp.stat().st_mtime < 86400:
            return
        run_capture(core.FLATHUB_SETUP_CMD)
        rc, _, _ = run_capture(["flatpak", "update", "--user", "--appstream", "flathub"], timeout=300)
        if rc == 0:
            I._mkdirs(I.STATE); stamp.touch()

    def installed(self):
        res = {}
        for src in self.sources:
            c = core.list_installed_cmd(src)
            if c:
                rc, out, _ = run_capture(c)
                for k, v in core.parse_installed(src, out).items():
                    res[(src, k)] = v
        return res

    def installed_apps(self):
        """Для вкладки «Установленные»: приложения (Flatpak, Snap, AppImage/архивы) + apt-пакеты c .desktop."""
        items = []
        inst = self.installed()
        for (src, k), p in inst.items():
            if src in ("flatpak", "snap"):
                items.append(p)
        if self.pm == "apt":
            gui = set()
            for f in glob.glob("/usr/share/applications/*.desktop"):
                rc, out, _ = run_capture(["dpkg-query", "-S", f], timeout=10)
                if rc == 0 and ":" in out:
                    gui.add(out.split(":")[0].strip())
            for n in sorted(gui):
                if ("apt", n) in inst:
                    items.append(inst[("apt", n)])
        for a in localapps.list_local_apps():
            items.append(core.Package(a["kind"], a["id"], a["name"], "в домашнем каталоге", "", True))
        return items

    def updates(self):
        res = []
        for src in self.sources:
            c = core.list_updates_cmd(src)
            if c:
                rc, out, _ = run_capture(c, timeout=120)
                res += [core.Package(src, n, n, "", "", True) for n in core.parse_updates(src, out)]
        return res

    # ---- безопасность ----
    def apt_simulate(self, cmd, log):
        rc, out, err = run_capture(cmd, timeout=300)
        if rc != 0:
            log(out + err)
            return None, (err or out).strip().splitlines()[-1:] or ["симуляция apt не удалась"]
        sim = core.parse_apt_simulation(out)
        log(f"Симуляция: новых пакетов {len(sim.installs)}, обновится {len(sim.upgrades)}, удалится {len(sim.removals)}")
        return sim, sim.problems_ru()

    def maybe_snapshot(self, what, log, confirm, settings):
        if settings.get("timeshift_prompt", True) and self.which("timeshift"):
            if confirm("Снимок системы", f"Перед «{what}» можно создать снимок Timeshift, чтобы откатиться при проблеме. Создать?", False):
                rc = run_stream(["pkexec", "timeshift", "--create", "--comments", f"Ставилка: перед {what}"], log)
                if rc != 0 and not confirm("Снимок не создан", "Timeshift вернул ошибку. Продолжить без снимка?", True):
                    return False
        return True

    def _system_gate(self, title, sim_cmd, log, confirm, settings):
        if self.pm == "apt" and sim_cmd:
            sim, problems = self.apt_simulate(sim_cmd, log)
            if sim is None:
                log("Отказ: " + "; ".join(problems))
                return False
            if problems:
                log("⛔ ОТКАЗ ПО БЕЗОПАСНОСТИ: " + "; ".join(problems))
                confirm("Установка заблокирована",
                        "Ставилка не будет выполнять эту операцию, потому что " + "; ".join(problems) +
                        ".\nЭто может сломать систему. Если она действительно нужна — сделайте её вручную в терминале.", True, only_ok=True)
                return False
            body = f"Это изменение системы (через {SOURCE_LABELS[self.pm]}, нужен пароль администратора).\n"
            if sim.installs:
                body += f"Будет установлено пакетов: {len(sim.installs)} ({', '.join(sim.installs[:12])}{' …' if len(sim.installs) > 12 else ''}).\n"
            if sim.upgrades:
                body += f"Будет обновлено: {', '.join(sim.upgrades[:12])}.\n"
            body += "Ничего не будет удалено."
            if not confirm(title, body, False):
                return False
        elif not confirm(title, "Это изменение системы, нужен пароль администратора. Продолжить?", False):
            return False
        return self.maybe_snapshot(title, log, confirm, settings)

    def _apt_sources_snapshot(self):
        return set(glob.glob("/etc/apt/sources.list.d/*")) | {"/etc/apt/sources.list"}

    def _after_system(self, before, log):
        new = self._apt_sources_snapshot() - before
        if new:
            log("⚠ Пакет добавил сторонние репозитории apt: " + ", ".join(sorted(new)) +
                ". Ставилка их не добавляла; при желании удалите эти файлы вручную.")

    # ---- операции ----
    def install(self, p, log, confirm, settings):
        if p.source == "flatpak":
            run_capture(core.FLATHUB_SETUP_CMD)
            return run_stream(core.install_cmd("flatpak", p.id), log) == 0
        sim = core.install_cmd(p.source, p.id, simulate=True) if p.source == "apt" else None
        if not self._system_gate(f"Установка {p.name}", sim, log, confirm, settings):
            return False
        before = self._apt_sources_snapshot()
        ok = run_stream(core.install_cmd(p.source, p.id), log) == 0
        self._after_system(before, log)
        return ok

    def remove(self, p, log, confirm, settings):
        if p.source in ("appimage", "tarball"):
            localapps.remove_local_app(p.id, log); return True
        if p.source == "flatpak":
            return run_stream(core.remove_cmd("flatpak", p.id), log) == 0
        bad = core.check_remove_target(p.id)
        if bad:
            log("⛔ " + bad); confirm("Удаление заблокировано", bad, True, only_ok=True); return False
        sim = core.remove_cmd(p.source, p.id, simulate=True) if p.source == "apt" else None
        if self.pm == "apt" and sim:
            s, _ = self.apt_simulate(sim, log)
            if s is None:
                return False
            others = [x for x in s.removals if x != p.id]
            crit = [x for x in s.removals if core.is_critical(x)]
            if crit or others:
                msg = ("вместе с ним удалились бы: " + ", ".join(others)) if others else ""
                if crit:
                    msg += "; среди них критически важные: " + ", ".join(crit)
                log("⛔ ОТКАЗ ПО БЕЗОПАСНОСТИ: " + msg)
                confirm("Удаление заблокировано", "Ставилка не удалит этот пакет: " + msg + ".", True, only_ok=True)
                return False
        if not confirm(f"Удаление {p.name}", "Это изменение системы. Удаляется только этот пакет (без autoremove). Продолжить?", True):
            return False
        return run_stream(core.remove_cmd(p.source, p.id), log) == 0

    def update(self, p, log, confirm, settings):
        if p.source == "flatpak":
            return run_stream(core.update_cmd("flatpak", p.id), log) == 0
        cmd = core.update_cmd(p.source, p.id)
        if not cmd:
            log("Для этого менеджера пакетов частичное обновление небезопасно — обновите систему штатными средствами.")
            return False
        sim = core.update_cmd(p.source, p.id, simulate=True) if p.source == "apt" else None
        if not self._system_gate(f"Обновление {p.name}", sim, log, confirm, settings):
            return False
        return run_stream(cmd, log) == 0

    def install_file(self, path, log, confirm, settings):
        k = core.file_kind(path)
        log(f"Файл: {path} (тип: {k or 'неизвестен'})")
        if k == "appimage":
            localapps.install_appimage(path, log); return True
        if k == "tarball":
            if not confirm("Установка из архива", f"Архив будет распакован в домашний каталог (~/.local/share/stavilka/apps), система не меняется. Продолжить?", False):
                return False
            localapps.install_tarball(path, log); return True
        if k in ("flatpakref", "flatpak-bundle"):
            if not self.which("flatpak"):
                log("Flatpak не установлен — откройте «Настройки → Подготовка»"); return False
            return run_stream(core.file_install_cmd(path, self.pm), log) == 0
        if k == "script":
            try:
                head = open(path, errors="replace").read(4000)
            except OSError as e:
                log(str(e)); return False
            if not confirm("Запуск скрипта", "Скрипт будет запущен от вашего имени (БЕЗ прав администратора) — если ему нужен root, он сам попросит.\n"
                           "Запускайте только скрипты из надёжных источников. Начало файла:\n\n" + head[:1500], True):
                return False
            return run_stream(["bash", os.path.abspath(path)], log) == 0
        if k == "deb":
            if self.pm != "apt":
                log(".deb можно ставить только в Debian/Ubuntu-системах"); return False
            if not self._system_gate(f"Установка {os.path.basename(path)}", core.deb_install_cmd(path, True), log, confirm, settings):
                return False
            before = self._apt_sources_snapshot()
            ok = run_stream(core.deb_install_cmd(path), log) == 0
            self._after_system(before, log)
            return ok
        if k == "rpm":
            cmd = core.file_install_cmd(path, self.pm)
            if not cmd:
                log("Пакеты .rpm предназначены для Fedora/openSUSE. В Debian их ставить небезопасно: поищите эту программу во Flathub, AppImage или .deb.")
                confirm(".rpm в Debian", "Ставилка не ставит .rpm в Debian (конвертация alien часто ломает зависимости). Поищите программу во Flathub или в виде .deb/AppImage.", False, only_ok=True)
                return False
            if not self._system_gate(f"Установка {os.path.basename(path)}", None, log, confirm, settings):
                return False
            return run_stream(cmd, log) == 0
        log("Этот тип файла не поддерживается"); return False

    def launch(self, p):
        import subprocess
        if p.source == "flatpak":
            cmd = ["flatpak", "run", p.id]
        elif p.source == "snap":
            cmd = ["snap", "run", p.id]
        elif p.source in ("appimage", "tarball"):
            d = localapps.APPS_DESKTOP / f"stavilka-{p.id}.desktop"
            cmd = ["gio", "launch", str(d)] if d.exists() else None
        else:
            ds = [f for f in glob.glob("/usr/share/applications/*.desktop")
                  if run_capture(["dpkg-query", "-S", f], 5)[1].startswith(p.id + ":")]
            cmd = ["gio", "launch", ds[0]] if ds else [p.id]
        if cmd:
            subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        return False

    def setup_flatpak(self, log, confirm, settings):
        if not self.which("flatpak"):
            if self.pm != "apt":
                log("Установите flatpak средствами вашего дистрибутива"); return False
            if not self.install(core.Package("apt", "flatpak", "Flatpak"), log, confirm, settings):
                return False
            self.sources = core.detect_sources(self.which)
        return run_stream(core.FLATHUB_SETUP_CMD, log) == 0
