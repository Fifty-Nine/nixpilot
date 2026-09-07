{
  lib,
  pkgs,
  inputs,
  ...
}: {
  hardware.i2c.enable = lib.mkDefault true;
  hardware.raspberry-pi.firmware.enable = lib.mkDefault true;
  hardware.raspberry-pi.firmware.uboot.enable = lib.mkDefault true;
  hardware.raspberry-pi."4".gpio.enable = lib.mkDefault true;
  hardware.raspberry-pi.configtxt.file = lib.mkDefault ./config.txt;

  fileSystems."/boot/firmware".options = lib.mkForce ["nofail"];

  system.activationScripts.rpi-firmware-mount = {
    text = ''
      # Ensure /boot/firmware is mounted before staging firmware
      if ! mountpoint -q /boot/firmware; then
        mount /boot/firmware || true
      fi
    '';
  };

  system.activationScripts.raspberry-pi-firmware = {
    deps = ["rpi-firmware-mount"];
  };

  sdImage.firmwareSize = lib.mkDefault 512;
  boot.kernelParams = ["cma=128M"];
  boot.supportedFilesystems.zfs = lib.mkForce false;
  boot.initrd.availableKernelModules = [
    "pcie-brcmstb"
    "reset-raspberrypi"
    "usb-storage"
    "usbhid"
    "vc4"
    "mmc_block"
  ];

  boot.kernelPackages = pkgs.linuxPackagesFor (pkgs.callPackage (inputs.nixos-hardware + "/raspberry-pi/common/kernel.nix") {
    rpiVersion = 4;
    # Override kernel config for faster compilation by disabling debug info and BTF
    extraConfig = ''
      DEBUG_INFO n
      DEBUG_INFO_BTF n
    '';
  });

  zramSwap.enable = lib.mkDefault true;
}
