"""Unified execution layer for the RealityScan 2.2 CLI.

Every script in this repository that drives RealityScan must go through this
module so that launching, monitoring, error detection, and race-condition
handling behave identically everywhere.

How execution works
-------------------
The batch scripts in ``RS_CLI/Scripts`` boot one persistent *headless*
RealityScan instance (named ``RS1`` by default) and delegate each operation
to it with ``-delegateTo``. Delegated commands are *queued* — the delegating
process returns as soon as the command is handed over, NOT when the
operation finishes. Synchronisation therefore uses three cooperating
mechanisms, in line with RealityScan's own CLI facilities:

1. ``-waitCompleted <instance>`` after every delegated command (issued twice
   with a short grace period in between, because ``-waitCompleted`` can
   return prematurely when it runs before the instance has picked the
   queued command up — a race we have hit in production).
2. RealityScan's built-in process trigger: the instance is started with
   ``appProcessAction=ExecuteProgram`` and ``appProcessExecCmd`` pointing at
   ``RS_CLI/Errors/ErrorWriter.bat``. RealityScan itself invokes that hook
   whenever a process finishes and passes ``$(processResult)``. Every
   completion is appended to ``results.log``; failures are appended to
   ``errors.txt``. This is the source of truth for per-operation success —
   the batch scripts abort as soon as ``errors.txt`` becomes non-empty.
3. ``-writeProgress progress.txt`` on the instance, which this module tails
   to report activity and to warn about stalls. There is deliberately NO
   overall timeout: alignment/reconstruction on large datasets legitimately
   runs for many hours.

Race-condition rules enforced here:
- A per-instance lock file prevents two orchestrators from driving the same
  instance name concurrently.
- Marker files (``progress.txt``, ``errors.txt``, ``results.log``) are
  cleared before every run so a previous run's state can never be misread
  as the current run's.
- After a workflow finishes, we verify via ``-getStatus`` that the instance
  actually shut down before the next workflow starts, so consecutive runs
  can never share (and contaminate) a scene.
- Completion is never inferred from process *names* (the pre-2.x code
  polled ``tasklist`` for ``RealityCapture.exe``, which silently matched
  nothing once the executable became ``RealityScan.exe``).

Multi-GPU
---------
RealityScan uses every CUDA GPU by default. To pin an instance to specific
GPUs (e.g. to run one instance per GPU), set ``gpu_devices`` in
``rs_settings.json`` under the ``realityscan`` section (e.g. ``"0,1"``), or
pass ``gpu_devices`` to :meth:`RealityScanCLI.run_batch_script`. The value
is exported as ``CUDA_VISIBLE_DEVICES``/``RS_GPU_DEVICES`` for the launched
instance. Give each concurrent instance a unique ``instance_name``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

try:
	from module_base.settings_store import SettingsStore
except ImportError:
	sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
	from module_base.settings_store import SettingsStore

_THIS_DIR = os.path.dirname(os.path.realpath(__file__))
SCRIPTS_DIR = os.path.join(_THIS_DIR, 'RS_CLI', 'Scripts')
METADATA_DIR = os.path.join(_THIS_DIR, 'RS_CLI', 'Metadata')
ERRORS_DIR = os.path.join(_THIS_DIR, 'RS_CLI', 'Errors')

DEFAULT_INSTANCE_NAME = 'RS1'

# Newest install locations first; extend when Epic ships a new version.
EXECUTABLE_CANDIDATES = [
	r'C:\Program Files\Epic Games\RealityScan_2.2\RealityScan.exe',
	r'C:\Program Files\Capturing Reality\RealityScan 2.2\RealityScan.exe',
	r'C:\Program Files\Epic Games\RealityScan_2.1\RealityScan.exe',
	r'C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe',
	r'C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe',
]

# How long progress may stay silent before we log a stall warning. This is
# a warning only — large datasets can legitimately be quiet for a long time.
STALL_WARNING_SECONDS = 2 * 60 * 60
PROGRESS_POLL_SECONDS = 2.0
SHUTDOWN_VERIFY_TIMEOUT_SECONDS = 300


@dataclass
class WorkflowResult:
	success: bool
	return_code: int
	log_path: str = None
	errors: str = ''
	completed_processes: list[str] = field(default_factory=list)
	duration_seconds: float = 0.0


class RealityScanCLI:
	"""Shared launcher/monitor for every RealityScan CLI workflow."""

	def __init__(self, logger, settings: SettingsStore = None, instance_name: str = None):
		self.logger = logger
		self.settings = settings or SettingsStore()
		self.instance_name = (
			instance_name
			or self.settings.get('realityscan', 'instance_name')
			or DEFAULT_INSTANCE_NAME
		)

	# ------------------------------------------------------------------
	# Executable discovery
	# ------------------------------------------------------------------

	def find_executable(self) -> str:
		"""Resolve RealityScan.exe: settings file, then RS_EXECUTABLE env
		var, then standard install locations (newest first)."""
		candidates = []

		configured = self.settings.get('realityscan', 'executable')
		if configured:
			candidates.append(configured)

		env_exe = os.environ.get('RS_EXECUTABLE')
		if env_exe:
			candidates.append(env_exe)

		candidates.extend(EXECUTABLE_CANDIDATES)

		for candidate in candidates:
			if candidate and os.path.isfile(candidate):
				return candidate

		raise FileNotFoundError(
			'RealityScan.exe not found. Set "realityscan.executable" in '
			'rs_settings.json or the RS_EXECUTABLE environment variable. '
			f'Tried: {candidates}'
		)

	# ------------------------------------------------------------------
	# Instance status (via RealityScan's own -getStatus)
	# ------------------------------------------------------------------

	def is_instance_running(self) -> bool:
		exe = self.find_executable()
		result = subprocess.run(
			[exe, '-getStatus', self.instance_name],
			stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
		)
		return result.returncode == 0

	def wait_for_instance_shutdown(self, timeout: float = SHUTDOWN_VERIFY_TIMEOUT_SECONDS) -> bool:
		"""Block until the instance is gone. Returns False on timeout —
		callers must treat that as 'do not start the next workflow'."""
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			if not self.is_instance_running():
				return True
			time.sleep(PROGRESS_POLL_SECONDS)
		return False

	# ------------------------------------------------------------------
	# Locking (one orchestrator per instance name)
	# ------------------------------------------------------------------

	def _lock_path(self) -> str:
		return os.path.join(ERRORS_DIR, f'{self.instance_name}.lock')

	@staticmethod
	def _pid_alive(pid: int) -> bool:
		if os.name == 'nt':
			result = subprocess.run(
				['tasklist', '/FI', f'PID eq {pid}', '/NH'],
				stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
			)
			return str(pid) in result.stdout
		try:
			os.kill(pid, 0)
			return True
		except OSError:
			return False

	def _acquire_lock(self) -> None:
		os.makedirs(ERRORS_DIR, exist_ok=True)
		lock_path = self._lock_path()

		if os.path.isfile(lock_path):
			try:
				with open(lock_path, 'r', encoding='utf-8') as f:
					holder_pid = int(f.read().strip() or 0)
			except (ValueError, OSError):
				holder_pid = 0

			if holder_pid and self._pid_alive(holder_pid):
				raise RuntimeError(
					f'RealityScan instance "{self.instance_name}" is already '
					f'being driven by PID {holder_pid} (lock: {lock_path}). '
					'Use a different instance_name to run workflows in '
					'parallel, or wait for the other run to finish.'
				)
			self.logger.warning('Removing stale RealityScan lock %s (PID %s is gone)', lock_path, holder_pid)
			os.remove(lock_path)

		# O_EXCL closes the window between the check above and creation.
		fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
		with os.fdopen(fd, 'w', encoding='utf-8') as f:
			f.write(str(os.getpid()))

	def _release_lock(self) -> None:
		try:
			os.remove(self._lock_path())
		except OSError:
			pass

	# ------------------------------------------------------------------
	# Marker files written by the instance / ErrorWriter hook
	# ------------------------------------------------------------------

	def _marker(self, name: str) -> str:
		return os.path.join(ERRORS_DIR, name)

	def _clear_markers(self) -> None:
		for name in ('progress.txt', 'errors.txt', 'results.log'):
			path = self._marker(name)
			if os.path.isfile(path):
				os.remove(path)

	def _read_marker(self, name: str) -> str:
		path = self._marker(name)
		if not os.path.isfile(path):
			return ''
		try:
			with open(path, 'r', encoding='utf-8', errors='replace') as f:
				return f.read().strip()
		except OSError:
			return ''

	# ------------------------------------------------------------------
	# Workflow execution
	# ------------------------------------------------------------------

	def run_batch_script(self, script_name: str, args: list[str], log_dir: str,
						 display_output: bool = False, gpu_devices: str = None) -> WorkflowResult:
		"""Run one RS_CLI workflow script and block until the RealityScan
		instance has finished and shut down.

		The batch script is responsible for per-command synchronisation
		(delegate → waitCompleted×2 → check errors.txt); this method is
		responsible for orchestration-level concerns: locking, marker
		hygiene, GPU pinning, live progress reporting, stall warnings, and
		verified instance shutdown.
		"""
		exe = self.find_executable()
		script_path = os.path.join(SCRIPTS_DIR, script_name)
		if not os.path.isfile(script_path):
			raise FileNotFoundError(f'Workflow script not found: {script_path}')

		os.makedirs(log_dir, exist_ok=True)
		log_path = os.path.join(log_dir, f'output_{time.strftime("%Y-%m-%d_%H-%M-%S")}.txt')

		env = os.environ.copy()
		env['RS_EXECUTABLE'] = exe
		env['RS_INSTANCE'] = self.instance_name
		gpu_devices = gpu_devices if gpu_devices is not None else self.settings.get('realityscan', 'gpu_devices')
		if gpu_devices:
			env['RS_GPU_DEVICES'] = str(gpu_devices)
			env['CUDA_VISIBLE_DEVICES'] = str(gpu_devices)

		self._acquire_lock()
		start_time = time.monotonic()
		try:
			if self.is_instance_running():
				self.logger.warning(
					'RealityScan instance "%s" is already running; the workflow '
					'will attach to it and start a fresh scene.', self.instance_name)

			self._clear_markers()

			creationflags = 0
			if os.name == 'nt':
				creationflags = (subprocess.CREATE_NEW_CONSOLE if display_output
								 else subprocess.CREATE_NO_WINDOW)

			with open(log_path, 'w', encoding='utf-8', errors='replace') as log_file:
				process = subprocess.Popen(
					['cmd', '/c', script_name] + list(args),
					cwd=SCRIPTS_DIR, env=env,
					stdout=log_file, stderr=subprocess.STDOUT,
					creationflags=creationflags,
				)
				self._monitor_until_exit(process)

			return_code = process.returncode
			errors = self._read_marker('errors.txt')
			results = [line for line in self._read_marker('results.log').splitlines() if line.strip()]

			# The workflow ends by delegating -quit; make sure the instance is
			# really gone before anyone starts the next workflow.
			if not self.wait_for_instance_shutdown():
				self.logger.error(
					'RealityScan instance "%s" did not shut down within %s s; '
					'refusing to continue while it may still hold the scene.',
					self.instance_name, SHUTDOWN_VERIFY_TIMEOUT_SECONDS)
				return WorkflowResult(False, return_code, log_path, errors or 'instance did not shut down', results,
									  time.monotonic() - start_time)

			success = return_code == 0 and not errors
			if not success:
				self.logger.error(
					'RealityScan workflow %s failed (exit code %s). Errors: %s. Log: %s',
					script_name, return_code, errors or '<none reported>', log_path)

			return WorkflowResult(success, return_code, log_path, errors, results,
								  time.monotonic() - start_time)
		finally:
			self._release_lock()

	def _monitor_until_exit(self, process: subprocess.Popen) -> None:
		"""Poll the workflow process, relaying progress.txt updates and
		warning on stalls. No overall timeout by design."""
		progress_path = self._marker('progress.txt')
		last_progress_line = ''
		last_activity = time.monotonic()
		stall_warned = False

		while process.poll() is None:
			time.sleep(PROGRESS_POLL_SECONDS)

			line = self._tail_line(progress_path)
			if line and line != last_progress_line:
				last_progress_line = line
				last_activity = time.monotonic()
				stall_warned = False
				self.logger.info('RealityScan [%s]: %s', self.instance_name, line)

			errors = self._read_marker('errors.txt')
			if errors:
				# The batch script aborts itself on errors.txt; we just make
				# the failure visible immediately instead of at the end.
				self.logger.error('RealityScan [%s] reported an error: %s', self.instance_name, errors)
				last_activity = time.monotonic()

			if not stall_warned and time.monotonic() - last_activity > STALL_WARNING_SECONDS:
				stall_warned = True
				self.logger.warning(
					'RealityScan [%s] has reported no progress for over %.1f hours. '
					'Long silences are normal for very large datasets; check the '
					'instance manually if this persists.',
					self.instance_name, STALL_WARNING_SECONDS / 3600)

	@staticmethod
	def _tail_line(path: str) -> str:
		if not os.path.isfile(path):
			return ''
		try:
			with open(path, 'rb') as f:
				f.seek(0, os.SEEK_END)
				size = f.tell()
				f.seek(max(0, size - 4096))
				chunk = f.read().decode('utf-8', errors='replace')
			lines = [l.strip() for l in chunk.splitlines() if l.strip()]
			return lines[-1] if lines else ''
		except OSError:
			return ''
