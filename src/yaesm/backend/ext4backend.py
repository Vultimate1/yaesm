import abc
from typing import final
from pathlib import Path

from yaesm.backend.backendbase import BackendBase
import yaesm.backup as bckp
from yaesm.timeframe import Timeframe
from yaesm.sshtarget import SSHTarget
import subprocess
import re

class EXT4Backend(BackendBase):
    """Abstract base class for execution backend classes such as RsyncBackend
    and BtrfsBackend. An actual backend class inherits from BackendBase, and
    implements the methods '_exec_backup_local_to_local()',
    '_exec_backup_local_to_remote()', '_exec_backup_remote_to_local()',
    '_delete_backups_local()', and '_delete_backups_remote()' . Any code using a
    backend only needs to interact with the 'do_backup()' method, which is
    defined in this class.
    """

    #def __init__(self, backup:bckp.Backup, timeframe:Timeframe, src_dir, dst_dir):
    #    """Perform a backup of 'backup' for the Timeframe 'timeframe'."""
        # Assumes backup contains src_dir and dst_dir
        #super().__init__(backup, timeframe)
        #if isinstance(backup.dst_dir, SSHTarget):
        #    self.dst_dir = backup.dst_dir.with_path(dst_dir.path.joinpath(timeframe.name)) # Add backup to folder for dst_dir at given timeframe
        #else:
        #    self.dst_dir = dst_dir.joinpath(timeframe.name) # creates a folder for the timeframe at given dst_dir
        #if backup.backup_type == "local_to_local":
        #    self._exec_backup_local_to_local(src_dir, dst_dir) # execute local to local backup for EXT4 filesystems
        #elif backup.backup_type == "local_to_remote":
        #    self._exec_backup_local_to_remote(src_dir, dst_dir) # execute local to remote backup for EXT4 filesystem
        #else: # remote_to_local
        #    self._exec_backup_remote_to_local(timeframe)
        #backups = bckp.backups_collect(dst_dir) # sorted newest to oldest
        #to_delete = []
        #while len(backups) > timeframe.keep:
        #    to_delete.append(backups.pop())
        #if to_delete:
        #    if (isinstance(dst_dir, SSHTarget)):
        #        self._delete_backups_remote(*to_delete)
        #    else:
        #        self._delete_backups_local(*to_delete)
    #    self.src_dir = src_dir
    #    self.dst_dir = dst_dir
    #    fs_details = get_fs_details(self.src_dir)
    #    self.block_size = fs_details[2]
    #    self.num_inodes = self.get_num_inodes(fs_details[0])
    #    self.num_blocks = fs_details[3]
    #    self.fs_type = fs_details[1]

    def _exec_backup_local_to_local(self, src_dir:Path, dst_dir:Path, check=True):
        """Execute a single local to local backup instance of 'src_dir' and place it in
        'dst_dir', which should represent an existing directory on the local
        system. Does not perform any cleanup on the file system or the hard drive.
        """
        vols = []
        results = subprocess.run(["sudo", "pvs"]).stdout.split('\n')
        for line in results:
             details = line.split()
             vols = details[0]
        src_exists = src_dir.is_dir()
        dst_exists = dst_dir.is_dir()

        #ret = subprocess.run(["sudo", "dump", "-0uf", backup, dst_dir.pathname], check=check).returncode
        snapshot = self._ext4_bootstrap_snapshot_basename()
        self.make_local_snapshot(src_dir, dst_dir)


    def _exec_backup_local_to_remote(self, src_dir:Path, dst_dir:SSHTarget):
        """Execute a single local to remote backup of 'src_dir' and place it in
        the SSHTarget 'dst_dir', which should have a .path representing an
        existing directory on the remote server. Does not perform any cleanup.
        """
        ...

    def _exec_backup_remote_to_local(self, src_dir:SSHTarget, dst_dir:Path):
        """Execute a single remote to local backup of the SSHTarget 'src_dir' and
        place it in 'dst_dir', which should represent an existing directory on
        the local system. Does not perform any cleanup.
        """
        ...

    def _delete_backups_local(self, *backups):
        """Delete all the local backups in '*backups' (Paths)."""
        ...

    def _delete_backups_remote(self, *backups):
        """Delete all the remote backups in '*backups' (SSHTargets)."""
        ...

def make_local_snapshot(self, src_dir:Path, dst_dir:Path):
        fs = self.get_fs_details(src_dir)
        fs_dev = fs[0]
        vols = self.mount_to_LVM(fs_dev)
        is_created = self.create_vol_group(fs_dev)
        snap_name = self.dst_dir.joinpath(self.timeframe.name)
        ret = 0
        if (is_created):
             ret = subprocess.run(["sudo", "lvcreate", "--size", "10G", "--snapshot", "--name", snap_name, dst_dir], capture_output=True).returncode
        return ret==0

def _ext4_bootstrap_snapshot_basename(self):
        """Return the basename of a btrfs bootstrap snapshot."""
        return ".yaesm-ext4-bootstrap-snapshot"

def make_partition(block_dev):
        cmdout = subprocess.run(["sudo", "parted", block_dev], capture_output=True)
        cmdout = subprocess.run(["sudo", "mkpart", "primary", "ext4", "1MiB", "100GiB", block_dev], capture_output=True)
        cmdout = subprocess.run(["sudo", "mkfs.ext4", block_dev+"1", "-v"], capture_output=True)
        out = cmdout.stdout.decode('utf-8').split('\n')
        print(out)
        #sizes = re.findall("\\d+", out[0])
        cmdout = subprocess.run(["sudo", "file", "-sL", block_dev+"1"], capture_output=True).stdout.split()
        type = out[4]
        uuid = out[7].split('=')[1]
        num_blocks = out[8].split()[0] #sizes[0]
        num_inodes = out[9].split()[0]
        block_size = out[0].split()[4]
        journal_size = out[7].split()[2].split('(')[1]
        return num_blocks, num_inodes, block_size, uuid, journal_size

def get_fs_details(src_dir):
        #details = subprocess.run(["sudo", "mount | grep", self.src_dir])
        details = subprocess.run(["sudo", "df", "-T", src_dir],  capture_output=True).stdout.decode('utf-8').split('\n')
        print(details)
        block_size = details[0].split()
        block_size = block_size[2].split('-')
        block_size = block_size[0]
        details = details[1].split()
        #fs_type = details.decode('utf-8').stdout.split(' ')[4]
        #block_dev = details.decode('utf-8').stdout.split(' ')[0]
        fs_type = details[1]
        block_dev = details[0]
        num_blocks = details[2]
        return block_dev, fs_type, block_size, num_blocks

def get_num_inodes(fs):
        info = subprocess.run(["sudo", "df", "-i", fs],  capture_output=True).stdout.decode('utf-8').split('\n')[1]
        return info[1]

def find_curr_ext4():
        print("start\n")
        devs = subprocess.run(["sudo", "lsblk"], capture_output=True)
        outputs = str(devs.stdout.decode('utf-8')).split('\n')
        #print("outputs:", outputs)
        #details = []
        for i in range(1, len(outputs)):
             details = outputs[i].split()
             print(details)
             print('\n')
             for j in range(0, len(details)):
                  print("name: ", details[j])
                  if ('/mnt' in details[j]): # file system mounted on root
                        print("yup")
                        return details[j]

def choose_ext4_fs():
        devs = subprocess.run(["sudo", "df", "-Th"], capture_output=True)
        outputs = devs.stdout.decode('utf-8').split('\n')
        details = [outputs[i].split('\t') for i in range(1, len(outputs))]
        fs = []
        for i in range(0, len(details), 6):
             if (details[i+1] == "ext4"):
                   fs.append(details[i])
             print(details[i])
        chosen_fs = input("Choose an EXT4 file system mounted on your disk from the above systems.")
        while chosen_fs not in fs:
             chosen_fs = input("Not a valid device. Choose an EXT4 file system mounted on your disk from the above systems.")
        return chosen_fs

def tune_fs():
        devs = subprocess.run(["sudo", "df", "-Th"], capture_output=True) # output should be in 
        outputs = devs.stdout.decode('utf-8').split('\n')
        details = [outputs[i].split('\t') for i in range(1, len(outputs))]
        fs = []
        file_sizes = []
        mount_locs = []
        for i in range(0, len(details), 6):
             if (details[i+1] == "ext3"):
                   fs.append(details[i])
             print(details[i])
        chosen_fs = input("Choose an EXT3 file system mounted on your disk to tune.")
        while chosen_fs not in fs:
             chosen_fs = input("Not a valid device. Choose an EXT3 file system mounted on your disk from the above systems.")
        #tune2fs_node_size_out = subprocess.run(["sudo", "tune2fs", "-l", "{chosen_fs} | grep 'Inode size'"])
        #inode_size = (re.findall(r'\d+', tune2fs_node_size_out))
        inode_size = self.get_block_size(chosen_fs)
        chosen_fs_loc = subprocess.run(["sudo", "mount | grep", chosen_fs],  capture_output=True).stdout.decode('utf-8').split()[2]
        tune_out = subprocess.run(["sudo" , "tune2fs", "-I", "{inode_size}", chosen_fs], check=True)
        subprocess.run(["sudo", "mount", "-t", "ext4", chosen_fs, chosen_fs_loc], capture_output=True)
        is_success = subprocess.run(["sudo", "mount"],  capture_output=True).stdout.decode('utf-8').split(' ')
        if (is_success[2] == chosen_fs_loc):
             return 1
        return 0

def get_block_size(fs):
        tune2fs_node_size_out = subprocess.run(["sudo", "tune2fs", "-l", fs+" | grep 'Inode size'"], capture_output=True)
        inode_size = (re.findall(r'\d+', tune2fs_node_size_out))
        return inode_size

def add_journaling():
        cmd = subprocess.run(["sudo", "df", "-t", "ext4"], capture_output=True)
        if (len(cmd.stdout.decode('utf-8')) == 0):
             print("Not applicable, not EXT4")

def create_volume_partitions(fs):
        """A helper function to create a set of volumes for the given file system or block device on your computer.  This 
        enables the creation of and usage of an EXT4 instance on your Linux system. There are at least 2 volumes that are created 
        from the original partition, one being the original file system, and the other being the EXT4 filesystem. Both can then be 
        placed into groups."""
        partition_cmd = subprocess.run(["sudo", "cfdisk"], capture_output=True)
        vols = {fs+"1", fs+"2"}
        outputs = []
        for vol in vols:
             pvcreate = subprocess.run(["sudo", "pvcreate", vol], capture_output=True)
             outputs.append(pvcreate.stdout.decode('utf-8')) #Example:  for "/dev/sdb1": Physical volume "/dev/sdb1" successfully created.
        return outputs

def create_vol_group(fs, group_name):
        """A helper function create a group for the partitions created from the file system that you have on your computer, which
        must be EXT4.
        How it works:  All volumes that are created the original file system that you entered in 'mount_to_LVM' are appended to a newly
        created array of volumes, where a volume group is then created to extend your EXT4 file system."""
        vols = []
        vols.append(fs+str(i) for i in range(2))
        group_vols = "".join(vols[i]+" " for i in range(len(vols)))
        group_create = subprocess.run(["sudo", "vgcreate", group_name, group_vols], capture_output=True) #Volume group "group_name" successfully extended
        return group_create.stdout.decode('utf-8')

