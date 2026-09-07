# nixpilot: Reusable NixOS modules and MCP servers for TinyPilot KVM appliances.
{
  description = "Reusable NixOS modules and MCP servers for TinyPilot KVM appliances";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.nixos-hardware.url = "github:NixOS/nixos-hardware";
  inputs.nixos-hardware.inputs.nixpkgs.follows = "nixpkgs";

  outputs = {
    self,
    nixpkgs,
    nixos-hardware,
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
      system = pkgs.stdenv.hostPlatform.system;

      evalCheck = name: config:
        pkgs.runCommand name {} ''
          # ${builtins.unsafeDiscardStringContext
            (nixpkgs.lib.nixosSystem {
              inherit system;
              modules = [
                ./nixosModules/backend.nix
                ./nixosModules/ustreamer.nix
                ./nixosModules/usb-gadget.nix
                ./nixosModules/mcp.nix
                ./nixosModules/caddy.nix
                {
                  boot.loader.grub.enable = false;
                  fileSystems."/" = {
                    device = "/dev/sda1";
                    fsType = "ext4";
                  };
                  system.stateVersion = "24.11";
                  nixpkgs.hostPlatform = system;
                }
                config
              ];
            }).config.system.build.toplevel.drvPath}
          touch $out
        '';
    in
      {
        nixpilot-selftest = pkgs.callPackage ./checks.nix {
          inherit
            (self.packages.${system})
            nixpilot-mcp
            nixpilot-keyboard-mcp
            nixpilot-screen-mcp
            ;
        };

        eval-minimal = evalCheck "eval-minimal" {
          services.nixpilot = {
            usb-gadget.enableKeyboard = true;
            usb-gadget.enableMouse = true;
            ustreamer.enable = true;
            backend.enable = true;
          };
        };

        eval-mcp-no-gamepad = evalCheck "eval-mcp-no-gamepad" {
          services.nixpilot = {
            usb-gadget.enableKeyboard = true;
            usb-gadget.enableMouse = true;
            ustreamer.enable = true;
            backend.enable = true;
            mcp.enable = true;
          };
        };

        eval-full = evalCheck "eval-full" {
          services.nixpilot = {
            usb-gadget.enableKeyboard = true;
            usb-gadget.enableMouse = true;
            usb-gadget.enableGamepad = true;
            ustreamer.enable = true;
            backend.enable = true;
            mcp.enable = true;
            caddy.enable = true;
          };
        };

        eval-custom = evalCheck "eval-custom" {
          services.nixpilot = {
            usb-gadget.enableKeyboard = true;
            usb-gadget.enableMouse = false;
            usb-gadget.enableGamepad = true;
            mcp.enable = true;
          };
        };
      }
      // (nixpkgs.lib.optionalAttrs (system == "aarch64-linux") {
        eval-sd-image = pkgs.runCommand "eval-sd-image" {} ''
          # ${builtins.unsafeDiscardStringContext self.nixosConfigurations.sdImage.config.system.build.sdImage.drvPath}
          touch $out
        '';
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
