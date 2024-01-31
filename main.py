import sys
import logging
import argparse
import inquirer

from module_base.parameter import Parameter

from module_base.rc_module import RCModule
from modules.extract_images.extract_images import ExtractImages
from modules.georeference.georeference_images import GeoreferenceImages
from modules.image_batcher.batch_directory import BatchDirectory
from modules.realitycapture_interface.realitycapture_interface import RealityCaptureAlignment

def intialize_logger() -> logging.Logger:
	logging.basicConfig(level=logging.INFO)
	logger = logging.getLogger(__name__)

	return logger

def initialize_modules(logger) -> dict[str, RCModule]:
	"""
	Initializes the modules and returns a dict of the active modules.
	"""

	# the available modules, add new modules here to register them
	available_modules: dict[str, RCModule] = {
		'Extract Images': ExtractImages(logger),
		'Georeference Images': GeoreferenceImages(logger),
		'Batch Directory': BatchDirectory(logger),
		'RealityCapture Alignment': RealityCaptureAlignment(logger)
	}
	
	# initialize a list of choices for the user to select from
	module_choices = [
		inquirer.Checkbox(
			'modules',
			message='Select modules to enable (arrow keys to move, space to select, enter to confirm)',
			choices=[module_name for module_name in available_modules.keys()],
			default=[module_name for module_name in available_modules.keys()],
			carousel=True
		)
	]

	# prompt checkbox to select modules
	answers = inquirer.prompt(module_choices)

	# enable modules based on user selection
	enabled_modules = {}
	for module_name, module in available_modules.items():
		if module_name in answers['modules']:
			enabled_modules[module_name] = module

	return enabled_modules

def initialize_parameters(modules) -> dict[str, Parameter]:
	"""
	Initializes the parameters and returns a dict of the active parameters.
	"""
	params: dict[str, Parameter] = {}

	# Global Parameters
	params['output_dir'] = Parameter(
		name='Output Directory',
		cli_short='o',
		cli_long='output_dir',
		type=str,
		default_value=None,
		description='Path to the output directory',
		prompt_user=True
	)

	params['continue_automatically'] = Parameter(
		name='Continue Automatically',
		cli_short='c',
		cli_long='continue_automatically',
		type=bool,
		default_value=False,
		description='Whether to continue automatically after each module',
		prompt_user=True
	)

	for module in modules.values():
		for param_name, param in module.get_parameters().items():
			if param.disable_when_module_active is not None:
				# If the disable_when_module_active is a list, check if any of the modules are active
				if isinstance(param.disable_when_module_active, list):
					for module_name in param.disable_when_module_active:
						if module_name in modules:
							continue
				# If the disable_when_module_active is a string, check if the module is active
				else:
					if param.disable_when_module_active in modules:
						continue

			params[param_name] = param

	return params

def parse_arguments(argv, params, logger) -> dict[str, dict[str, object]]:
	"""
	Parses the command line arguments and prompts the user for any missing values.
	"""
	parser = argparse.ArgumentParser()
	
	for param in params.values():
		parser.add_argument(f'-{param.cli_short}', f'--{param.cli_long}', type=param.get_type(), help=param.get_description())

	args = parser.parse_args()

	for param in params.values():
		# if it's not specified in the command line arguments and prompt_user is true, prompt the user for the value
		if getattr(args, param.cli_long) is None and param.prompt_user:
			# Try getting the value from the user
			try:
				input_value = input(f'{param.get_description()}: ')

				# Special handling for boolean types because all strings will cast to True
				if param.get_type() == bool:
					setattr(args, param.cli_long, input_value.lower() in ['true', 't', 'yes', 'y'])
				else:
					setattr(args, param.cli_long, param.get_type()(input_value))
			# Unable to cast the input value to the correct type, set to default value
			except ValueError:
				logger.warning(f'Invalid value for {param.get_name()}, using default value: {param.get_default_value()}')
				setattr(args, param.cli_long, param.get_default_value())

		# if it's not specified in the command line arguments and prompt_user is false, set the default value
		if getattr(args, param.cli_long) is None and not param.prompt_user:
			setattr(args, param.cli_long, param.get_default_value())

		# set the value in the params dict
		param.set_value(getattr(args, param.cli_long))

def update_parameters(params, modules) -> None:
	"""
	Updates the parameters of the modules with the values from the params dict.
	"""
	for module in modules.values():
		module.set_params(params)

def log_output_data(logger, output_data, num_spaces=0) -> None:
	"""
	Logs output data in the format:
		Data Name: Data Value

	Can also handle dictionary values.
	"""
	for data_name, data_value in output_data.items():
		spaces = '  ' * num_spaces

		if isinstance(data_value, dict):
			logger.info(f'{spaces}{data_name}:')
			log_output_data(logger, data_value, num_spaces + 1)
		else:
			logger.info(f'{spaces}{data_name}: {data_value}')

def main(argv):
	# Initialize logger and modules
	logger = intialize_logger()
	modules: dict[str, RCModule] = initialize_modules(logger)

	# Initialize parameters
	params = initialize_parameters(modules)
	parse_arguments(argv, params, logger)
	update_parameters(params, modules)

	# Print parameters
	logger.info("Parameters:")
	for param_name, param in params.items():
		logger.info(f'{param_name} ({param.cli_short}): {param.get_value()}')

	overall_output_data = {}

	# Run modules
	for index, module in enumerate(modules.values()):
		success, message = module.validate_parameters()

		if not success:
			logger.error(message)
			return
		
		logger.info(f'Running module: {module.get_name()}')
		module_output_data = module.run()
		module.finish()
		logger.info(f'Finished running module: {module.get_name()}')
		
		if module_output_data is not None and "Success" in module_output_data:
			logger.info(f'Success: {module_output_data["Success"]}')
		else:
			logger.info(f'Success: {success}')

		overall_output_data[module.get_name()] = module_output_data

		# if continue_automatically is false and it's not the last module, wait for user input
		if not params["continue_automatically"].get_value() and index != len(modules) - 1:
			input("Press enter to continue...")

	# Log output data
	logger.info("Output Data:")
	log_output_data(logger, overall_output_data)

if __name__ == '__main__':
	main(sys.argv)