import logging
import sys
from tqdm import tqdm
import time

from module_base.parameter import Parameter

class RCModule:
	name: str = None
	params: dict[str, Parameter] = None
	logger: logging.Logger = None
	loading_bars: list[tqdm] = None
	
	def __init__(self, name, logger):
		"""
		Initializes the module.
		"""
		self.name = name
		self.logger = logger
		self.params = {}
		self.loading_bars = []

	def get_parameters(self) -> dict[str, Parameter]:
		"""
		Get the parameters needed to run the module.
		Returns:
			A dictionary containing the parameter id and the parameter object.
		"""
		return self.params

	def run(self) -> dict[str, any]:
		"""
		Runs the module.
		Returns:
			A dictionary containing the output data of the module.
		"""
		return None

	def finish(self):
		"""
		Finalizes the module. Cleans up any resources.
		"""
		for bar in self.loading_bars:
			bar.close()

		# Give the loading bars time to refresh
		time.sleep(0.2)

	def _initialize_loading_bar(self, total, description) -> tqdm:
		"""
		Initializes a loading bar.
		Args:
			total (int): The total number of steps.
			description (str): The description of the loading bar.
		Returns:
			The initialized loading bar.
		"""
		loading_bar = tqdm(total=total, unit='steps', desc=description, leave=True, miniters=1, file=sys.stdout)
		self.loading_bars.append(loading_bar)
		return loading_bar
	
	def _update_loading_bar(self, loading_bar, update):
		"""
		Updates a loading bar.
		Args:
			loading_bar (tqdm): The loading bar to update.
			update (int): The number of steps to update the loading bar by.
		"""
		max_value = loading_bar.total
		loading_bar.n = min(loading_bar.n + update, max_value)
		loading_bar.refresh()

	def _finish_loading_bar(self, loading_bar):
		"""
		Finishes a loading bar.
		Args:
			loading_bar (tqdm): The loading bar to finish.
		"""
		loading_bar.n = loading_bar.total
		loading_bar.refresh()

	def get_progress(self) -> float:
		"""
		Gets the progress of the module (Judged by average of loading bars).

		Returns:
			A float between 0 and 1 representing the progress of the module.
		"""
		if len(self.loading_bars) == 0:
			return 0
		
		total = 0

		for bar in self.loading_bars:
			total += bar.n / bar.total

		return total / len(self.loading_bars)

	def validate_parameters(self) -> (bool, str):
		"""
		Validates the parameters of the module.

		Returns:
			A tuple containing a boolean indicating whether the parameters are valid and a string containing an error message if the parameters are invalid.
		"""
		return True, None
	
	def get_name(self) -> str:
		return self.name
	
	def set_params(self, params) -> None:
		self.params = params