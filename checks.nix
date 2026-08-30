# Offline check: run the report-codec selftest against the packaged module.
{
  runCommand,
  nixpilot-mcp,
}:
runCommand "nixpilot-mcp-selftest" {} ''
  export PYTHONPATH=${nixpilot-mcp}/share
  ${nixpilot-mcp.nixpilotPython}/bin/python3 -m nixpilot_mcp.selftest
  touch $out
''
