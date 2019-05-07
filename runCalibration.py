from __future__ import division

from datetime import datetime
from sys import stdout

from calculateQ0 import getStrLim
from cryomodule import Cryomodule, Cavity
from epicsShell import cagetPV, caputPV
from time import sleep
from math import ceil
from runQ0Measurement import checkCryo, waitForLL, waitForJT

TARGET_LL = 95


CAVITY_MAX_HEAT_LOAD = 15


CAVITY_MIN_HEAT_LOAD = 0


HEAT_LOAD_PARAMS = {2: {"low": 16, "high": 120}, 3: {"low": 64, "high": 120}}


def walkHeaters(cryomodule, elecHeatDelta):
    # type: (Cryomodule, int) -> None

    for _ in range(1, int(elecHeatDelta / 8) + 1):
        for heaterSetpointPV in cryomodule.heaterDesPVs:
            currVal = float(cagetPV(heaterSetpointPV))
            caputPV(heaterSetpointPV, str(currVal + 1))
            print("Waiting 30s for cryo to stabilize...")
            sleep(30)


def runCalibration(cryoModule):
    # type: (Cryomodule) -> DataSession
    refValvePos = checkCryo(cryoModule, 2)

    for _ in range(5):
        walkHeaters(cryoModule, 8)

        stdout.write("\nDoing things...")
        stdout.flush()

        startTime = datetime.now()

        while ((datetime.now() - startTime).total_seconds() < 2400
               and float(cagetPV(cryoModule.dsLevelPV)) > 90):
            stdout.write(".")

        print("Please ask the cryo group to refill to {LL} on the downstream "
              "sensor".format(LL=TARGET_LL))

        waitForLL(cryoModule)
        waitForJT(cryoModule, refValvePos)


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