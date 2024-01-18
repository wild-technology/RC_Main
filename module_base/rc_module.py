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
		self.name = name
		self.logger = logger
		self.params = {}
		self.loading_bars = []

	def get_parameters(self) -> dict[str, Parameter]:
		return self.params

	def run(self):
		pass

	def finish(self):
		for bar in self.loading_bars:
			bar.refresh()
			bar.close()

		# Give the loading bars time to refresh
		time.sleep(0.1)

	def _initialize_loading_bar(self, total, description):
		loading_bar = tqdm(total=total, unit='steps', desc=description, leave=True, miniters=1, file=sys.stdout)
		self.loading_bars.append(loading_bar)
		return loading_bar
	
	def _update_loading_bar(self, loading_bar, update):
		max_value = loading_bar.total
		loading_bar.n = min(loading_bar.n + update, max_value)
		loading_bar.refresh()

	def get_progress(self):
		if len(self.loading_bars) == 0:
			return 0
		
		total = 0

		for bar in self.loading_bars:
			total += bar.n / bar.total

		return total / len(self.loading_bars)

	def validate_parameters(self) -> (bool, str):
		return True, None
	
	def get_name(self) -> str:
		return self.name
	
	def set_params(self, params) -> None:
		self.params = params