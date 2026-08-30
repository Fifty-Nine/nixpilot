# Offline check: run the profile selftests (pure source gates, no device
# access) against the packaged modules.
{
  runCommand,
  nixpilot-mcp,
  nixpilot-keyboard-mcp,
  nixpilot-screen-mcp,
}:
runCommand "nixpilot-selftest" {} ''
  export PYTHONPATH=${nixpilot-mcp}/share
  ${nixpilot-mcp.nixpilotPython}/bin/python3 -m nixpilot.gamepad.selftest
  ${nixpilot-keyboard-mcp.nixpilotPython}/bin/python3 -m nixpilot.keyboard.selftest
  ${nixpilot-screen-mcp.nixpilotPython}/bin/python3 -m nixpilot.screen.selftest
  touch $out
''
