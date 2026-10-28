#!/bin/sh
# Сборка AppImage и .deb. Использование: packaging/build.sh [путь к appimagetool]
set -eu
cd "$(dirname "$0")/.."
V=$(python3 -c 'import stavilka;print(stavilka.VERSION)')
TOOL=${1:-appimagetool}
rm -rf build dist; mkdir -p build dist
# ---------- AppImage ----------
A=build/AppDir
mkdir -p $A/usr/lib/stavilka $A/usr/share/icons/hicolor/scalable/apps
cp -r stavilka $A/usr/lib/stavilka/
find $A -name __pycache__ -prune -exec rm -rf {} +
cp data/stavilka.svg $A/stavilka.svg
cp data/stavilka.svg $A/usr/share/icons/hicolor/scalable/apps/ru.ivanivorontsov.Stavilka.svg
cat > $A/ru.ivanivorontsov.Stavilka.desktop <<D
[Desktop Entry]
Type=Application
Name=Ставилка
Comment=Установка любых программ для Linux
Exec=stavilka %F
Icon=stavilka
Categories=System;PackageManager;
Terminal=false
D
cat > $A/AppRun <<'R'
#!/bin/sh
# Использует системный Python с GTK4/libadwaita (есть в Debian 13 с GNOME).
HERE="$(dirname "$(readlink -f "$0")")"
export PYTHONPATH="$HERE/usr/lib/stavilka${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Нужен python3" >&2; exit 1
fi
exec python3 -m stavilka "$@"
R
chmod +x $A/AppRun
ln -sf stavilka.svg $A/.DirIcon
ARCH=x86_64 "$TOOL" --no-appstream $A dist/Stavilka-$V-x86_64.AppImage >/dev/null 2>&1 || ARCH=x86_64 "$TOOL" --appimage-extract-and-run --no-appstream $A dist/Stavilka-$V-x86_64.AppImage
# ---------- .deb ----------
D=build/deb
mkdir -p $D/DEBIAN $D/usr/bin $D/usr/lib/stavilka $D/usr/share/applications $D/usr/share/icons/hicolor/scalable/apps $D/usr/share/doc/stavilka
cp -r stavilka $D/usr/lib/stavilka/
find $D -name __pycache__ -prune -exec rm -rf {} +
printf '#!/bin/sh\nexport PYTHONPATH=/usr/lib/stavilka\nexec python3 -m stavilka "$@"\n' > $D/usr/bin/stavilka
chmod 755 $D/usr/bin/stavilka
cp data/stavilka.svg $D/usr/share/icons/hicolor/scalable/apps/ru.ivanivorontsov.Stavilka.svg
python3 - "$D" <<'P'
import sys; sys.path.insert(0, ".")
from stavilka.core import MIME_TYPES
open(sys.argv[1] + "/usr/share/applications/ru.ivanivorontsov.Stavilka.desktop", "w").write(
"[Desktop Entry]\nType=Application\nName=Ставилка\nComment=Установка любых программ для Linux\nExec=stavilka %F\n"
"Icon=ru.ivanivorontsov.Stavilka\nCategories=System;PackageManager;\nTerminal=false\nMimeType=" + ";".join(MIME_TYPES) + ";\n")
P
cp README.md $D/usr/share/doc/stavilka/
cat > $D/DEBIAN/control <<C
Package: stavilka
Version: $V
Section: admin
Priority: optional
Architecture: all
Maintainer: Ivan Vorontsov <noreply@example.invalid>
Depends: python3 (>= 3.11), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5)
Recommends: flatpak, libnotify-bin, desktop-file-utils
Suggests: python3-nautilus, timeshift
Description: Ставилка — установка любых программ для Linux
 Поиск и установка из APT, Flathub, Snap; установка .deb, AppImage,
 .flatpakref, архивов и скриптов из контекстного меню файлового менеджера.
 Перед каждым изменением системы делает симуляцию и отказывается,
 если что-то удалится или затронет критичные пакеты.
C
# пакет НЕ содержит maintainer-скриптов: ничего не выполняется от root при установке
dpkg-deb --root-owner-group --build $D dist/stavilka_${V}_all.deb >/dev/null
cp packaging/install.sh dist/
(cd dist && sha256sum * > SHA256SUMS)
ls -la dist
