################################################################################
# A script that runs a heater calibration for a given Cryomodule object. This
# consists of five heater runs at different electric heat loads. The script
# should take ~4 hours to run.
# Author: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import division
from datetime import datetime
from container import Cryomodule, CalibDataSession
from csv import writer
from utils import MIN_DS_LL, MAX_DS_LL, MYSAMPLER_TIME_INTERVAL, cagetPV


# The number of distinct heater settings we're using for cryomodule calibrations
from utils import writeAndWait

NUM_CAL_RUNS = 5

# Information about the acceptable heat load ranges for the LERF cryomodueles
# (not currently used)
# HEAT_LOAD_PARAMS = {2: {"low": 16, "high": 120}, 3: {"low": 64, "high": 120}}


def runCalibration(cryoModule, refValvePos=None):
    # type: (Cryomodule, float) -> (CalibDataSession, float)

    def launchHeaterRun():
        print("Ramping heaters to the next setting...")

        cryoModule.walkHeaters(1)
        runStartTime = datetime.now()

        writeAndWait("\nWaiting either 40 minutes or for the LL to drop below"
                     " {NUM}...".format(NUM=MIN_DS_LL))

        while ((datetime.now() - runStartTime).total_seconds() < 2400
               and float(cagetPV(cryoModule.dsLevelPV)) > MIN_DS_LL):
            writeAndWait(".", 10)

        print("\nDone\n")

    # Check whether or not we've already found a good JT position during this
    # program execution
    if not refValvePos:
        refValvePos = cryoModule.getRefValvePos(2)

    cryoModule.waitForCryo(refValvePos)

    startTime = datetime.now()

    for _ in range(NUM_CAL_RUNS - 1):

        launchHeaterRun()

        print("Please ask the cryo group to refill to {LL} on the downstream "
              "sensor".format(LL=MAX_DS_LL))

        cryoModule.waitForCryo(refValvePos)

    # Kinda jank way to avoid waiting for cryo conditions after the final run
    launchHeaterRun()

    endTime = datetime.now()

    print("\nEnd Time: {END}".format(END=datetime.now()))
    duration = (datetime.now() - startTime).total_seconds() / 3600
    print("Duration in hours: {DUR}".format(DUR=duration))

    # Walking the heaters back to their starting settings
    cryoModule.walkHeaters(-NUM_CAL_RUNS)

    dataSession = cryoModule.addDataSession(startTime, endTime,
                                            MYSAMPLER_TIME_INTERVAL,
                                            refValvePos)

    # Record this calibration dataSession's metadata
    with open(cryoModule.idxFile, 'a') as f:
        csvWriter = writer(f)
        csvWriter.writerow([cryoModule.cryModNumJLAB, dataSession.refHeatLoad,
                            refValvePos, startTime.strftime("%m/%d/%y %H:%M"),
                            endTime.strftime("%m/%d/%y %H:%M"),
                            MYSAMPLER_TIME_INTERVAL])

    return dataSession, refValvePos


if __name__ == "__main__":
    cryMod = Cryomodule(int(input("SLAC CM: ")), int(input("JLAB CM: ")))
    session, _ = runCalibration(cryMod)
