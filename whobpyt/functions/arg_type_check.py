import inspect

def method_arg_type_check(method_obj):
    """
    Takes the method object of a given function (e.g. RNNJANSEN) and checks that the passed arguments abide by their 
    expected data types.  If there is a discrepency, raises a ValueError.
    """

    args_signature = inspect.signature(method_obj)
    expected_types = {param.name: param.annotation for param in args_signature.parameters.values()} # Extract expected parameter types from the signature

    for arg_name, arg_type in expected_types.items(): # Iterate through each passed arguments' label and data type
        if arg_name != 'self' and arg_name in locals(): # Skip 'self' argument and check if argument is present
            if not isinstance(locals()[arg_name], arg_type): # Check if the passed arg's data type does not match its expected type
                passed_arg_type = type(locals()[arg_name]).__name__ # Passed data type
                expected_arg_type = arg_type.__name__ # Expected data type
                raise ValueError(f"{arg_name} should be of type {expected_arg_type}, but got type {passed_arg_type} instead.") # Halt if discrepancy