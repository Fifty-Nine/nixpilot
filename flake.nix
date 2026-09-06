# nixpilot: MCP servers for the NixOS TinyPilot KVM, served by the host.
{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = {
    self,
    nixpkgs,
  }: let
    systems = ["x86_64-linux" "aarch64-linux"];
    forAllSystems = f:
      nixpkgs.lib.genAttrs systems (system:
        f nixpkgs.legacyPackages.${system});
  in {
    nixosModules = {
      backend = ./nixosModules/backend.nix;
      ustreamer = ./nixosModules/ustreamer.nix;
      usb-gadget = ./nixosModules/usb-gadget.nix;
      edid = ./nixosModules/edid.nix;
      default = ./nixosModules/default.nix;
    };

    packages = forAllSystems (pkgs: rec {
      # One derivation per profile; each shares the same source tree, package
      # layout (share/nixpilot), and interpreter env.
      nixpilot-mcp = pkgs.callPackage ./package.nix {
        pname = "nixpilot-mcp";
        module = "nixpilot.gamepad";
      };
      nixpilot-screen-mcp = pkgs.callPackage ./package.nix {
        pname = "nixpilot-screen-mcp";
        module = "nixpilot.screen";
      };
      nixpilot-keyboard-mcp = pkgs.callPackage ./package.nix {
        pname = "nixpilot-keyboard-mcp";
        module = "nixpilot.keyboard";
      };
      default = nixpilot-mcp;
    });

    # Offline selftest gate: pure source checks (report codecs and JPEG
    # marker walk) without any device dependency. The protocol smoke gates
    # (scripts/mcp-smoke.sh, scripts/keyboard-smoke.sh,
    # scripts/screen-smoke.sh) need the device and therefore run only
    # on/against the host.
    checks = forAllSystems (pkgs: {
      nixpilot-selftest = pkgs.callPackage ./checks.nix {
        inherit
          (self.packages.${pkgs.stdenv.hostPlatform.system})
          nixpilot-mcp
          nixpilot-keyboard-mcp
          nixpilot-screen-mcp
          ;
      };
    });

    devShells = forAllSystems (pkgs: {
      default = let
        pyEnv = pkgs.python3.withPackages (ps: [ps.mcp]);
      in
        pkgs.mkShell {
          packages = with pkgs; [
            pyEnv
            pre-commit
            ruff
            alejandra
            deadnix
            statix
            nixf-diagnose
            flake-checker
          ];
        };
    });

    formatter = forAllSystems (pkgs: pkgs.alejandra);
  };
}
