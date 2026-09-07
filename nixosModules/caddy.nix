{
  lib,
  config,
  ...
}: let
  cfg = config.services.nixpilot.caddy;
  backendPort = config.services.nixpilot.backend.port;
  ustreamerPort = config.services.nixpilot.ustreamer.port;
in {
  options.services.nixpilot.caddy = {
    enable = lib.mkEnableOption "TinyPilot reverse proxy (Caddy)";

    hostName = lib.mkOption {
      type = lib.types.str;
      default = "localhost";
      description = "Host name to serve the web interface on.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 80;
      description = "Port to serve the web interface on.";
    };

    tls = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Caddy TLS configuration. Set to 'internal' for local certs, an email for Let's Encrypt, or leave null to disable (and use http on port 80).";
    };
  };

  config = lib.mkIf cfg.enable {
    services.caddy = {
      enable = true;

      virtualHosts."${
        if cfg.tls == null
        then "http://"
        else ""
      }${cfg.hostName}:${toString cfg.port}" = {
        extraConfig = ''
          ${
            if cfg.tls != null
            then "tls ${cfg.tls}"
            else ""
          }

          header {
            X-Frame-Options "DENY"
            X-Content-Type-Options "nosniff"
            X-XSS-Protection "1; mode=block"
          }

          handle_errors {
            @502 {
              expression {err.status_code} == 502
            }
            respond @502 "TinyPilot is currently starting up or offline." 502
          }

          handle /stream* {
            forward_auth 127.0.0.1:${toString backendPort} {
              uri /api/auth
            }
            reverse_proxy 127.0.0.1:${toString ustreamerPort} {
              flush_interval -1
            }
          }

          handle /snapshot* {
            forward_auth 127.0.0.1:${toString backendPort} {
              uri /api/auth
            }
            reverse_proxy 127.0.0.1:${toString ustreamerPort}
          }

          handle /state* {
            forward_auth 127.0.0.1:${toString backendPort} {
              uri /api/auth
            }
            reverse_proxy 127.0.0.1:${toString ustreamerPort}
          }

          reverse_proxy /* 127.0.0.1:${toString backendPort}
        '';
      };
    };
  };
}
