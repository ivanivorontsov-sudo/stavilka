import threading, os
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio, Gdk
from . import APP_ID, APP_NAME, VERSION, core
from . import integration as I
from .actions import Backend, SOURCE_LABELS

CSS = b"""
.src-badge { font-size: 0.8em; padding: 2px 8px; border-radius: 99px; background: alpha(@accent_bg_color,0.15); color: @accent_color; }
.sys-badge { font-size: 0.8em; padding: 2px 8px; border-radius: 99px; background: alpha(@warning_bg_color,0.2); color: @warning_color; }
.logview { font-family: monospace; font-size: 0.9em; }
"""


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=980, default_height=700)
        self.app = app
        self.be = Backend()
        self.settings = I.load_settings()
        self.busy = False
        prov = Gtk.CssProvider(); prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.toasts = Adw.ToastOverlay()
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.stack = Adw.ViewStack()
        sw = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        hb.set_title_widget(sw)
        self.spinner = Gtk.Spinner()
        hb.pack_end(self.spinner)
        openb = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Установить из файла…")
        openb.connect("clicked", self.on_open_file)
        hb.pack_start(openb)
        tv.add_top_bar(hb)
        tv.set_content(self.stack)
        self.toasts.set_child(tv)
        self.set_content(self.toasts)

        self.stack.add_titled_with_icon(self.build_search(), "search", "Поиск", "system-search-symbolic")
        self.inst_list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.stack.add_titled_with_icon(self.wrap_list(self.inst_list, "Обновить список", self.refresh_installed), "installed", "Установленные", "view-grid-symbolic")
        self.upd_list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.stack.add_titled_with_icon(self.wrap_list(self.upd_list, "Проверить обновления", self.refresh_updates), "updates", "Обновления", "software-update-available-symbolic")
        self.stack.add_titled_with_icon(self.build_log(), "log", "Журнал", "utilities-terminal-symbolic")
        self.stack.add_titled_with_icon(self.build_settings(), "settings", "Настройки", "emblem-system-symbolic")

        dt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        dt.connect("drop", lambda t, v, x, y: [self.install_file(f.get_path()) for f in v.get_files()] or True)
        self.add_controller(dt)
        self.log(f"{APP_NAME} {VERSION}. Источники: " + ", ".join(SOURCE_LABELS.get(s, s) for s in self.be.sources))

    # ---------- общие ----------
    def log(self, line):
        def f():
            b = self.logbuf
            b.insert(b.get_end_iter(), line + "\n")
            self.logview.scroll_to_mark(b.get_insert(), 0, False, 0, 0)
        GLib.idle_add(f)

    def toast(self, t):
        GLib.idle_add(lambda: self.toasts.add_toast(Adw.Toast(title=t, timeout=4)))

    def confirm(self, title, body, danger=False, only_ok=False):
        """Вызывается из рабочего потока, блокирует его до ответа."""
        ev = threading.Event(); res = {"ok": False}
        def show():
            d = Adw.AlertDialog(heading=title, body=body)
            if only_ok:
                d.add_response("ok", "Понятно")
            else:
                d.add_response("cancel", "Отмена")
                d.add_response("ok", "Продолжить")
                d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE if danger else Adw.ResponseAppearance.SUGGESTED)
            d.connect("response", lambda _d, r: (res.__setitem__("ok", r == "ok" and not only_ok), ev.set()))
            d.present(self)
        GLib.idle_add(show)
        ev.wait()
        return res["ok"]

    def run_bg(self, fn, done=None, show_log=False):
        if self.busy:
            self.toast("Подождите, идёт другая операция"); return
        self.busy = True; self.spinner.start()
        if show_log:
            self.stack.set_visible_child_name("log")
        def w():
            try:
                r = fn()
            except Exception as e:
                self.log(f"Ошибка: {e}"); r = None
            def fin():
                self.busy = False; self.spinner.stop()
                if done: done(r)
            GLib.idle_add(fin)
        threading.Thread(target=w, daemon=True).start()

    def wrap_list(self, lb, btn_label, cb):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        b = Gtk.Button(label=btn_label, halign=Gtk.Align.START, css_classes=["pill"])
        b.connect("clicked", lambda *_: cb())
        box.append(b)
        sc = Gtk.ScrolledWindow(vexpand=True); clamp = Adw.Clamp(maximum_size=900); clamp.set_child(lb); sc.set_child(clamp)
        box.append(sc)
        return box

    def pkg_row(self, p, mode):
        r = Adw.ActionRow(title=GLib.markup_escape_text(p.name), subtitle=GLib.markup_escape_text((p.description or p.id)[:160]))
        badge = Gtk.Label(label=SOURCE_LABELS.get(p.source, p.source), css_classes=["src-badge"], valign=Gtk.Align.CENTER)
        r.add_suffix(badge)
        if p.system_change:
            r.add_suffix(Gtk.Label(label="меняет систему", css_classes=["sys-badge"], valign=Gtk.Align.CENTER,
                                   tooltip_text="Пакет ставится в систему от имени администратора. Flatpak и AppImage систему не меняют."))
        def btn(lbl, cls, cb):
            b = Gtk.Button(label=lbl, valign=Gtk.Align.CENTER, css_classes=cls); b.connect("clicked", lambda *_: cb()); r.add_suffix(b)
        if mode == "update":
            btn("Обновить", ["suggested-action"], lambda: self.do(self.be.update, p, self.refresh_updates))
        elif p.installed:
            btn("Запустить", [], lambda: self.be.launch(p) or self.toast("Не удалось найти, как запустить"))
            btn("Удалить", ["destructive-action"], lambda: self.do(self.be.remove, p, self.after_change))
        else:
            btn("Установить", ["suggested-action"], lambda: self.do(self.be.install, p, self.after_change))
        return r

    def do(self, op, p, then):
        self.run_bg(lambda: op(p, self.log, self.confirm, self.settings),
                    lambda ok: (self.toast("Готово" if ok else "Не выполнено — подробности в журнале"), then()), show_log=True)

    def after_change(self):
        if self.search.get_text().strip():
            self.on_search()

    def fill(self, lb, items, mode, empty):
        while (c := lb.get_first_child()):
            lb.remove(c)
        if not items:
            lb.append(Adw.ActionRow(title=empty))
        for p in items:
            lb.append(self.pkg_row(p, mode))

    # ---------- поиск ----------
    def build_search(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        top = Gtk.Box(spacing=8)
        self.search = Gtk.SearchEntry(placeholder_text="Например: telegram, gimp, vlc, obs…", hexpand=True)
        self.search.connect("activate", lambda *_: self.on_search())
        top.append(self.search)
        self.src_names = ["Все источники"] + [SOURCE_LABELS[s] for s in self.be.sources]
        self.src_dd = Gtk.DropDown.new_from_strings(self.src_names)
        top.append(self.src_dd)
        b = Gtk.Button(label="Найти", css_classes=["suggested-action"]); b.connect("clicked", lambda *_: self.on_search()); top.append(b)
        box.append(Adw.Clamp(maximum_size=900, child=top))
        self.res_list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.status = Adw.StatusPage(icon_name="system-software-install-symbolic", title="Найдите любую программу",
            description="Поиск идёт сразу по Flathub, репозиториям Debian (APT) и Snap.\n"
                        "Файлы .deb, .AppImage, .flatpakref, архивы и скрипты можно перетащить в окно\n"
                        "или выбрать в файловом менеджере: ПКМ → «Установить в Linux».", vexpand=True)
        self.sstack = Gtk.Stack(); self.sstack.add_named(self.status, "empty")
        sc = Gtk.ScrolledWindow(vexpand=True); sc.set_child(Adw.Clamp(maximum_size=900, child=self.res_list))
        self.sstack.add_named(sc, "res")
        box.append(self.sstack)
        return box

    def on_search(self):
        q = self.search.get_text().strip()
        if not q:
            return
        i = self.src_dd.get_selected()
        srcs = None if i == 0 else [self.be.sources[i - 1]]
        self.run_bg(lambda: self.be.search(q, srcs), lambda res: (self.fill(self.res_list, res or [], "search", "Ничего не найдено"), self.sstack.set_visible_child_name("res")))

    def refresh_installed(self):
        self.run_bg(self.be.installed_apps, lambda r: self.fill(self.inst_list, r or [], "installed", "Пусто"))

    def refresh_updates(self):
        self.run_bg(self.be.updates, lambda r: self.fill(self.upd_list, r or [], "update", "Всё обновлено 🎉 (apt: список актуален на момент последнего apt update)"))

    # ---------- журнал ----------
    def build_log(self):
        self.logbuf = Gtk.TextBuffer()
        self.logview = Gtk.TextView(buffer=self.logbuf, editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                    css_classes=["logview"], left_margin=12, top_margin=12)
        sc = Gtk.ScrolledWindow(vexpand=True); sc.set_child(self.logview)
        return sc

    # ---------- файлы ----------
    def on_open_file(self, *_):
        fd = Gtk.FileDialog(title="Выберите пакет или программу")
        def done(d, r):
            try:
                f = d.open_finish(r)
            except GLib.Error:
                return
            self.install_file(f.get_path())
        fd.open(self, None, done)

    def install_file(self, path):
        if path:
            self.run_bg(lambda: self.be.install_file(path, self.log, self.confirm, self.settings),
                        lambda ok: self.toast("Установлено" if ok else "Не установлено — см. журнал"), show_log=True)

    # ---------- настройки ----------
    def build_settings(self):
        pg = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Поведение")
        def sw(title, sub, key, cb=None):
            r = Adw.SwitchRow(title=title, subtitle=sub, active=self.settings.get(key, True))
            def ch(row, _):
                self.settings[key] = row.get_active(); I.save_settings(self.settings)
                if cb: cb(row.get_active())
            r.connect("notify::active", ch); g.add(r); return r
        self.as_row = sw("Запускать вместе с системой", "Через 15 с после входа, с низким приоритетом: только проверка обновлений и уведомление", "autostart",
           lambda on: I.set_autostart(on))
        sw("Предлагать снимок Timeshift", "Перед изменениями системы, если Timeshift установлен", "timeshift_prompt")
        pg.add(g)
        g2 = Adw.PreferencesGroup(title="Интеграция", description="Все файлы создаются только в вашем домашнем каталоге (~/.local, ~/.config)")
        def act(title, sub, label, cb, cls="flat"):
            r = Adw.ActionRow(title=title, subtitle=sub)
            b = Gtk.Button(label=label, valign=Gtk.Align.CENTER, css_classes=[cls]); b.connect("clicked", lambda *_: cb()); r.add_suffix(b); g2.add(r)
        act("Меню приложений и контекстное меню", "Nautilus, Dolphin, Nemo, Caja, Thunar и «Открыть с помощью»", "Установить", self.do_integrate, "suggested-action")
        act("Подготовка Flatpak", "Установить flatpak (если нет) и подключить Flathub для пользователя", "Подготовить",
            lambda: self.run_bg(lambda: self.be.setup_flatpak(self.log, self.confirm, self.settings), lambda ok: self.toast("Flathub готов" if ok else "Не получилось — см. журнал"), True))
        act("Убрать интеграцию", "Удалить пункты меню, ярлык и автозапуск", "Убрать", lambda: (I.unintegrate(), self.as_row.set_active(False), self.toast("Интеграция удалена")))
        act("Полностью удалить следы Ставилки", "Интеграция, настройки, журнал, копия AppImage. Установленные программы останутся", "Удалить всё", self.full_uninstall, "destructive-action")
        pg.add(g2)
        g3 = Adw.PreferencesGroup(title="Гарантии безопасности")
        for t in ["Никогда не трогает загрузчик, ядро, initramfs, systemd и экранный менеджер",
                  "Перед каждой установкой/удалением через apt — симуляция; если что-то удалится или затронет критичные пакеты — отказ",
                  "Никаких full-upgrade, dist-upgrade и autoremove", "Не добавляет сторонние репозитории",
                  "Без телеметрии и сетевых запросов, кроме самих менеджеров пакетов"]:
            g3.add(Adw.ActionRow(title=t, title_lines=3))
        pg.add(g3)
        return pg

    def do_integrate(self):
        try:
            exe = I.integrate(autostart=self.settings.get("autostart", True))
            self.log(f"Интеграция установлена, программа вызывается как {exe}")
            self.toast("Готово: ПКМ → «Установить в Linux»")
        except Exception as e:
            self.log(f"Ошибка интеграции: {e}")

    def full_uninstall(self):
        def w():
            if self.confirm("Удалить все следы?", "Будут удалены пункты меню, автозапуск, настройки и копия программы в ~/.local/share/stavilka. "
                            "Программы, установленные через Ставилку из AppImage/архивов, тоже лежат там и будут удалены.", True):
                I.unintegrate(full=True); GLib.idle_add(self.app.quit)
        threading.Thread(target=w, daemon=True).start()

    def first_run(self):
        if self.settings.get("integrated") or self.settings.get("first_run_done"):
            return
        self.settings["first_run_done"] = True; I.save_settings(self.settings)
        d = Adw.AlertDialog(heading="Добро пожаловать в Ставилку",
            body="Добавить Ставилку в меню приложений и в контекстное меню файлов («Установить в Linux»)?\n\n"
                 "Также она будет запускаться после входа в систему (через 15 с, в фоне) и сообщать об обновлениях — это можно отключить в настройках.\n\n"
                 "Все файлы создаются только в вашем домашнем каталоге, система не меняется.")
        d.add_response("no", "Не сейчас"); d.add_response("noauto", "Без автозапуска"); d.add_response("yes", "Добавить")
        d.set_response_appearance("yes", Adw.ResponseAppearance.SUGGESTED)
        def resp(_d, r):
            if r in ("yes", "noauto"):
                self.settings["autostart"] = (r == "yes"); I.save_settings(self.settings); self.as_row.set_active(r == "yes")
                self.do_integrate()
        d.connect("response", resp); d.present(self)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.win = None

    def _win(self):
        if not self.win:
            self.win = Window(self)
        self.win.present()
        return self.win

    def do_activate(self):
        w = self._win(); GLib.idle_add(w.first_run)

    def do_open(self, files, n, hint):
        w = self._win()
        for f in files:
            if f.get_path():
                w.install_file(f.get_path())
