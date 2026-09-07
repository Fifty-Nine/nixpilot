{
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.services.nixpilot.usb-gadget;
in {
  options.services.nixpilot.usb-gadget = {
    enable = lib.mkEnableOption "TinyPilot composite USB gadget via configfs";
    enableKeyboard = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Enable keyboard gadget endpoint (hidg0).";
    };
    enableMouse = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Enable mouse gadget endpoint (hidg1).";
    };
  };

  config = lib.mkIf cfg.enable {
    services.udev.extraRules = ''
      KERNEL=="hidg0", SUBSYSTEM=="usbmisc", GROUP="tinypilot", MODE="0660"
      KERNEL=="hidg1", SUBSYSTEM=="usbmisc", GROUP="tinypilot", MODE="0660"
      KERNEL=="hidg2", SUBSYSTEM=="usbmisc", GROUP="tinypilot", MODE="0660"
    '';

    systemd.services.nixpilot-usb-gadget = {
      description = "TinyPilot USB Gadget Configuration";
      wantedBy = ["sysinit.target"];
      before = ["systemd-modules-load.service"];

      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = pkgs.writeShellScript "setup-usb-gadget" ''
          set -e
          modprobe libcomposite || true

          GADGET_DIR="/sys/kernel/config/usb_gadget/tinypilot"
          mkdir -p "$GADGET_DIR"
          cd "$GADGET_DIR"

          mkdir -p strings/0x409
          mkdir -p configs/c.1/strings/0x409

          ${lib.optionalString cfg.enableKeyboard ''
            mkdir -p functions/hid.usb0
            echo 1 > functions/hid.usb0/protocol
            echo 1 > functions/hid.usb0/subclass
            echo 8 > functions/hid.usb0/report_length
            echo -ne \\x05\\x01\\x09\\x06\\xa1\\x01\\x05\\x07\\x19\\xe0\\x29\\xe7\\x15\\x00\\x25\\x01\\x75\\x01\\x95\\x08\\x81\\x02\\x95\\x01\\x75\\x08\\x81\\x03\\x95\\x05\\x75\\x01\\x05\\x08\\x19\\x01\\x29\\x05\\x91\\x02\\x95\\x01\\x75\\x03\\x91\\x03\\x95\\x06\\x75\\x08\\x15\\x00\\x25\\x65\\x05\\x07\\x19\\x00\\x29\\x65\\x81\\x00\\xc0 > functions/hid.usb0/report_desc
            ln -s functions/hid.usb0 configs/c.1/ || true
          ''}

          ${lib.optionalString cfg.enableMouse ''
            mkdir -p functions/hid.usb1
            echo 2 > functions/hid.usb1/protocol
            echo 1 > functions/hid.usb1/subclass
            echo 3 > functions/hid.usb1/report_length
            echo -ne \\x05\\x01\\x09\\x02\\xa1\\x01\\x09\\x01\\xa1\\x00\\x05\\x09\\x19\\x01\\x29\\x03\\x15\\x00\\x25\\x01\\x95\\x03\\x75\\x01\\x81\\x02\\x95\\x01\\x75\\x05\\x81\\x03\\x05\\x01\\x09\\x30\\x09\\x31\\x15\\x81\\x25\\x7f\\x75\\x08\\x95\\x02\\x81\\x06\\xc0\\xc0 > functions/hid.usb1/report_desc
            ln -s functions/hid.usb1 configs/c.1/ || true
          ''}

          # Gamepad endpoint
          mkdir -p functions/hid.usb2
          echo 1 > functions/hid.usb2/protocol
          echo 1 > functions/hid.usb2/subclass
          echo 10 > functions/hid.usb2/report_length
          echo -ne \\x05\\x01\\x09\\x05\\xa1\\x01\\x05\\x09\\x19\\x01\\x29\\x10\\x15\\x00\\x25\\x01\\x95\\x10\\x75\\x01\\x81\\x02\\x05\\x01\\x09\\x30\\x09\\x31\\x09\\x33\\x09\\x34\\x15\\x00\\x26\\xff\\x00\\x75\\x08\\x95\\x04\\x81\\x02\\x09\\x32\\x09\\x35\\x15\\x00\\x26\\xff\\x00\\x75\\x08\\x95\\x02\\x81\\x02\\x09\\x39\\x15\\x01\\x25\\x08\\x35\\x00\\x46\\x3b\\x01\\x65\\x14\\x75\\x04\\x95\\x01\\x81\\x42\\x75\\x04\\x95\\x01\\x81\\x03\\x75\\x08\\x95\\x01\\x81\\x03\\xc0 > functions/hid.usb2/report_desc
          ln -s functions/hid.usb2 configs/c.1/ || true

          ls /sys/class/udc > UDC || true
        '';
        ExecStop = pkgs.writeShellScript "teardown-usb-gadget" ''
          GADGET_DIR="/sys/kernel/config/usb_gadget/tinypilot"
          if [ -d "$GADGET_DIR" ]; then
            echo "" > "$GADGET_DIR/UDC" || true
            rm -f "$GADGET_DIR"/configs/c.1/hid.usb* || true
            rmdir "$GADGET_DIR"/configs/c.1/strings/0x409 || true
            rmdir "$GADGET_DIR"/configs/c.1 || true
            rmdir "$GADGET_DIR"/functions/hid.usb* || true
            rmdir "$GADGET_DIR"/strings/0x409 || true
            rmdir "$GADGET_DIR" || true
          fi
        '';
      };
    };
  };
}
