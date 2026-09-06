{pkgs, ...}: let
  backendDeps = ps:
    with ps; [
      eventlet
      flask
      flask-socketio
      flask-wtf
      python-dotenv
      passlib
      pyyaml
    ];
in
  pkgs.stdenv.mkDerivation {
    pname = "tinypilot-backend";
    version = "2.1.0";
    src = pkgs.fetchFromGitHub {
      owner = "tiny-pilot";
      repo = "tinypilot";
      rev = "94367fb09e15da6d7b3c56db2a89abf059da4aad";
      hash = "sha256-dUvjK40ZrLNpUflbzDV+I2pwchm1c09XJdzUbKcEaGY=";
    };
    nativeBuildInputs = [pkgs.makeWrapper];
    postPatch = ''
      substituteInPlace app/local_system.py app/debug_logs.py app/update_logs.py \
        --replace-fail "/usr/bin/sudo" "sudo"
      substituteInPlace app/local_system.py \
        --replace-fail "/sbin/shutdown" "${pkgs.systemd}/bin/shutdown"
    '';
    dontBuild = true;
    installPhase = ''
      runHook preInstall
      mkdir -p $out/share/tinypilot $out/bin
      cp -r . $out/share/tinypilot
      makeWrapper ${pkgs.python3.withPackages backendDeps}/bin/python \
        $out/bin/tinypilot-backend \
        --add-flags "$out/share/tinypilot/app/main.py"
      runHook postInstall
    '';
  }
