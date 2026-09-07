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
    # mkForce: sd-image-aarch64 sets a broad all-platform module list
    # (dw-hdmi, rockchipdrm, sun4i-drm, ...) with modules the RPi kernel does
    # not build; drop them in favour of the RPi 4 essentials.
    initrd.availableKernelModules = lib.mkForce [
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
        # argsOverride replaces kernel.nix's structuredExtraConfig wholesale, so
        # entries from the rpiVersion = 4 branch of kernel.nix are replicated
        # here (host-tuning entries included: NFS root, IP autoconfig, single
        # CPU for the 2 GB board, small CMA, preempt-none). Re-sync these
        # whenever the nixos-hardware input is updated.
        structuredExtraConfig = with lib.kernel; {
          NR_CPUS = lib.mkForce (freeform "4");
          CMA_SIZE_MBYTES = lib.mkForce (freeform "5");
          NFS_FS = lib.mkForce yes;
          NFS_V4 = yes;
          ROOT_NFS = yes;
          IP_PNP = lib.mkForce yes;
          IP_PNP_DHCP = yes;
          IP_PNP_RARP = yes;
          NET_CLS_BPF = lib.mkForce yes;
          NLS_CODEPAGE_437 = lib.mkForce yes;
          FB_SIMPLE = yes;
          PREEMPT = lib.mkForce yes;
          PREEMPT_LAZY = lib.mkForce no;
          PREEMPT_VOLUNTARY = lib.mkForce no;
          DEBUG_INFO = lib.mkForce (option no);
          DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT = lib.mkForce (option no);
          DEBUG_INFO_BTF = lib.mkForce (option no);
          GDB_SCRIPTS = lib.mkForce (option no);
        };
      };
    });
  };

  zramSwap.enable = lib.mkDefault true;
}
