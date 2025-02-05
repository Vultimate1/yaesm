# -*- mode: ruby -*-
# vi: set ft=ruby :

Vagrant.configure("2") do |config|
  config.vm.define "yaesm-pytest" do |config| 
    
    config.vm.box = "archlinux/archlinux"

    config.vm.provider "virtualbox" do |vb|
      vb.gui = false
    end

    # rsync is one-way, so modifications on guest don't affect host
    config.vm.synced_folder ".", "/home/vagrant/yaesm", type: "rsync"

    config.vm.provision "shell", inline: <<-SHELL
      # PACMAN
      pacman -Syu --noconfirm
      pacman -S --noconfirm base-devel git

      # YAY
      sudo -u vagrant git clone https://aur.archlinux.org/yay.git /tmp/yay
      ( cd /tmp/yay && sudo -u vagrant makepkg -si --noconfirm )
      rm -rf /tmp/yay
      sudo -u vagrant yay -Syu --noconfirm

      # PYTHON/PYTEST
      pacman -S --noconfirm python3 python-pytest

      # RSYNC
      pacman -S --noconfirm rsync

      # BTRFS
      pacman -S --noconfirm btrfs-progs

      # ZFS
      sudo -u vagrant yay -S --noconfirm zfs-linux zfs-utils
    SHELL
  end
end
