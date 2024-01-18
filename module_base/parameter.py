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

	def __init__(self, name, cli_short, cli_long, type, default_value, description=None, prompt_user=True, disable_when_module_active=None):
		self.name = name
		self.cli_short = cli_short
		self.cli_long = cli_long
		self.value = default_value
		self.default_value = default_value
		self.description = description
		self.type = type
		self.prompt_user = prompt_user
		self.disable_when_module_active = disable_when_module_active
	
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