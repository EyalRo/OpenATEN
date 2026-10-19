#!/bin/sh
# Generates the logo and the tray state icons (same drawing, different colours/dot). Needs only sh.
# usage: gen.sh  (writes ../assets/*.svg next to this script)
cd "$(dirname "$0")"
icon() { # name screen_big screen_small pointer_fill dot_fill [dot_mark]
  cat > "$1.svg" <<SVG
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <title>OpenATEN</title>
  <rect x="3" y="28" width="26" height="18" rx="3.5" fill="$3"/>
  <rect x="33" y="10" width="28" height="34" rx="3.5" fill="$2"/>
  <path d="M25 17 L25 38 L30 33.5 L33.6 41.5 L37.4 39.8 L33.8 32 L40.5 32 Z" fill="$4" stroke="#0f172a" stroke-width="2" stroke-linejoin="round"/>
  $( [ -n "$5" ] && echo "<circle cx=\"52\" cy=\"52\" r=\"9\" fill=\"$5\" stroke=\"#ffffff\" stroke-width=\"2.5\"/>" )
  $6
</svg>
SVG
}
icon openaten            "#1d4ed8" "#0f766e" "#ffffff" ""
icon openaten-connected  "#1d4ed8" "#0f766e" "#ffffff" "#22c55e"
icon openaten-forwarding "#1d4ed8" "#0f766e" "#facc15" "#f97316"
icon openaten-error      "#1d4ed8" "#0f766e" "#ffffff" "#dc2626" '<path d="M52 47 V53 M52 56 V57" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>'
icon openaten-offline    "#64748b" "#64748b" "#e2e8f0" "#94a3b8"
