{
  lib,
  config,
  pkgs,
  ...
}: let
  cfg = config.services.nixpilot.mcp;
in {
  options.services.nixpilot.mcp = {
    enable = lib.mkEnableOption "NixPilot MCP services";

    gamepad = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = config.services.nixpilot.usb-gadget.enableGamepad && cfg.enable;
        defaultText = lib.literalExpression "config.services.nixpilot.usb-gadget.enableGamepad && config.services.nixpilot.mcp.enable";
        description = "Enable the MCP server for the gamepad.";
      };
      package = lib.mkOption {
        type = lib.types.package;
        default = pkgs.callPackage ../package.nix {
          pname = "nixpilot-mcp";
          module = "nixpilot.gamepad";
        };
        description = "The package to use for the gamepad MCP server.";
      };
    };

    keyboard = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = config.services.nixpilot.usb-gadget.enableKeyboard && cfg.enable;
        defaultText = lib.literalExpression "config.services.nixpilot.usb-gadget.enableKeyboard && config.services.nixpilot.mcp.enable";
        description = "Enable the MCP server for the keyboard.";
      };
      package = lib.mkOption {
        type = lib.types.package;
        default = pkgs.callPackage ../package.nix {
          pname = "nixpilot-keyboard-mcp";
          module = "nixpilot.keyboard";
        };
        description = "The package to use for the keyboard MCP server.";
      };
    };

    screen = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = cfg.enable;
        defaultText = lib.literalExpression "config.services.nixpilot.mcp.enable";
        description = "Enable the MCP server for the screen.";
      };
      package = lib.mkOption {
        type = lib.types.package;
        default = pkgs.callPackage ../package.nix {
          pname = "nixpilot-screen-mcp";
          module = "nixpilot.screen";
        };
        description = "The package to use for the screen MCP server.";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages =
      lib.optional cfg.gamepad.enable cfg.gamepad.package
      ++ lib.optional cfg.keyboard.enable cfg.keyboard.package
      ++ lib.optional cfg.screen.enable cfg.screen.package;
  };
}
