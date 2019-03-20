"""user_input.py -- Contains the get_input function. Has debugging
functionality if called as __main__.
Author: Ben Ripman
Editor: Lisa Zacarias
"""

ERROR_MESSAGE = "Please provide valid input"


def get_input(prompt, desired_type):
    """Uses the supplied prompt to pester the user until they give
    input of the desired type.
    """

    response = raw_input(prompt)

    try:
        response = desired_type(response)
    except ValueError:
        print ERROR_MESSAGE
        return get_input(prompt, desired_type)

    return response


def get_str_limited(prompt, acceptable_strings=[]):

    response = get_input(prompt, str)
    while response not in acceptable_strings:
        print ERROR_MESSAGE
        response = get_input(prompt, str)

    return response


def get_str(prompt, constrained=False, acceptable_strings=[]):
    """Uses the supplied prompt to pester the user until they yield a
    string. If the constrained arg is True, only accepts input that is
    contained within the acceptable_strings list.
    """

    if constrained:
        return get_str_limited(prompt, acceptable_strings)
    else:
        return get_input(prompt, str)


def get_int_limited(prompt, low_lim, high_lim):
    response = get_input(prompt, int)

    while response < low_lim or response > high_lim:
        print ERROR_MESSAGE
        response = get_input(prompt, int)

    return response


def get_int(prompt, constrained=False, low_lim=0, high_lim=1):
    """Uses the supplied prompt to pester the user until they yield an
    integer. If the constrained arg is True, only accepts input that is
    >= low_lim and <= high_lim.
    """

    if constrained:
        return get_int_limited(prompt, low_lim, high_lim)
    else:
        return get_input(prompt, int)


def get_float_limited(prompt, low_lim, high_lim):
    response = get_input(prompt, float)

    while response < low_lim or response > high_lim:
        print ERROR_MESSAGE
        response = get_input(prompt, float)

    return response


def get_float(prompt, constrained=False, low_lim=0.0, high_lim=1.0):
    """Uses the supplied prompt to pester the user until they yield a
    float. If the constrained arg is True, only accepts input that is
    >= low_lim and <= high_lim.
    """

    if constrained:
        return get_float_limited(prompt, low_lim, high_lim)
    else:
        return get_input(prompt, float)


def main():
    """Tests various functions defined in this module."""
    print("\nHello world from user_input.py!\n")

    print("Let's try asking the user for some input.")
    get_str('Give me any string: ')
    get_str('Give me a decision (y or n): ', True, ['y', 'n'])
    get_int('Give me any integer: ')
    get_int('Give me a number between 1 and 10: ', True, 1, 10)
    get_float('Give me any float: ')
    get_float('Give me a float between 0 and 1: ', True, 0, 1)

if __name__ == '__main__':
    main()
