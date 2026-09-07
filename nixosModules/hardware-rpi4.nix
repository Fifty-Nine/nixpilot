{
  lib,
  pkgs,
  inputs,
  ...
}: {
  hardware = {
    i2c.enable = lib.mkDefault true;
    raspberry-pi = {
      firmware = {
        enable = lib.mkDefault true;
        uboot.enable = lib.mkDefault true;
      };
      "4".gpio.enable = lib.mkDefault true;
      configtxt = {
        settings.all = {
          dtparam = [
            "i2c_arm=on"
            "i2c_vc=on"
            "spi=on"
          ];
        };
        deviceTreeOverlays.all = [
          {
            dwc2 = {
              dr_mode = "peripheral";
            };
          }
          {tc358743 = {};}
          {"tc358743-audio" = {};}
          {
            "gpio-fan" = {
              gpiopin = 2;
              temp = 60000;
              hyst = 10000;
            };
          }
          {"vc4-kms-v3d" = {};}
        ];
      };
    };
  };

  fileSystems."/boot/firmware".options = lib.mkForce ["nofail"];

  system = {
    activationScripts = {
      rpi-firmware-mount = {
        text = ''
          # Ensure /boot/firmware is mounted before staging firmware
          if ! mountpoint -q /boot/firmware; then
            mount /boot/firmware || true
          fi
        '';
      };
      raspberry-pi-firmware = {
        deps = ["rpi-firmware-mount"];
      };
    };
  };

  sdImage.firmwareSize = lib.mkDefault 512;

  boot = {
    kernelParams = ["cma=128M"];
    supportedFilesystems.zfs = lib.mkForce false;
    initrd.availableKernelModules = [
      "pcie-brcmstb"
      "reset-raspberrypi"
      "usb-storage"
      "usbhid"
      "vc4"
      "mmc_block"
    ];

    kernelPackages = pkgs.linuxPackagesFor (pkgs.callPackage (inputs.nixos-hardware + "/raspberry-pi/common/kernel.nix") {
      rpiVersion = 4;
      argsOverride = {
        structuredExtraConfig = with lib.kernel; {
          DEBUG_INFO = lib.mkForce no;
          DEBUG_INFO_BTF = lib.mkForce no;
        };
      };
    });
  };

  zramSwap.enable = lib.mkDefault true;
}
