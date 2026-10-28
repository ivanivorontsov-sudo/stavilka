#!/bin/sh
# Установка Ставилки (AppImage) только для текущего пользователя — без root.
# Использование: sh install.sh [путь/к/Stavilka-*.AppImage]
set -eu
AI=${1:-$(ls "$(dirname "$0")"/Stavilka-*-x86_64.AppImage 2>/dev/null | tail -n1)}
[ -f "$AI" ] || { echo "Не найден файл Stavilka-*.AppImage"; exit 1; }
if ! python3 -c 'import gi;gi.require_version("Gtk","4.0");gi.require_version("Adw","1")' 2>/dev/null; then
  echo "Нужны GTK4 и libadwaita. Установите (это обычные пакеты Debian 13):"
  echo "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
  exit 1
fi
chmod +x "$AI"
APPIMAGE="$(readlink -f "$AI")" "$AI" --integrate || APPIMAGE="$(readlink -f "$AI")" "$AI" --appimage-extract-and-run --integrate
echo "Готово. Ставилка есть в меню приложений, а в файловом менеджере: ПКМ → «Установить в Linux»."
echo "Удалить всё: ~/.local/share/stavilka/stavilka --uninstall"
