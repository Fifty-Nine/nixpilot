{
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.services.nixpilot.edid-defaults;
in {
  options.services.nixpilot.edid-defaults = {
    enable = lib.mkEnableOption "Baseline hardware / EDID defaults for Pi 4";
  };

  config = lib.mkIf cfg.enable {
    environment.etc."tinypilot-edid/edid-pi4.hex".source = pkgs.writeText "edid-pi4.hex" ''
      00ffffffffffff005262000000000000
      25150103800000780a0dc9a057479827
      12484c00000001010101010101010101
      010101010101023a801871382d40582c
      450000000000001e000000fd00323c1e
      2e08000a202020202020000000fc0054
      696e7950696c6f740a202020000000ff
      003030303030303030303030300a01ad
      02031a71479004030222122009070715
      07503d07c083010000023a801871382d
      40582c450000000000001e0000000000
      00000000000000000000000000000000
      00000000000000000000000000000000
      00000000000000000000000000000000
      00000000000000000000000000000000
      0000000000000000000000000000001a
    '';

    # Provide the baseline Raspberry Pi 4 baseline module / firmware mount fix snippet
    # so downstream users have working hardware settings out of the box
    boot.kernelModules = ["libcomposite" "dwc2"];
  };
}
