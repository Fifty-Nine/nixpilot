{
  lib,
  config,
  pkgs,
  ...
}: let
  cfg = config.services.nixpilot.usb-gadget;

  keyboardReport = "BQEJBqEBBQgZASkDFQAlAXUBlQORAglLlQGRApUEkQEFBxngKeeVCIECdQiVAYEBGQApkSb/AJUGgQDA";
  absoluteMouseReport = "BQEJAqEBBQkZASkIFQAlAZUIdQGBAgUBCTAJMRYAACb/f3UQlQKBAgk4FYElf3UIlQGBBgUMCjgCFYElf3UIlQGBBsA=";
  relativeMouseReport = "BQEJAqEBCQGhAAUJGQEpCBUAJQGVCHUBgQIFAQkwCTEWAYAm/391EJUCgQYJOBWBJX91CJUBgQYFDAo4AhWBJX91CJUBgQbAwA==";
  gamepadReport = "BQEJBaEBBQkZASkQFQAlAXUBlRCBAgUBCTAJMQkzCTQJMgk1FQAm/wB1CJUGgQIJORUAJQc1AEY7AWUAdQSVAYFCdQSVAYEDdQiVAYEDwA==";

  gadgetDir = "/sys/kernel/config/usb_gadget/g1";

  removeGadget = pkgs.writeShellScript "tinypilot-usb-gadget-remove" ''
    set -eu
    if [ ! -d ${gadgetDir} ]; then
      exit 0
    fi
    cd ${gadgetDir}
    if [ -n "$(cat UDC 2>/dev/null)" ]; then
      : > UDC
    fi
    for c in configs/c.*; do
      if [ -e "$c" ]; then
        for f in "$c"/hid.*; do
          rm -f "$f"
        done
        if [ -d "$c/strings/0x409" ]; then
          rmdir "$c/strings/0x409" || true
        fi
        rmdir "$c" || true
      fi
    done
    for f in functions/hid.*; do
      if [ -d "$f" ]; then
        rmdir "$f" || true
      fi
    done
    if [ -d strings/0x409 ]; then
      rmdir strings/0x409 || true
    fi
    cd /
    rmdir ${gadgetDir} || true
  '';

  initGadget = pkgs.writeShellScript "tinypilot-usb-gadget-init" ''
    set -eu
    mkdir -p ${gadgetDir}
    cd ${gadgetDir}

    echo 0x1d6b > idVendor # Linux Foundation
    echo 0x0104 > idProduct # Multifunction Composite Gadget
    echo 0x0100 > bcdDevice
    echo 0x0200 > bcdUSB

    mkdir -p strings/0x409
    echo 6b65796d696d6570690 > strings/0x409/serialnumber
    echo tinypilot > strings/0x409/manufacturer
    echo "Multifunction USB Device" > strings/0x409/product

    ${lib.optionalString cfg.enableKeyboard ''
      mkdir -p functions/hid.keyboard
      echo 1 > functions/hid.keyboard/protocol
      echo 1 > functions/hid.keyboard/subclass
      echo 8 > functions/hid.keyboard/report_length
      echo ${keyboardReport} | base64 -d > functions/hid.keyboard/report_desc
      if [ -e functions/hid.keyboard/no_out_endpoint ]; then
        echo 1 > functions/hid.keyboard/no_out_endpoint
      fi
    ''}

    ${lib.optionalString cfg.enableMouse ''
      mkdir -p functions/hid.mouse_absolute
      echo 0 > functions/hid.mouse_absolute/protocol
      echo 0 > functions/hid.mouse_absolute/subclass
      echo 7 > functions/hid.mouse_absolute/report_length
      echo ${absoluteMouseReport} | base64 -d > functions/hid.mouse_absolute/report_desc

      mkdir -p functions/hid.mouse_relative
      echo 0 > functions/hid.mouse_relative/protocol
      echo 0 > functions/hid.mouse_relative/subclass
      # 7-byte max report size, verbatim from the vendor script.
      echo 7 > functions/hid.mouse_relative/report_length
      echo ${relativeMouseReport} | base64 -d > functions/hid.mouse_relative/report_desc
      if [ -e functions/hid.mouse_relative/no_out_endpoint ]; then
        echo 1 > functions/hid.mouse_relative/no_out_endpoint
      fi
    ''}

    ${lib.optionalString cfg.enableGamepad ''
      mkdir -p functions/hid.gamepad
      echo 0 > functions/hid.gamepad/protocol
      echo 0 > functions/hid.gamepad/subclass
      echo 10 > functions/hid.gamepad/report_length
      echo ${gamepadReport} | base64 -d > functions/hid.gamepad/report_desc
      # IN-only: no host output reports (no rumble/LED feedback).
      if [ -e functions/hid.gamepad/no_out_endpoint ]; then
        echo 1 > functions/hid.gamepad/no_out_endpoint
      fi
    ''}

    mkdir -p configs/c.1
    echo 250 > configs/c.1/MaxPower
    mkdir -p configs/c.1/strings/0x409
    echo "TinyPilot Config" > configs/c.1/strings/0x409/configuration

    ${lib.optionalString cfg.enableKeyboard ''
      ln -s functions/hid.keyboard configs/c.1/
    ''}
    ${lib.optionalString cfg.enableMouse ''
      ln -s functions/hid.mouse_absolute configs/c.1/
      ln -s functions/hid.mouse_relative configs/c.1/
    ''}
    ${lib.optionalString cfg.enableGamepad ''
      ln -s functions/hid.gamepad configs/c.1/
    ''}

    udevadm settle -t 5 || true
    ls /sys/class/udc > UDC
  '';
in {
  options.services.nixpilot.usb-gadget = {
    enable = lib.mkEnableOption "TinyPilot USB HID composite gadget via configfs";

    enableKeyboard = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Present standard keyboard interface (/dev/hidg0).";
    };

    enableMouse = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Present absolute (/dev/hidg1) and relative (/dev/hidg2) mouse interfaces.";
    };

    enableGamepad = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Present standard gamepad interface (/dev/hidg3).";
    };
  };

  config = lib.mkIf cfg.enable {
    users.groups.usb-gadget = {};

    services.udev.extraRules = ''
      KERNEL=="hidg*", SUBSYSTEM=="hidg", GROUP="usb-gadget", MODE="0660"
    '';

    boot.kernelModules = ["libcomposite" "dwc2"];

    systemd.services.tinypilot-usb-gadget = {
      description = "TinyPilot USB HID gadget";
      wantedBy = ["multi-user.target"];
      after = ["systemd-modules-load.service"];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStartPre = removeGadget;
        ExecStart = initGadget;
        ExecStop = removeGadget;
      };
    };
  };
}
