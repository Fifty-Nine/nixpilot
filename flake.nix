# nixpilot: Reusable NixOS modules and MCP servers for TinyPilot KVM appliances.
{
  description = "Reusable NixOS modules and MCP servers for TinyPilot KVM appliances";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    nixos-hardware = {
      url = "github:NixOS/nixos-hardware";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = {
    self,
    nixpkgs,
    nixos-hardware,
  }: let
    systems = ["x86_64-linux" "aarch64-linux"];
    forAllSystems = f:
      nixpkgs.lib.genAttrs systems (system:
        f nixpkgs.legacyPackages.${system});

    mkEvalCheck = system: name: moduleConfig: let
      pkgs = nixpkgs.legacyPackages.${system};
      eval = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          self.nixosModules.default
          {
            system.stateVersion = "26.11";
            fileSystems."/" = {
              device = "/dev/sda1";
              fsType = "ext4";
            };
            boot.loader.grub.enable = false;
            boot.loader.generic-extlinux-compatible.enable = true;
          }
          moduleConfig
        ];
      };
      evalProof = builtins.unsafeDiscardStringContext eval.config.system.build.toplevel.drvPath;
    in
      pkgs.writeText "eval-check-${name}-${system}" evalProof;
  in {
    nixosModules = {
      backend = ./nixosModules/backend.nix;
      ustreamer = ./nixosModules/ustreamer.nix;
      usb-gadget = ./nixosModules/usb-gadget.nix;
      mcp = ./nixosModules/mcp.nix;
      caddy = ./nixosModules/caddy.nix;
      default = ./nixosModules/default.nix;
    };

    packages = forAllSystems (pkgs:
      rec {
        tinypilot-backend = pkgs.callPackage ./pkgs/tinypilot-backend.nix {};

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
      }
      // (nixpkgs.lib.optionalAttrs (pkgs.stdenv.hostPlatform.system == "aarch64-linux") {
        sdImage = self.nixosConfigurations.sdImage.config.system.build.sdImage;
      }));

    nixosConfigurations.sdImage = nixpkgs.lib.nixosSystem {
      system = "aarch64-linux";
      modules = [
        "${nixpkgs}/nixos/modules/installer/sd-card/sd-image-aarch64.nix"
        nixos-hardware.nixosModules.raspberry-pi-4
        self.nixosModules.default
        {
          system.activationScripts.bootFirmware = ''
            mkdir -p /boot/firmware
          '';
          sdImage.firmwareSize = 512;
          boot.kernelParams = ["cma=128M"];
        }
      ];
    };

    checks = forAllSystems (pkgs: let
      sys = pkgs.stdenv.hostPlatform.system;
    in
      {
        nixpilot-selftest = pkgs.callPackage ./checks.nix {
          inherit
            (self.packages.${sys})
            nixpilot-mcp
            nixpilot-keyboard-mcp
            nixpilot-screen-mcp
            ;
        };

        eval-minimal = mkEvalCheck sys "minimal" {
          services.nixpilot = {
            backend.enable = true;
            ustreamer.enable = true;
            usb-gadget = {
              enable = true;
              enableKeyboard = true;
              enableMouse = true;
              enableGamepad = false;
            };
            mcp.enable = false;
            caddy.enable = false;
          };
        };

        eval-mcp-no-gamepad = mkEvalCheck sys "mcp-no-gamepad" {
          services.nixpilot = {
            backend.enable = true;
            ustreamer.enable = true;
            usb-gadget = {
              enable = true;
              enableKeyboard = true;
              enableMouse = true;
              enableGamepad = false;
            };
            mcp.enable = true;
            caddy.enable = false;
          };
        };

        eval-full = mkEvalCheck sys "full" {
          services.nixpilot = {
            backend.enable = true;
            ustreamer.enable = true;
            usb-gadget = {
              enable = true;
              enableKeyboard = true;
              enableMouse = true;
              enableGamepad = true;
            };
            mcp.enable = true;
            caddy.enable = true;
          };
        };

        eval-custom = mkEvalCheck sys "custom" {
          services.nixpilot = {
            backend.enable = true;
            ustreamer.enable = true;
            usb-gadget = {
              enable = true;
              enableKeyboard = true;
              enableMouse = false;
              enableGamepad = true;
            };
            mcp = {
              enable = true;
              screen.enable = true;
            };
            caddy.enable = false;
          };
        };
      }
      // (nixpkgs.lib.optionalAttrs (sys == "aarch64-linux") {
        eval-sd-image = let
          evalProof = builtins.unsafeDiscardStringContext self.nixosConfigurations.sdImage.config.system.build.sdImage.drvPath;
        in
          pkgs.writeText "eval-check-sd-image-${sys}" evalProof;
      }));

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
