#!/bin/sh
# Renders the tray state icons to PNG (embedded in the binary as the icon_pixmap fallback). Needs rsvg-convert:
#   nix shell nixpkgs#librsvg -c ./render-png.sh
cd "$(dirname "$0")"
for name in connected forwarding error offline; do
  for size in 32 64; do
    rsvg-convert -w "$size" -h "$size" "openaten-$name.svg" -o "png/openaten-$name-$size.png"
  done
done
