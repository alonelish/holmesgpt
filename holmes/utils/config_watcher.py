import logging
import os
import threading
from typing import Callable, Optional

import yaml

from holmes.common.env_vars import ROBUSTA_CONFIG_PATH
from holmes.config import RobustaConfig


class ConfigWatcher:
    """
    Watches the Robusta config file for changes and triggers a callback when clusterName changes.

    This is needed because Kubernetes updates mounted ConfigMaps/Secrets automatically
    (within ~60-90 seconds), but the application needs to detect these changes and re-sync.
    """

    def __init__(
        self,
        on_cluster_name_change: Callable[[str, str], None],
        check_interval_seconds: int = 30,
    ):
        """
        Args:
            on_cluster_name_change: Callback function called when cluster name changes.
                                   Receives (old_cluster_name, new_cluster_name) as arguments.
            check_interval_seconds: How often to check for config changes (default: 30s).
        """
        self._on_cluster_name_change = on_cluster_name_change
        self._check_interval = check_interval_seconds
        self._current_cluster_name: Optional[str] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _read_cluster_name(self) -> Optional[str]:
        """Read cluster_name from the Robusta config file."""
        config_file_path = ROBUSTA_CONFIG_PATH

        if not os.path.exists(config_file_path):
            return None

        try:
            with open(config_file_path) as file:
                yaml_content = yaml.safe_load(file)
                if yaml_content is None:
                    return None
                config = RobustaConfig(**yaml_content)
                return config.global_config.get("cluster_name")
        except Exception as e:
            logging.warning(f"Failed to read cluster name from config: {e}")
            return None

    def _watch_loop(self) -> None:
        """Background loop that checks for config changes."""
        while not self._stop_event.is_set():
            try:
                new_cluster_name = self._read_cluster_name()

                if (
                    new_cluster_name is not None
                    and self._current_cluster_name is not None
                    and new_cluster_name != self._current_cluster_name
                ):
                    logging.info(
                        f"Detected cluster name change: '{self._current_cluster_name}' -> '{new_cluster_name}'"
                    )
                    old_name = self._current_cluster_name
                    self._current_cluster_name = new_cluster_name

                    try:
                        self._on_cluster_name_change(old_name, new_cluster_name)
                    except Exception as e:
                        logging.error(f"Error in cluster name change callback: {e}", exc_info=True)
                elif new_cluster_name is not None and self._current_cluster_name is None:
                    # Initial read or recovery from missing config
                    self._current_cluster_name = new_cluster_name

            except Exception as e:
                logging.error(f"Error in config watcher loop: {e}", exc_info=True)

            self._stop_event.wait(self._check_interval)

    def start(self, initial_cluster_name: Optional[str] = None) -> None:
        """Start the config watcher background thread."""
        if self._thread is not None and self._thread.is_alive():
            logging.warning("Config watcher is already running")
            return

        self._current_cluster_name = initial_cluster_name or self._read_cluster_name()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        logging.info(
            f"Config watcher started (interval: {self._check_interval}s, "
            f"initial cluster: {self._current_cluster_name})"
        )

    def stop(self) -> None:
        """Stop the config watcher background thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logging.info("Config watcher stopped")
