{
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.services.nixpilot.backend;
in {
  options.services.nixpilot.backend = {
    enable = lib.mkEnableOption "nixpilot backend service";
    port = lib.mkOption {
      type = lib.types.port;
      default = 8000;
      description = "Port to listen on.";
    };
    package = lib.mkOption {
      type = lib.types.package;
      description = "The tinypilot web application package.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.tmpfiles.rules = [
      "d /run/sudo 0711 root root"
    ];

    users.users.tinypilot = {
      isSystemUser = true;
      group = "tinypilot";
    };
    users.groups.tinypilot = {};

    systemd.services.nixpilot-backend = {
      description = "TinyPilot Backend";
      wantedBy = ["multi-user.target"];
      after = ["network.target"];
      serviceConfig = {
        User = "tinypilot";
        Group = "tinypilot";
        ExecStart = "${cfg.package}/bin/tinypilot-backend --port ${toString cfg.port}";
        Restart = "always";
      };
    };
  };
}
