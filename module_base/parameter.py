class Parameter:
	name: str = None
	cli_short: str = None
	cli_long: str = None
	type: type = None
	value: object = None
	default_value: object = None
	description: str = None
	prompt_user: bool = None
	disable_when_module_active: str = None
	parameter_group: str = None
	min_value: float = None
	max_value: float = None
	choices: list = None
	file_filter: str = None

	def __init__(self, name, cli_short, cli_long, type, default_value, description=None,
				 prompt_user=True, disable_when_module_active=None, parameter_group=None,
				 min_value=None, max_value=None, choices=None, file_filter=None):
		self.name = name
		self.cli_short = cli_short
		self.cli_long = cli_long
		self.value = default_value
		self.default_value = default_value
		self.description = description
		self.type = type
		self.prompt_user = prompt_user
		self.disable_when_module_active = disable_when_module_active
		self.parameter_group = parameter_group or "General"
		self.min_value = min_value
		self.max_value = max_value
		self.choices = choices
		self.file_filter = file_filter
	
	def get_name(self) -> str:
		return self.name
	
	def get_type(self) -> type:
		return self.type

	def get_value(self) -> object:
		return self.value
	
	def set_value(self, value) -> None:
		self.value = value
	
	def get_default_value(self) -> object:
		return self.default_value
	
	def get_description(self) -> str:
		return self.description

	def get_parameter_group(self) -> str:
		return self.parameter_group

	def validate(self) -> tuple[bool, str | None]:
		"""Validate the current value against constraints.

		Returns (True, None) if valid, or (False, error_message).
		"""
		if self.value is None:
			return True, None

		if self.choices is not None and self.value not in self.choices:
			return False, f"Parameter '{self.name}': value '{self.value}' not in choices {self.choices}"

		if self.type in (int, float) and self.value is not None:
			try:
				numeric = self.type(self.value)
			except (ValueError, TypeError):
				return False, f"Parameter '{self.name}': cannot convert '{self.value}' to {self.type.__name__}"
			if self.min_value is not None and numeric < self.min_value:
				return False, f"Parameter '{self.name}': value {numeric} below minimum {self.min_value}"
			if self.max_value is not None and numeric > self.max_value:
				return False, f"Parameter '{self.name}': value {numeric} above maximum {self.max_value}"

		return True, None

	def to_dict(self) -> dict:
		"""Serialize parameter to a dict for session state."""
		return {
			"name": self.name,
			"value": self.value,
			"default_value": self.default_value,
			"type": self.type.__name__ if self.type else None,
			"parameter_group": self.parameter_group,
		}