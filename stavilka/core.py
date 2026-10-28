"""Чистая логика Ставилки: построение команд, разбор вывода, проверки безопасности.
Ничего здесь не выполняет команды и не пишет вне ~/.local / ~/.config."""
from __future__ import annotations
import os, re, shlex, shutil
from dataclasses import dataclass, field

# ---------- пакетные менеджеры ----------
SYSTEM_PMS = ["apt", "dnf", "pacman", "zypper"]
PM_BINARIES = {"apt": "apt-get", "dnf": "dnf", "pacman": "pacman", "zypper": "zypper"}


def detect_system_pm(which=shutil.which):
    for pm in SYSTEM_PMS:
        if which(PM_BINARIES[pm]):
            return pm
    return None


def detect_sources(which=shutil.which):
    src = []
    pm = detect_system_pm(which)
    if pm:
        src.append(pm)
    if which("flatpak"):
        src.append("flatpak")
    if which("snap"):
        src.append("snap")
    return src


@dataclass
class Package:
    source: str
    id: str
    name: str
    description: str = ""
    version: str = ""
    installed: bool = False

    @property
    def system_change(self) -> bool:
        return self.source in SYSTEM_PMS or self.source == "snap"


ROOT = os.environ.get("STAVILKA_ROOT_CMD", "pkexec").split()  # переменная только для тестов
APT_ENV = ["env", "DEBIAN_FRONTEND=noninteractive", "APT_LISTCHANGES_FRONTEND=none"]
FLATHUB_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"


def search_cmd(source, q):
    return {
        "apt": ["apt-cache", "search", "--", q],
        "dnf": ["dnf", "-q", "search", q],
        "pacman": ["pacman", "-Ss", q],
        "zypper": ["zypper", "-q", "--no-refresh", "search", q],
        "flatpak": ["flatpak", "search", "--columns=name,description,application,version", q],
        "snap": ["snap", "find", q],
    }[source]


def install_cmd(source, pid, simulate=False):
    if source == "apt":
        return (["apt-get", "-s", "install", "--", pid] if simulate else
                ROOT + APT_ENV + ["apt-get", "install", "-y", "--no-remove", "--", pid])
    if source == "dnf":
        return ["dnf", "install", "--assumeno", pid] if simulate else ROOT + ["dnf", "install", "-y", pid]
    if source == "pacman":
        # -S без -u: никакого полного обновления системы
        return ["pacman", "-Sp", "--print-format", "%n", pid] if simulate else ROOT + ["pacman", "-S", "--needed", "--noconfirm", pid]
    if source == "zypper":
        return ROOT + ["zypper", "--non-interactive", "install", "--dry-run", pid] if simulate else ROOT + ["zypper", "--non-interactive", "install", pid]
    if source == "flatpak":
        return ["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", pid]
    if source == "snap":
        return ROOT + ["snap", "install", pid]
    raise ValueError(source)


def remove_cmd(source, pid, simulate=False):
    if source == "apt":
        # remove, НЕ purge и НЕ autoremove
        return ["apt-get", "-s", "remove", "--", pid] if simulate else ROOT + APT_ENV + ["apt-get", "remove", "-y", "--", pid]
    if source == "dnf":
        return ROOT + ["dnf", "remove", "-y", "--noautoremove", pid]
    if source == "pacman":
        return ROOT + ["pacman", "-R", "--noconfirm", pid]
    if source == "zypper":
        return ROOT + ["zypper", "--non-interactive", "remove", "--no-clean-deps", pid]
    if source == "flatpak":
        return ["flatpak", "uninstall", "--user", "-y", "--noninteractive", pid]
    if source == "snap":
        return ROOT + ["snap", "remove", pid]
    raise ValueError(source)


def update_cmd(source, pid, simulate=False):
    """Обновление ОДНОГО пакета. full-upgrade/dist-upgrade/-Syu не используются никогда."""
    if source == "apt":
        return (["apt-get", "-s", "install", "--only-upgrade", "--", pid] if simulate else
                ROOT + APT_ENV + ["apt-get", "install", "-y", "--only-upgrade", "--no-remove", "--", pid])
    if source == "flatpak":
        return ["flatpak", "update", "--user", "-y", "--noninteractive", pid]
    if source == "snap":
        return ROOT + ["snap", "refresh", pid]
    if source == "dnf":
        return ROOT + ["dnf", "upgrade", "-y", pid]
    if source == "zypper":
        return ROOT + ["zypper", "--non-interactive", "update", pid]
    if source == "pacman":
        return None  # частичные обновления в Arch опасны — не предлагаем
    raise ValueError(source)


def list_updates_cmd(source):
    return {
        "apt": ["apt", "list", "--upgradable"],
        "flatpak": ["flatpak", "remote-ls", "--user", "--updates", "--columns=application,version"],
        "snap": ["snap", "refresh", "--list"],
        "dnf": ["dnf", "-q", "check-update"],
    }.get(source)


def list_installed_cmd(source):
    return {
        "apt": ["dpkg-query", "-W", "-f", "${Package}\t${Version}\t${binary:Summary}\n"],
        "flatpak": ["flatpak", "list", "--user", "--app", "--columns=application,name,version"],
        "snap": ["snap", "list"],
        "dnf": ["dnf", "-q", "list", "--installed"],
        "pacman": ["pacman", "-Q"],
    }.get(source)


FLATHUB_SETUP_CMD = ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB_URL]

# ---------- разбор вывода ----------

def parse_search(source, out):
    res = []
    if source == "apt":
        for line in out.splitlines():
            if " - " in line:
                n, d = line.split(" - ", 1)
                res.append(Package("apt", n.strip(), n.strip(), d.strip()))
    elif source == "flatpak":
        for line in out.splitlines():
            p = line.split("\t")
            if len(p) >= 3 and "." in p[2]:
                res.append(Package("flatpak", p[2].strip(), p[0].strip(), p[1].strip(), p[3].strip() if len(p) > 3 else ""))
    elif source == "snap":
        for line in out.splitlines()[1:]:
            p = line.split(None, 4)
            if len(p) >= 2:
                res.append(Package("snap", p[0], p[0], p[4] if len(p) > 4 else "", p[1]))
    elif source == "pacman":
        lines = out.splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"^(\S+)/(\S+) (\S+)(.*)$", line)
            if m:
                d = lines[i + 1].strip() if i + 1 < len(lines) else ""
                res.append(Package("pacman", m.group(2), m.group(2), d, m.group(3), "[installed" in m.group(4)))
    elif source == "dnf":
        for line in out.splitlines():
            m = re.match(r"^\s*(\S+?)\.(\w+)\s+:\s+(.*)$", line)
            if m:
                res.append(Package("dnf", m.group(1), m.group(1), m.group(3)))
    elif source == "zypper":
        for line in out.splitlines():
            p = [x.strip() for x in line.split("|")]
            if len(p) >= 4 and p[1] and p[1] != "Name" and not set(p[1]) <= set("-+"):
                res.append(Package("zypper", p[1], p[1], p[2], installed=p[0].startswith("i")))
    return res


def parse_installed(source, out):
    res = {}
    if source == "apt":
        for line in out.splitlines():
            p = line.split("\t")
            if len(p) >= 2:
                res[p[0]] = Package("apt", p[0], p[0], p[2] if len(p) > 2 else "", p[1], True)
    elif source == "flatpak":
        for line in out.splitlines():
            p = line.split("\t")
            if len(p) >= 2:
                res[p[0]] = Package("flatpak", p[0], p[1], "", p[2] if len(p) > 2 else "", True)
    elif source == "snap":
        for line in out.splitlines()[1:]:
            p = line.split()
            if len(p) >= 2:
                res[p[0]] = Package("snap", p[0], p[0], "", p[1], True)
    elif source == "pacman":
        for line in out.splitlines():
            p = line.split()
            if len(p) == 2:
                res[p[0]] = Package("pacman", p[0], p[0], "", p[1], True)
    return res


def parse_updates(source, out):
    names = []
    if source == "apt":
        for line in out.splitlines():
            m = re.match(r"^([^/\s]+)/\S+\s+(\S+)", line)
            if m:
                names.append(m.group(1))
    elif source == "flatpak":
        names = [l.split("\t")[0] for l in out.splitlines() if l.strip()]
    elif source in ("snap",):
        names = [l.split()[0] for l in out.splitlines()[1:] if l.strip()]
    elif source == "dnf":
        names = [l.split()[0].rsplit(".", 1)[0] for l in out.splitlines() if l.strip() and not l.startswith(" ") and len(l.split()) == 3]
    return names

# ---------- безопасность ----------
CRITICAL_PATTERNS = [
    r"linux-image.*", r"linux-headers.*", r"linux-modules.*", r"linux-signed.*", r"grub.*", r"shim.*",
    r"systemd.*", r"libsystemd.*", r"udev", r"libudev.*", r"init", r"init-system-helpers", r"sysvinit.*",
    r"initramfs-tools.*", r"dracut.*", r"libc6.*", r"libc-bin", r"libstdc\+\+6", r"libgcc-s1",
    r"dpkg", r"apt", r"libapt-pkg.*", r"base-files", r"base-passwd", r"bash", r"coreutils", r"util-linux.*",
    r"login", r"passwd", r"sudo", r"polkitd", r"pkexec", r"dbus.*", r"libdbus.*",
    r"xserver-xorg.*", r"xorg", r"xwayland", r"libwayland.*", r"mesa.*", r"libgl1.*", r"libegl.*",
    r"gdm3", r"sddm", r"lightdm.*", r"lxdm", r"slim",
    r"gnome-shell.*", r"gnome-session.*", r"mutter.*", r"plasma-workspace.*", r"plasma-desktop.*", r"kwin.*",
    r"xfce4-session", r"xfwm4", r"cinnamon.*", r"mate-session-manager", r"marco", r"task-.*desktop",
    r"nvidia.*", r"openrc", r"runit-init", r"sysvinit-core", r"elogind", r"plymouth.*", r"libnvidia.*", r"firmware-.*", r"network-manager.*", r"cryptsetup.*", r"lvm2", r"mdadm",
    r"e2fsprogs", r"btrfs-progs", r"zfs.*", r"openssh-server", r"pipewire.*", r"wireplumber",
]
_CRIT = re.compile(r"^(?:" + "|".join(CRITICAL_PATTERNS) + r")$")


BOOT_PATTERNS = [r"linux-image.*", r"linux-signed.*", r"linux-modules.*", r"grub.*", r"shim.*", r"initramfs-tools.*",
                 r"dracut.*", r"nvidia.*", r"libnvidia.*", r"firmware-.*", r"openrc", r"runit-init", r"sysvinit-core",
                 r"systemd-sysv", r"plymouth.*", r"gdm3", r"sddm", r"lightdm.*", r"lxdm", r"slim", r"task-.*desktop", r"zfs.*", r"cryptsetup.*"]
_BOOT = re.compile(r"^(?:" + "|".join(BOOT_PATTERNS) + r")$")


def is_boot_related(pkg: str) -> bool:
    """Пакеты, которые даже при новой установке меняют загрузку/сеанс: ядро, загрузчик, драйверы, init, экранный менеджер."""
    return bool(_BOOT.match(pkg.split(":")[0]))


def is_critical(pkg: str) -> bool:
    return bool(_CRIT.match(pkg.split(":")[0]))


@dataclass
class SimResult:
    installs: list = field(default_factory=list)   # новые пакеты
    upgrades: list = field(default_factory=list)   # обновляемые (уже установлены)
    removals: list = field(default_factory=list)
    critical: list = field(default_factory=list)
    essential_removed: list = field(default_factory=list)

    @property
    def safe(self):
        return not self.removals and not self.critical

    def problems_ru(self):
        p = []
        if self.removals:
            p.append("будут УДАЛЕНЫ пакеты: " + ", ".join(self.removals))
        if self.critical:
            p.append("будут затронуты критически важные системные пакеты: " + ", ".join(self.critical))
        return p


def parse_apt_simulation(out: str) -> SimResult:
    r = SimResult()
    for line in out.splitlines():
        m = re.match(r"^(Inst|Remv|Purg) (\S+)(?: \[([^\]]+)\])?", line)
        if not m:
            continue
        kind, pkg, old = m.groups()
        if kind == "Inst":
            (r.upgrades if old else r.installs).append(pkg)
        else:
            r.removals.append(pkg)
        if (is_critical(pkg) and (kind != "Inst" or old)) or is_boot_related(pkg):
            # даже НОВАЯ установка ядра, загрузчика, драйверов, init или экранного менеджера меняет загрузку системы
            r.critical.append(pkg)
    if re.search(r"WARNING: The following essential packages will be removed", out):
        r.essential_removed.append("essential")
        if "essential" not in r.removals:
            r.removals.append("(essential-пакеты)")
    return r


def check_remove_target(pkg: str):
    if is_critical(pkg):
        return f"«{pkg}» — критически важный системный пакет, Ставилка его не удаляет."
    return None

# ---------- файлы ----------
FILE_KINDS = [
    (".flatpakref", "flatpakref"), (".flatpak", "flatpak-bundle"), (".appimage", "appimage"),
    (".deb", "deb"), (".rpm", "rpm"), (".tar.gz", "tarball"), (".tgz", "tarball"), (".tar.xz", "tarball"),
    (".tar.bz2", "tarball"), (".tar.zst", "tarball"), (".zip", "tarball"), (".sh", "script"), (".run", "script"),
]
MIME_TYPES = [
    "application/vnd.debian.binary-package", "application/x-deb", "application/x-rpm", "application/x-redhat-package-manager",
    "application/vnd.appimage", "application/x-iso9660-appimage", "application/vnd.flatpak.ref", "application/vnd.flatpak",
    "application/x-compressed-tar", "application/x-xz-compressed-tar", "application/x-bzip-compressed-tar", "application/zip",
    "application/x-shellscript", "application/x-makeself",
]


def file_kind(path: str):
    low = path.lower()
    for ext, k in FILE_KINDS:
        if low.endswith(ext):
            return k
    return None


def deb_install_cmd(path, simulate=False):
    path = os.path.abspath(path)
    return (["apt-get", "-s", "install", "--", path] if simulate else
            ROOT + APT_ENV + ["apt-get", "install", "-y", "--no-remove", "--", path])


def file_install_cmd(path, pm):
    k = file_kind(path)
    path = os.path.abspath(path)
    if k == "flatpakref":
        return ["flatpak", "install", "--user", "-y", "--noninteractive", "--from", path]
    if k == "flatpak-bundle":
        return ["flatpak", "install", "--user", "-y", "--noninteractive", "--bundle", path]
    if k == "deb":
        return deb_install_cmd(path) if pm == "apt" else None
    if k == "rpm":
        return {"dnf": ROOT + ["dnf", "install", "-y", path],
                "zypper": ROOT + ["zypper", "--non-interactive", "install", path]}.get(pm)
    if k == "script":
        return ["bash", path]  # всегда от имени пользователя, без root
    return None  # appimage/tarball — внутренняя обработка


def safe_name(s: str) -> str:
    s = os.path.basename(s)
    for ext, _ in FILE_KINDS:
        if s.lower().endswith(ext):
            s = s[: -len(ext)]
            break
    s = re.sub(r"[^\w.+-]+", "-", s).strip("-.")
    return s or "app"


def pretty_name(s: str) -> str:
    n = safe_name(s)
    n = re.sub(r"[-_](x86_64|amd64|aarch64|arm64|linux|x64|\d[\w.]*)+$", "", n, flags=re.I)
    return n.replace("-", " ").replace("_", " ").strip() or "Приложение"


def desktop_escape(s: str) -> str:
    return s.replace("\n", " ").replace("\r", " ")


def exec_quote(path: str) -> str:
    # спецификация Desktop Entry: аргумент в двойных кавычках, экранировать " ` $ \
    return '"' + re.sub(r'(["`$\\])', r'\\\1', path) + '"'


def make_desktop_entry(name, exec_line, icon="application-x-executable", comment="", extra=None):
    lines = ["[Desktop Entry]", "Type=Application", f"Name={desktop_escape(name)}",
             f"Exec={exec_line}", f"Icon={icon}", "Terminal=false"]
    if comment:
        lines.append(f"Comment={desktop_escape(comment)}")
    for k, v in (extra or {}).items():
        lines.append(f"{k}={v}")
    lines.append("X-Stavilka=true")
    return "\n".join(lines) + "\n"


def autostart_entry(exe_path):
    inner = f"test -x {shlex.quote(exe_path)} && exec nice -n 19 ionice -c3 {shlex.quote(exe_path)} --background || true"
    # sh -c '...' — внешние одинарные кавычки, внутри shlex-кавычки уже одинарные → используем двойные снаружи
    esc = inner.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return make_desktop_entry(
        "Ставилка (фоновая проверка обновлений)", f'sh -c "{esc}"', icon="system-software-install",
        comment="Проверяет обновления после входа в систему",
        extra={"X-GNOME-Autostart-enabled": "true", "X-GNOME-Autostart-Delay": "15",
               "X-KDE-autostart-after": "panel", "NoDisplay": "true", "StartupNotify": "false"})
