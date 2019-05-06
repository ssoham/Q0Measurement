from subprocess import check_output, CalledProcessError, check_call
from time import sleep
from os import devnull


FNULL = open(devnull, "w")


# PyEpics doesn't work at LERF yet...
def cagetPV(pv, startIdx=1, attempt=1):
    # type: (str, int, int) -> [str]

    if attempt < 4:
        try:
            out = check_output(["caget", pv, "-n"]).split()[startIdx:]
            if startIdx == 1:
                return out.pop()
            elif startIdx >= 2:
                return out
        except CalledProcessError as _:
            sleep(2)
            print("Retrying caget")
            return cagetPV(pv, startIdx, attempt + 1)

    else:
        raise CalledProcessError("caget failed too many times")


def caputPV(pv, val, attempt=1):
    # type: (str, str, int) -> int

    if attempt < 4:
        try:
            out = check_call(["caput", pv, val], stdout=FNULL)
            sleep(2)
            return out
        except CalledProcessError:
            sleep(2)
            print("Retrying caput")
            return caputPV(pv, val, attempt + 1)
    else:
        raise CalledProcessError("caput failed too many times")