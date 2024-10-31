import abc

class BackendBase(abc.ABC):
    """Abstract base class for execution backend classes such as RsyncBackend and BtrfsBackend."""

    @abc.abstractmethod
    def execute_backup(self, src_dir, dst_dir):
        """Execute a single backup of src_dir to dst_dir."""
        ...

    @abc.abstractmethod
    def cleanup_backup(self, src_dir, dst_dir):
        """Cleanup system after executing a backup with execute_backup(src_dir, dst_dir)."""
        ...

    @abc.abstractmethod
    def bootstrap_backup(self, src_dir, dst_dir):
        """Bootstrap the system to support backups of src_dir to be placed in dst_dir."""
        ...

    @abc.abstractmethod
    def backup_is_bootstrapped(self, src_dir, dst_dir):
        """Return True if the system is ready for a backup of src_dir to dst_dir, and return False otherwise."""
        ...

    @abc.abstractmethod
    def backup_is_viable(self, src_dir, dst_dir):
        """Return True if a backup from src_dir to dst_dir is viable, and return False otherwise."""
        ...
