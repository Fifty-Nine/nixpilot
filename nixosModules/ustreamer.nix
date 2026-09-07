{
  lib,
  config,
  pkgs,
  ...
}: let
  cfg = config.services.nixpilot.ustreamer;
in {
  options.services.nixpilot.ustreamer = {
    enable = lib.mkEnableOption "TinyPilot MJPEG video capture service (uStreamer on the TC358743 HDMI-to-CSI bridge)";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.ustreamer;
      description = "The uStreamer package to run.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 48001;
      description = "TCP port served by uStreamer.";
    };

    lanBind = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Listen on all interfaces instead of loopback only.";
    };

    device = lib.mkOption {
      type = lib.types.str;
      default = "/dev/video0";
      description = "Video capture device node.";
    };

    edid.file = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = ./edid-pi4.hex;
      description = "EDID file in v4l2-ctl set-edid hex-text format.";
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.ustreamer = {
      isSystemUser = true;
      group = "ustreamer";
      extraGroups = ["video"];
    };
    users.groups.ustreamer = {};

    systemd.services = {
      tinypilot-load-edid = lib.mkIf (cfg.edid.file != null) {
        description = "TinyPilot HDMI capture EDID load";
        wantedBy = ["local-fs.target"];
        unitConfig = {
          StartLimitIntervalSec = 300;
          StartLimitBurst = 20;
        };
        serviceConfig = {
          Type = "oneshot";
          ExecStart = "${pkgs.v4l-utils}/bin/v4l2-ctl -d ${cfg.device} --set-edid=file=${cfg.edid.file},format=hex";
          Restart = "on-failure";
          RestartSec = 2;
        };
      };

      tinypilot-ustreamer = {
        description = "TinyPilot MJPEG video stream (uStreamer)";
        wantedBy = ["multi-user.target"];
        after =
          ["systemd-modules-load.service"]
          ++ lib.optionals (cfg.edid.file != null) ["tinypilot-load-edid.service"];
        unitConfig = {
          StartLimitIntervalSec = 300;
          StartLimitBurst = 20;
        };
        serviceConfig = {
          Type = "simple";
          User = "ustreamer";
          Group = "ustreamer";
          ExecStart = ''
            ${cfg.package}/bin/ustreamer \
              --device=${cfg.device} \
              --host=${
              if cfg.lanBind
              then "0.0.0.0"
              else "127.0.0.1"
            } \
              --port=${toString cfg.port} \
              --format=UYVY \
              --encoder=m2m-image \
              --workers=3 \
              --persistent \
              --drop-same-frames=30 \
              --buffers=3
          '';
          Restart = "always";
          RestartSec = 2;
          ProtectSystem = "strict";
          ProtectHome = true;
          PrivateTmp = true;
          SupplementaryGroups = ["video"];
        };
      };
    };
  };
}
