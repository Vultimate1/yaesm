from abc import ABC

class BackupBase(ABC):
    """Abstract base class for the Snapshot, LocalBackup, and SSHBackup classes."""

    @abstractmethod
    def bootstrap(self):
        """Perform the bootstrap process for the backup environment (for this backup)."""
        ...

    @abstractmethod
    def execute(self):
        """Execute a single backup (for this backup)."""
        ...

    @abstractmethod
    def cleanup(self):
        """Cleanup after executing a backup (for this backup)."""
        ...

    @abstractmethod
    def all_existing(self):
        """Return list of all exisiting backups (for this backup) on the filesystem."""
        ...
