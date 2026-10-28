import sys, argparse, os


def background():
    """Фоновый режим автозапуска: проверить обновления, уведомить, выйти. Любая ошибка — тихий выход."""
    from . import integration as I
    try:
        if not I.load_settings().get("autostart", True) or not I.autostart_guard_begin():
            return 0
        from .actions import Backend
        import subprocess, shutil
        n = len(Backend().updates())
        if n and shutil.which("notify-send"):
            subprocess.run(["notify-send", "-a", "Ставилка", "-i", "system-software-install",
                            "Доступны обновления", f"Программ с обновлениями: {n}. Откройте Ставилку → «Обновления»."], timeout=10)
        I.autostart_guard_ok()
    except Exception:
        pass
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="stavilka", description="Ставилка — установка программ для Linux")
    ap.add_argument("--background", action="store_true", help="фоновая проверка обновлений (автозапуск)")
    ap.add_argument("--integrate", action="store_true", help="добавить в меню/контекстное меню")
    ap.add_argument("--no-autostart", action="store_true")
    ap.add_argument("--unintegrate", action="store_true", help="убрать интеграцию")
    ap.add_argument("--uninstall", action="store_true", help="удалить все следы Ставилки")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("files", nargs="*")
    a = ap.parse_args(argv)
    from . import VERSION, integration as I
    if a.version:
        print(VERSION); return 0
    if a.background:
        return background()
    if a.integrate:
        print("Интеграция:", I.integrate(autostart=not a.no_autostart)); return 0
    if a.unintegrate or a.uninstall:
        I.unintegrate(full=a.uninstall); print("Готово"); return 0
    try:
        from .app import App
    except (ImportError, ValueError) as e:
        msg = ("Для Ставилки нужны GTK4 и libadwaita:\n  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n" f"({e})")
        print(msg, file=sys.stderr)
        import shutil, subprocess
        if shutil.which("zenity"):
            subprocess.run(["zenity", "--error", "--text", msg])
        return 1
    return App().run([sys.argv[0]] + [os.path.abspath(f) for f in a.files])


if __name__ == "__main__":
    sys.exit(main())
