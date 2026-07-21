"""Persistent user settings for the RealityScan pipeline scripts.

Every standalone script stores the user's last-entered values (data
locations, output folders, executable paths) in a single JSON file at the
repository root: ``rs_settings.json``. On the next run those values are
offered as defaults, so a plain <Enter> repeats the previous session's
answer.

The file is intentionally human-editable and is not committed to git.

Usage:

    from module_base.settings_store import SettingsStore

    settings = SettingsStore()
    input_dir = settings.prompt("geoall", "image_base_dir",
                                "Folder containing the images to georeference")
    settings.set("geoall", "image_base_dir", input_dir)  # prompt() already saves
"""

from __future__ import annotations

import json
import os
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DEFAULT_SETTINGS_PATH = os.path.join(_REPO_ROOT, "rs_settings.json")


class SettingsStore:
	def __init__(self, path: str = None):
		self.path = path or DEFAULT_SETTINGS_PATH
		self._data = self._load()

	def _load(self) -> dict:
		if not os.path.isfile(self.path):
			return {}
		try:
			with open(self.path, "r", encoding="utf-8") as f:
				data = json.load(f)
			return data if isinstance(data, dict) else {}
		except (json.JSONDecodeError, OSError):
			# A corrupt settings file must never block a run; start fresh
			# but keep the broken file aside for inspection.
			try:
				os.replace(self.path, self.path + ".corrupt")
			except OSError:
				pass
			return {}

	def _save(self) -> None:
		# Atomic write: never leave a half-written settings file behind if
		# the process dies mid-save.
		directory = os.path.dirname(self.path) or "."
		fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
		try:
			with os.fdopen(fd, "w", encoding="utf-8") as f:
				json.dump(self._data, f, indent=2, sort_keys=True)
			os.replace(tmp_path, self.path)
		except OSError:
			if os.path.exists(tmp_path):
				os.remove(tmp_path)
			raise

	def get(self, section: str, key: str, fallback=None):
		return self._data.get(section, {}).get(key, fallback)

	def set(self, section: str, key: str, value) -> None:
		self._data.setdefault(section, {})[key] = value
		self._save()

	def prompt(self, section: str, key: str, message: str, fallback=None):
		"""Ask the user for a value, offering the stored value (or
		``fallback``) as the default. The answer is persisted immediately."""
		default = self.get(section, key, fallback)

		if default is not None:
			answer = input(f"{message} [{default}]: ").strip()
			value = answer or default
		else:
			value = input(f"{message}: ").strip()
			while not value:
				value = input(f"{message} (required): ").strip()

		self.set(section, key, value)
		return value

	def prompt_bool(self, section: str, key: str, message: str, fallback: bool = None):
		default = self.get(section, key, fallback)
		suffix = " [y/n]" if default is None else (" [Y/n]" if default else " [y/N]")

		while True:
			answer = input(f"{message}{suffix}: ").strip().lower()
			if not answer and default is not None:
				value = bool(default)
				break
			if answer in ("y", "yes"):
				value = True
				break
			if answer in ("n", "no"):
				value = False
				break

		self.set(section, key, value)
		return value
