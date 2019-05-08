################################################################################
# A script that runs a heater calibration for a given Cryomodule object. This
# consists of five heater runs at different electric heat loads. The script
# should take ~4 hours to run.
# Author: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import division
from datetime import datetime
from sys import stdout
from cryomodule import Cryomodule, MIN_DS_LL, MAX_DS_LL
from epicsShell import cagetPV, caputPV
from time import sleep
from runQ0Measurement import checkCryo, waitForLL, waitForJT


# The number of distinct heater settings we're using for cryomodule calibrations
NUM_CAL_RUNS = 5

# CAVITY_MAX_HEAT_LOAD = 15
#
# CAVITY_MIN_HEAT_LOAD = 0
#
# HEAT_LOAD_PARAMS = {2: {"low": 16, "high": 120}, 3: {"low": 64, "high": 120}}


def runCalibration(cryoModule):
    # type: (Cryomodule) -> DataSession
    refValvePos = checkCryo(cryoModule, 2)

    for runNum in range(NUM_CAL_RUNS + 1):
        stdout.write("\nRamping heaters to the next setting...")
        stdout.flush()

        walkHeaters(cryoModule, 8 * runNum)

        startTime = datetime.now()

        while ((datetime.now() - startTime).total_seconds() < 2400
               and float(cagetPV(cryoModule.dsLevelPV)) > MIN_DS_LL):
            stdout.write(".")

        print("Please ask the cryo group to refill to {LL} on the downstream "
              "sensor".format(LL=MAX_DS_LL))

        waitForLL(cryoModule)
        waitForJT(cryoModule, refValvePos)

    walkHeaters(cryoModule, -8 * runNum)


def walkHeaters(cryomodule, elecHeatDelta):
    # type: (Cryomodule, int) -> None

    step = 1 if elecHeatDelta > 0 else -1

    elecHeatDelta = abs(elecHeatDelta)

    for _ in range(1, int(elecHeatDelta / 8) + 1):
        for heaterSetpointPV in cryomodule.heaterDesPVs:
            currVal = float(cagetPV(heaterSetpointPV))
            caputPV(heaterSetpointPV, str(currVal + step))
            print("Waiting 30s for cryo to stabilize...")
            sleep(30)


if __name__ == "__main__":
    pass
    # @pre Should be sorted in reverse order
    # numRuns = 5
    #
    # # noinspection PyCompatibility
    # cryoModule = Cryomodule(int(raw_input("SLAC CM: ")),
    #                         int(raw_input("JLAB CM: ")))
    #
    # currHeatLoad = 0
    #
    # for cavity in cryoModule.cavities:
    #     currHeatLoad += float(cagetPV(cavity.genHeaterPV("POWER_SETPT")))
    #
    # maxLoad = HEAT_LOAD_PARAMS[cryoModule.cryModNumJLAB]["high"]
    # headroom = maxLoad - currHeatLoad
    #
    # if headroom < 0:
    #     print("High heat load detected. (1) Walk down to 120 before calibrating"
    #           " or (2) calibrate here?")
    #
    #
    # if heatLoads[0] > headroom:
    #     delta = ceil(heatLoads[0] / 8)
    #     for i in range(1, 9):
    #         pass