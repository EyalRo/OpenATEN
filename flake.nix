{
  description = "OpenATEN: open tooling for the ATEN US3342 host-to-host bridge";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }:
    let
      project = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project;
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in {
      packages = forAll (pkgs: rec {
        default = pkgs.python3Packages.buildPythonApplication {
          pname = "openaten";
          version = project.version;  # single source of truth: pyproject.toml
          pyproject = true;
          src = self;
          build-system = [ pkgs.python3Packages.setuptools ];
          dependencies = [ pkgs.python3Packages.pyusb pkgs.python3Packages.evdev ];
          makeWrapperArgs = [ "--prefix" "LD_LIBRARY_PATH" ":" (pkgs.lib.makeLibraryPath [ pkgs.libusb1 ]) ];
          nativeCheckInputs = [ pkgs.python3Packages.pytestCheckHook pkgs.python3Packages.hypothesis ];
          meta.mainProgram = "openaten";
        };
        # The tray icon and setup window app: a ksni (StatusNotifierItem) tray that follows the daemon's status file; left-click opens a GTK4 window.
        # `cargo test` runs in the build; the icons are installed so hosts that resolve themed icon names find them.
        panel = pkgs.rustPlatform.buildRustPackage {
          pname = "openaten-panel";
          version = (builtins.fromTOML (builtins.readFile ./panel/Cargo.toml)).package.version;
          src = ./panel;
          cargoLock.lockFile = ./panel/Cargo.lock;
          nativeBuildInputs = [ pkgs.pkg-config pkgs.wrapGAppsHook4 ];
          buildInputs = [ pkgs.gtk4 pkgs.libadwaita ];
          # `setfacl` for the window's explicit "Grant access" step (pkexec runs it by absolute path)
          preFixup = ''
            gappsWrapperArgs+=(--prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.acl ]})
          '';
          postInstall = ''
            for f in assets/*.svg; do
              install -Dm644 "$f" "$out/share/icons/hicolor/scalable/apps/$(basename "$f")"
            done
          '';
          meta.mainProgram = "openaten-panel";
        };
        # Rust core port (`openaten` Rust binary): the non-hardware core of OpenATEN, ported to Rust. Hardware seams (USB, evdev,
        # uinput, netlink) are stubbed behind traits, so `cargo test` runs the 105-test suite with no hardware attached.
        rust-core = pkgs.rustPlatform.buildRustPackage {
          pname = "openaten-core";
          version = (builtins.fromTOML (builtins.readFile ./rust/Cargo.toml)).package.version;
          src = ./rust;
          cargoLock.lockFile = ./rust/Cargo.lock;
          nativeBuildInputs = [ pkgs.pkg-config ];
          buildInputs = [ pkgs.libusb1 ];
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
      # NixOS module: `imports = [ openaten.nixosModules.default ]; services.openaten = { enable = true; role = "master"; users = [ "me" ]; };`
      nixosModules.default = { config, lib, pkgs, ... }:
        let
          cfg = config.services.openaten;
          udevRules = pkgs.writeTextFile {
            name = "openaten-udev-rules";
            destination = "/lib/udev/rules.d/70-openaten.rules";  # numbered below 73 so the uaccess tag applies
            text = builtins.readFile ./udev/70-openaten.rules;
          };
          args = [ "daemon" "--role" cfg.role ]
            ++ [ (if cfg.clipboard then "--clipboard" else "--no-clipboard") ]
            ++ lib.optionals (cfg.receiveDir != null) [ "--receive-dir" cfg.receiveDir ]
            ++ cfg.extraArgs;
        in {
          options.services.openaten = {
            enable = lib.mkEnableOption "the OpenATEN daemon (keyboard/mouse hand-off, clipboard and file transfer over an ATEN US3342 bridge)";
            package = lib.mkOption {
              type = lib.types.package;
              default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
              defaultText = lib.literalExpression "openaten.packages.\${system}.default";
              description = "The openaten package to run.";
            };
            role = lib.mkOption {
              type = lib.types.enum [ "master" "slave" ];
              description = "Link role: `master` on one computer, `slave` on the other.";
            };
            clipboard = lib.mkOption {
              type = lib.types.bool;
              default = true;
              description = "Share the clipboard with the other computer (needs wl-clipboard or xclip in the session). On by default.";
            };
            receiveDir = lib.mkOption {
              type = lib.types.nullOr lib.types.str;
              default = "%h/Downloads";
              example = "%h/Downloads/OpenATEN";
              description = "Accept files sent from the other computer into this directory (systemd specifiers such as %h work). Receiving is off when null.";
            };
            logLevel = lib.mkOption {
              type = lib.types.enum [ "DEBUG" "INFO" "WARNING" "ERROR" "CRITICAL" ];
              default = "INFO";
              description = "Log level (OPENATEN_LOG); logs go to the user journal.";
            };
            extraArgs = lib.mkOption {
              type = lib.types.listOf lib.types.str;
              default = [ ];
              example = [ "--hotkey" "ctrl+alt+k" ];
              description = "Extra arguments for `openaten daemon`.";
            };
            users = lib.mkOption {
              type = lib.types.listOf lib.types.str;
              default = [ ];
              description = "Users added to the `input` group, which the daemon needs to read the keyboard and mouse.";
            };
            usbStorageQuirk = lib.mkOption {
              type = lib.types.bool;
              default = true;
              description = "Add `usb-storage.quirks=05e3:0627:i` to the kernel command line so the kernel never probes the bridge's mass-storage function (it can wedge the firmware, see docs/hardware.md).";
            };
          };

          config = lib.mkIf cfg.enable {
            services.udev.packages = [ udevRules ];
            boot.kernelParams = lib.optional cfg.usbStorageQuirk "usb-storage.quirks=05e3:0627:i";
            users.users = lib.genAttrs cfg.users (_: { extraGroups = [ "input" ]; });
            environment.systemPackages = [ cfg.package ];
            systemd.user.services.openaten = {
              description = "OpenATEN daemon (keyboard/mouse hand-off over the ATEN bridge)";
              documentation = [ "https://github.com/EyalRo/OpenATEN/blob/main/docs/setup.md" ];
              wantedBy = [ "graphical-session.target" ];
              after = [ "graphical-session.target" ];
              path = lib.optionals cfg.clipboard [ pkgs.wl-clipboard pkgs.xclip ];
              environment.OPENATEN_LOG = cfg.logLevel;
              serviceConfig = {
                ExecStart = "${cfg.package}/bin/openaten ${lib.escapeShellArgs args}";
                Restart = "on-failure";
                RestartSec = 3;
                NoNewPrivileges = true;
              };
            };
          };
        };

      # `nix flake check` evaluates the NixOS module with a minimal system (the openaten package is built too).
      checks = forAll (pkgs: {
        # `nix flake check` only evaluates packages, so build the default one here: that runs the test suite inside the Nix build.
        package = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        panel = self.packages.${pkgs.stdenv.hostPlatform.system}.panel;  # builds the tray app and runs its cargo tests
        rust-core = self.packages.${pkgs.stdenv.hostPlatform.system}.rust-core;  # builds the Rust port and runs its cargo tests
        nixos-module-eval =
          let
            sys = nixpkgs.lib.nixosSystem {
              modules = [
                self.nixosModules.default
                {
                  nixpkgs.hostPlatform = pkgs.stdenv.hostPlatform.system;
                  services.openaten = { enable = true; role = "master"; clipboard = true; receiveDir = "%h/OpenATEN"; users = [ "alice" ]; };
                  users.users.alice.isNormalUser = true;
                  boot.loader.grub.enable = false;
                  fileSystems."/" = { device = "none"; fsType = "tmpfs"; };
                  system.stateVersion = "25.05";
                }
              ];
            };
          in pkgs.runCommand "openaten-nixos-module-eval" {
            execStart = sys.config.systemd.user.services.openaten.serviceConfig.ExecStart;
            kernelParams = toString sys.config.boot.kernelParams;
            groups = toString sys.config.users.users.alice.extraGroups;
            toplevel = builtins.unsafeDiscardStringContext sys.config.system.build.toplevel.drvPath;
          } ''
            case "$execStart" in *"daemon --role master --clipboard --receive-dir"*) ;; *) echo "unexpected ExecStart: $execStart"; exit 1;; esac
            case "$kernelParams" in *usb-storage.quirks=05e3:0627:i*) ;; *) echo "kernel parameter missing: $kernelParams"; exit 1;; esac
            case " $groups " in *" input "*) ;; *) echo "alice is not in the input group: $groups"; exit 1;; esac
            echo ok > $out
          '';
      });
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ (pkgs.python3.withPackages (p: [ p.pyusb p.pytest p.evdev p.hypothesis ])) pkgs.libusb1 pkgs.cargo pkgs.rustc pkgs.pkg-config ];
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.libusb1 ];
          PYTHONPATH = "src";
        };
      });
    };
}
