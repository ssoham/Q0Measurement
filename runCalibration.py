################################################################################
# A script that runs a heater calibration for a given Cryomodule object. This
# consists of five heater runs at different electric heat loads. The script
# should take ~4 hours to run.
# Author: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import division
from datetime import datetime
from sys import stdout
from cryomodule import (Cryomodule, MIN_DS_LL, MAX_DS_LL, DataSession,
                        MYSAMPLER_TIME_INTERVAL)
from epicsShell import cagetPV, caputPV
from time import sleep
from runQ0Measurement import checkCryo, waitForLL, waitForJT
from csv import writer


# The number of distinct heater settings we're using for cryomodule calibrations
NUM_CAL_RUNS = 5


# HEAT_LOAD_PARAMS = {2: {"low": 16, "high": 120}, 3: {"low": 64, "high": 120}}


def runCalibration(cryoModule, refValvePos=None):
    # type: (Cryomodule, float) -> (DataSession, float)

    def launchHeaterRun():
        print("Ramping heaters to the next setting...")
        walkHeaters(cryoModule, 1)
        runStartTime = datetime.now()
        stdout.write("\nWaiting either 40 minutes or for the LL to drop below"
                     " 90...")
        stdout.flush()
        while ((datetime.now() - runStartTime).total_seconds() < 2400
               and float(cagetPV(cryoModule.dsLevelPV)) > MIN_DS_LL):
            stdout.write(".")

        print("\nDone\n")

    if not refValvePos:
        refValvePos = checkCryo(cryoModule, 2)

    startTime = datetime.now()

    for _ in range(NUM_CAL_RUNS - 1):

        launchHeaterRun()

        print("Please ask the cryo group to refill to {LL} on the downstream "
              "sensor".format(LL=MAX_DS_LL))

        waitForLL(cryoModule)
        waitForJT(cryoModule, refValvePos)

    launchHeaterRun()

    endTime = datetime.now()

    walkHeaters(cryoModule, -NUM_CAL_RUNS)

    session = cryoModule.addDataSession(startTime, endTime,
                                        MYSAMPLER_TIME_INTERVAL, refValvePos)

    with open(cryoModule.idxFile, 'a') as f:
        csvWriter = writer(f)
        csvWriter.writerow([cryoModule.cryModNumJLAB, session.refHeatLoad,
                            refValvePos, startTime.strftime("%m/%d/%y %H:%M"),
                            endTime.strftime("%m/%d/%y %H:%M"),
                            MYSAMPLER_TIME_INTERVAL])

    return session, refValvePos


def walkHeaters(cryomodule, heaterDelta):
    # type: (Cryomodule, int) -> None

    step = 1 if heaterDelta > 0 else -1

    for _ in range(abs(heaterDelta)):
        for heaterSetpointPV in cryomodule.heaterDesPVs:
            currVal = float(cagetPV(heaterSetpointPV))
            caputPV(heaterSetpointPV, str(currVal + step))
            print("Waiting 30s for cryo to stabilize...")
            sleep(30)


if __name__ == "__main__":
    cryMod = Cryomodule(int(input("SLAC CM: ")), int(input("JLAB CM: ")))
    runCalibration(cryMod)
