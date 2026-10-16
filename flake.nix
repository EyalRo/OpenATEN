{
  description = "OpenATEN: open tooling for the ATEN US3342 host-to-host bridge";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in {
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ (pkgs.python3.withPackages (p: [ p.pyusb p.pytest p.evdev ])) pkgs.libusb1 ];
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.libusb1 ];
          PYTHONPATH = "src";
        };
      });
    };
}
