{
  lib,
  config,
  pkgs,
  ...
}: let
  cfg = config.services.nixpilot.backend;
in {
  options.services.nixpilot.backend = {
    enable = lib.mkEnableOption "TinyPilot web backend (upstream open-source Python application)";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ../pkgs/tinypilot-backend.nix {};
      description = "The TinyPilot Python application package to run.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 48000;
      description = "TCP port served on loopback, which the reverse proxy routes to.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.tmpfiles.rules = ["d /run/sudo 0711 root root -"];

    security.sudo.extraRules = [
      {
        users = ["tinypilot"];
        commands = [
          {
            command = "${pkgs.systemd}/bin/shutdown";
            options = ["NOPASSWD"];
          }
        ];
      }
    ];

    users.users.tinypilot = {
      isSystemUser = true;
      group = "tinypilot";
      home = "/var/lib/tinypilot";
      extraGroups = ["usb-gadget"];
    };
    users.groups.tinypilot = {};

    systemd.services.nixpilot-backend = {
      description = "TinyPilot Web Backend";
      wantedBy = ["multi-user.target"];
      after = ["network.target" "systemd-tmpfiles-setup.service"];
      environment = {
        PORT = toString cfg.port;
        HOST = "127.0.0.1";
        # The backend reads runtime state (sqlite settings DB, users DB,
        # flask secret key, settings.yml) from its home dir, which the
        # vendor located at /home/tinypilot.
        TINYPILOT_HOME_DIR = "/var/lib/tinypilot";
        CONFIGURATION_FILE = "/var/lib/tinypilot/settings.yml";
      };
      serviceConfig = {
        ExecStart = "${cfg.package}/bin/tinypilot-backend";
        User = "tinypilot";
        Group = "tinypilot";
        WorkingDirectory = "${cfg.package}/share/tinypilot";
        StateDirectory = "tinypilot";
        StateDirectoryMode = "0750";
        Restart = "always";
        RestartSec = 2;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = false;
        ReadWritePaths = [
          "/var/lib/tinypilot"
          "/run/sudo"
        ];
      };
    };
  };
}
