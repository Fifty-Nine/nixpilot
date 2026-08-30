# nixpilot-mcp package: the profile-based MCP server for the gadget's HID
# endpoints, wrapped as a single PATH binary. The server module is plain
# source copied to $out/share and resolved through PYTHONPATH, so the
# aedificium tinypilot host gets the server plus a provisioned interpreter
# (python3.withPackages) inside the system closure — no on-device eval, no
# rsync, no GC eviction.
{
  lib,
  python3,
  runCommand,
  makeWrapper,
}: let
  version = "0.1.0";
  pyEnv = python3.withPackages (ps: [ps.mcp]);
in
  runCommand "nixpilot-mcp-${version}" {
    inherit version;
    meta = {
      description = "MCP servers for the NixOS TinyPilot KVM HID gadgets";
      mainProgram = "nixpilot-mcp";
      license = lib.licenses.mit;
      platforms = lib.platforms.linux;
    };
    nativeBuildInputs = [makeWrapper];
    # Exposed for dev tooling (lint/type checks against the same env).
    passthru.nixpilotPython = pyEnv;
  } ''
    install -d $out/share
    cp -r ${./nixpilot_mcp} $out/share/nixpilot_mcp
    mkdir -p $out/bin
    makeWrapper ${pyEnv}/bin/python3 $out/bin/nixpilot-mcp \
      --set PYTHONPATH $out/share \
      --add-flags '-m nixpilot_mcp'
  ''
