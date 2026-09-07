{
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.services.nixpilot.ustreamer;
in {
  options.services.nixpilot.ustreamer = {
    enable = lib.mkEnableOption "uStreamer video capture service for TinyPilot";
    port = lib.mkOption {
      type = lib.types.port;
      default = 48001;
      description = "Port for uStreamer loopback HTTP.";
    };
    lanBind = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Whether to bind uStreamer to all interfaces instead of just loopback.";
    };
    device = lib.mkOption {
      type = lib.types.str;
      default = "/dev/video0";
      description = "The V4L2 device for HDMI-to-CSI bridge.";
    };
    edid = {
      file = lib.mkOption {
        type = lib.types.nullOr lib.types.path;
        default = null;
        description = "Path to the EDID file to load before starting uStreamer. null disables EDID loading.";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.nixpilot-ustreamer = {
      description = "uStreamer video capture";
      wantedBy = ["multi-user.target"];
      after = ["network.target"];

      preStart = lib.mkIf (cfg.edid.file != null) ''
        ${pkgs.v4l-utils}/bin/v4l2-ctl -d ${cfg.device} --set-edid=file=${cfg.edid.file} --fix-edid-checksums || true
      '';

      serviceConfig = {
        ExecStart = ''
          ${pkgs.ustreamer}/bin/ustreamer \
            --device ${cfg.device} \
            --host ${
            if cfg.lanBind
            then "0.0.0.0"
            else "127.0.0.1"
          } \
            --port ${toString cfg.port} \
            --format=jpeg \
            --resolution=1920x1080 \
            --desired-fps=30 \
            --drop-same-frames=30
        '';
        Restart = "always";
        DynamicUser = true;
        SupplementaryGroups = ["video"];
      };
    };
  };
}
