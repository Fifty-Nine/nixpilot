# Offline check: run the profile selftest (pure source gate, no device
# access) against the packaged module.
{
  runCommand,
  nixpilot-mcp,
}:
runCommand "nixpilot-selftest" {} ''
  export PYTHONPATH=${nixpilot-mcp}/share
  ${nixpilot-mcp.nixpilotPython}/bin/python3 -m nixpilot.gamepad.selftest
  touch $out
''
