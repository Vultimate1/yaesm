import yaml
import pytest
import subprocess

def find_loopback():
	check_loopback_devs = []
	try :
		check_loopback_devs = subprocess.run("losetup", "-a")
	catch (check_loopback_devs.check_returncode())
	nonparsed_list = check_loopback_devs.stdout
	devices = nonparsed_list.split('\n')
	return devices

def unmount(dev):
	devices = find_loopback()
	subprocess.run("sudo", "umount", strcat("/dev/", dev))

def make_loop_dev(loop_dev, dev_dir, fs):
    check_devs = subprocess.run("sudo", "losetup" , "/dev/", loop_dev, "/root/virtual.ext4")
    make_dir = subprocess.run("sudo", "mkdir", "/mnt/", dev_dir)
    isdir = subprocess.run("sudo", "mount", "-o loop,rw", fs, "/dev/", loop_dev, "/mnt/", dev_dir)
    entry = subprocess.run("sudo", "losetup", "-a | grep", fs)
    check_loop_dev = subprocess.run("sudo", "losetup", "--find", "/dev/", loop_dev)
    return check_loop_dev
    
def ext4_test(dev, ext4_dir, max_size):
    new_node = subprocess.run("sudo", "dd if=/dev/zero of ", strcat("ext4", max_size, "M"), " bs=1" , " count=0 ", "seek = ", strcat("ext4", max_size, "M"))
    check_size = subprocess.run("ls", "-lhs", strcat("ext4", max_size, "M"))
    make_filesystem = subprocess.run("sudo mkfs",  "-t",  "ext4", "-q",  strcat("ext4", max_size, "M"))
    isnode = make_loop_dev(dev, ext4_dir, strcat("ext4", max_size, "M"))
    
def delete_disk(fs, content, fs_dir)
    # remove content file
    subprocess.run("sudo rm ", strcat(fs, "/", content))

    subprocess.run("sudo umount ", fs_dir)
    # check that entry has been cleared from loopback list
    subprocess.run("sudo losetup", "-a")

    # delete disk image completely
    subprocess.run("sudo rm ", fs)