# zash/utils/backup.py

import json
import shutil
import tarfile
import tempfile
import threading
from pathlib import Path
from typing import List, Optional

from gi.repository import Gio

from .exceptions import StorageReadError, StorageWriteError
from .logger import get_logger
from .platform import get_config_directory


class BackupManager:
    """Manages backup and recovery operations (tar.gz archives, no encryption)."""

    def __init__(self, backup_dir: Optional[Path] = None):
        self.logger = get_logger("zash.backup")
        if backup_dir is None:
            backup_dir = get_config_directory() / "backups"
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.logger.info(
            f"Backup manager initialized with directory: {self.backup_dir}"
        )

    def create_backup(
        self,
        target_file_path: str,
        sessions_store: Gio.ListStore,
        source_files: List[Path],
        layouts_dir: Path,
    ) -> None:
        """
        Creates a single .tar.gz backup file (no encryption).

        Args:
            target_file_path: The full path where the backup file will be saved.
            sessions_store: The session store to export passwords from.
            source_files: List of primary config files to include (e.g., sessions.json).
            layouts_dir: The directory containing layout files to be backed up.

        Raises:
            StorageWriteError: If the backup process fails.
        """
        with self._lock:
            with tempfile.TemporaryDirectory(prefix="zash_backup_") as tmpdir:
                temp_path = Path(tmpdir)
                self.logger.debug(f"Using temporary directory for backup: {temp_path}")

                try:
                    # 1. Copy primary source files
                    for src_file in source_files:
                        if src_file.exists():
                            shutil.copy(src_file, temp_path / src_file.name)

                    # 2. Copy layouts directory
                    if layouts_dir.exists() and layouts_dir.is_dir():
                        shutil.copytree(
                            layouts_dir, temp_path / "layouts", dirs_exist_ok=True
                        )

                    # 3. Export and save passwords (lazy import crypto)
                    from .crypto import export_all_passwords
                    passwords = export_all_passwords(sessions_store)
                    if passwords:
                        with open(temp_path / "passwords.json", "w") as f:
                            json.dump(passwords, f, indent=2)

                    # 4. Create the tar.gz archive
                    self.logger.info(f"Creating backup at {target_file_path}")
                    with tarfile.open(target_file_path, "w:gz") as archive:
                        for item in temp_path.rglob("*"):
                            archive.add(item, arcname=item.relative_to(temp_path))

                    self.logger.info("Backup created successfully.")

                except Exception as e:
                    self.logger.error(f"Failed to create backup: {e}")
                    raise StorageWriteError(target_file_path, str(e)) from e

    def restore_backup(self, source_file_path: str, config_dir: Path) -> None:
        """
        Restores configuration from a .tar.gz backup file.

        Args:
            source_file_path: The path to the .tar.gz backup file.
            config_dir: The root configuration directory to restore files to.

        Raises:
            StorageReadError: If the restore process fails.
        """
        with self._lock:
            with tempfile.TemporaryDirectory(prefix="zash_restore_") as tmpdir:
                temp_path = Path(tmpdir)
                self.logger.debug(f"Using temporary directory for restore: {temp_path}")

                try:
                    # 1. Extract the archive
                    self.logger.info(f"Extracting backup from {source_file_path}")
                    with tarfile.open(source_file_path, "r:gz") as archive:
                        archive.extractall(path=temp_path)

                    # 2. Restore files
                    for item in temp_path.iterdir():
                        target_path = config_dir / item.name
                        if item.is_dir():
                            shutil.rmtree(target_path, ignore_errors=True)
                            shutil.copytree(item, target_path, dirs_exist_ok=True)
                        elif item.is_file() and item.name != "passwords.json":
                            shutil.copy(item, target_path)

                    # 3. Import passwords
                    passwords_file = temp_path / "passwords.json"
                    if passwords_file.exists():
                        with open(passwords_file, "r") as f:
                            passwords = json.load(f)

                        from .crypto import store_password
                        imported_count = 0
                        for session_name, pwd in passwords.items():
                            try:
                                store_password(session_name, pwd)
                                imported_count += 1
                            except Exception as e:
                                self.logger.error(
                                    f"Failed to import password for '{session_name}': {e}"
                                )
                        self.logger.info(f"Imported {imported_count} passwords.")

                    self.logger.info(
                        "Restore from backup completed successfully."
                    )

                except tarfile.TarError as e:
                    self.logger.error(f"Invalid tar.gz backup: {e}")
                    raise StorageReadError(
                        source_file_path, "Invalid backup archive."
                    ) from e
                except Exception as e:
                    self.logger.error(f"Failed to restore from backup: {e}")
                    raise StorageReadError(source_file_path, str(e)) from e

    # Backward-compatible wrappers (password is ignored)
    def create_encrypted_backup(
        self,
        target_file_path: str,
        password: str,
        sessions_store: Gio.ListStore,
        source_files: List[Path],
        layouts_dir: Path,
    ) -> None:
        self.logger.warning(
            "Encrypted backups are no longer supported; creating unencrypted tar.gz."
        )
        self.create_backup(target_file_path, sessions_store, source_files, layouts_dir)

    def restore_from_encrypted_backup(
        self, source_file_path: str, password: str, config_dir: Path
    ) -> None:
        self.logger.warning(
            "Encrypted backups are no longer supported; restoring from tar.gz."
        )
        self.restore_backup(source_file_path, config_dir)


_backup_manager: Optional[BackupManager] = None
_backup_manager_lock = threading.Lock()


def get_backup_manager() -> BackupManager:
    """Get the global backup manager instance."""
    global _backup_manager
    if _backup_manager is None:
        with _backup_manager_lock:
            if _backup_manager is None:
                _backup_manager = BackupManager()
    return _backup_manager
