
import yaesm.backend.ext4backend as ext4backend

import abc
from typing import final
from pathlib import Path
import pytest

from yaesm.timeframe import Timeframe
from yaesm.sshtarget import SSHTarget
import subprocess
import re

#@pytest.fixture
#def ext4():
#    return ext4backend.EXT4Backend(src_dir="/src", dst_dir="/dest")

def test_get_fs_details():
    block_dev, fs_type, block_size, num_blocks = ext4backend.get_fs_details("/dev/sdc")
    assert fs_type == "ext4"
    assert (block_size == "1K" or block_size == "4K")
    assert int(num_blocks)>10000

def test_make_ext4():
    num_blocks, num_inodes, block_size, uuid, journal_size = ext4backend.make_partition("/dev/sdc")
    assert float(num_blocks) > 0
    assert float(num_inodes) > 0
    assert block_size == "1k" or block_size == "4k"
    assert len(uuid)>=36
    cmdout = subprocess.run(["sudo", "lsblk"], capture_output=True).stdout
    type = cmdout.split('\n')[1].split()[0]
    assert type=='ext4'
    dev_items = []
    devs = subprocess.run(["sudo", "lsblk"]).stdout.split('\n').split()
    i = 0
    for dev in devs:
        items = dev.split()
        if i > 0:
             dev_items.append(items)
             for item in items:
                 print(item)
        i=i+1
    assert "/dev/sdc1" in dev_items

def get_num_inodes():
    num_inodes = ext4backend.get_num_inodes("/sda1")
    assert int(num_inodes) > 10000000
    subprocess.run(["sudo", "umount", "/mnt/sda1"], capture_output=True)
    subprocess.run(["sudo", "rm", "-r", "/mnt/sda1"], capture_output=True)
    assert int(num_inodes) > 10000000
    subprocess.run(["sudo", "umount", "/mnt/sdb"], capture_output=True)
    subprocess.run(["sudo", "rm", "-r", "/mnt/sdb"], capture_output=True)

def test_find_curr_ext4():
    #subprocess.run(["sudo", "dd if=/dev/zero of=img.img bs=1M count=1024"], capture_output=True)
    #subprocess.run(["sudo", "mkfs.ext4", "img.img"], capture_output=True)
    #subprocess.run(["sudo", "losetup", "/dev/loop0", "img.img"], capture_output=True)
    #subprocess.run(["sudo", "losetup", "-a"], capture_output=True)
    #subprocess.run(["sudo", "mkdir", "/loop0"], capture_output=True)
    #subprocess.run(["sudo", "mount", "/dev/loop0", "/mnt"], capture_output=True)
    loc = ext4backend.find_curr_ext4()
    assert ("/dev/sdc" in str(loc))
    #subprocess.run(["sudo", "umount", "/mnt/loop0"], capture_output=True)
    #subprocess.run(["sudo", "rm", "-r", "/mnt/loop0"], capture_output=True)
    #subprocess.run(["sudo" ,"losetup", "-d", "/dev/loop0"], capture_output=True)
    #assert not ("loop0" in str(loc))

def test_create_volume_partitions():
    output = ext4backend.create_volume_partitions("/dev/sdc")
    assert "/dev/sdc1" in output and "/dev/sdc2" in output
    output = subprocess.run(["sudo", "lsblk"], capture_output=True).stdout.decode('utf-8')
    assert "/dev/sdc1" in output and "/dev/sdc2" in output
