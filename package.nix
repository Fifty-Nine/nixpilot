# nixpilot server package: one MCP profile executable per package instance,
# wrapped as a single PATH binary. The nixpilot package tree is plain source
# copied to $out/share and resolved through PYTHONPATH, so the aedificium
# tinypilot host gets the servers plus a provisioned interpreter
# (python3.withPackages) inside the system closure — no on-device eval, no
# rsync, no GC eviction.
{
  lib,
  python3,
  runCommand,
  makeWrapper,
  pname,
  module,
}: let
  version = "0.1.0";
  pyEnv = python3.withPackages (ps: [ps.mcp]);
in
  runCommand "${pname}-${version}" {
    inherit version;
    meta = {
      description = "MCP server for the NixOS TinyPilot KVM (${module})";
      mainProgram = pname;
      license = lib.licenses.mit;
      platforms = lib.platforms.linux;
    };
    nativeBuildInputs = [makeWrapper];
    # Exposed for dev tooling (lint/type checks against the same env).
    passthru.nixpilotPython = pyEnv;
  } ''
    install -d $out/share
    cp -r ${./nixpilot} $out/share/nixpilot
    mkdir -p $out/bin
    makeWrapper ${pyEnv}/bin/python3 $out/bin/${pname} \
      --set PYTHONPATH $out/share \
      --add-flags '-m ${module}'
  ''
