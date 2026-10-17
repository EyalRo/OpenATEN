{
  description = "OpenATEN: open tooling for the ATEN US3342 host-to-host bridge";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in {
      packages = forAll (pkgs: rec {
        default = pkgs.python3Packages.buildPythonApplication {
          pname = "openaten";
          version = "0.1.0";
          pyproject = true;
          src = self;
          build-system = [ pkgs.python3Packages.setuptools ];
          dependencies = [ pkgs.python3Packages.pyusb pkgs.python3Packages.evdev ];
          makeWrapperArgs = [ "--prefix" "LD_LIBRARY_PATH" ":" (pkgs.lib.makeLibraryPath [ pkgs.libusb1 ]) ];
          doCheck = false;
          meta.mainProgram = "openaten";
        };
        # Border strips (`openaten strip`): GTK4 + gtk4-layer-shell. gtk4-layer-shell must be preloaded before libwayland-client, so this
        # lives in its own wrapper instead of slowing down the daemon.
        strip =
          let py = pkgs.python3.withPackages (p: [ p.pygobject3 (p.toPythonModule default) ]);
          in pkgs.writeShellScriptBin "openaten-strip" ''
            export GI_TYPELIB_PATH=${pkgs.lib.makeSearchPath "lib/girepository-1.0" (map pkgs.lib.getLib (with pkgs; [ gtk4 gtk4-layer-shell glib gobject-introspection cairo pango gdk-pixbuf graphene harfbuzz ]))}
            export LD_PRELOAD=${pkgs.gtk4-layer-shell}/lib/libgtk4-layer-shell.so''${LD_PRELOAD:+:$LD_PRELOAD}
            exec ${py}/bin/python -m openaten strip "$@"
          '';
      });
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ (pkgs.python3.withPackages (p: [ p.pyusb p.pytest p.evdev ])) pkgs.libusb1 ];
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.libusb1 ];
          PYTHONPATH = "src";
        };
      });
    };
}
