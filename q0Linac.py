import json
from datetime import datetime
from typing import Dict, List

from epics import PV

import dataSession
from scLinac import Cavity, Cryomodule, LINACS, Linac, Rack
from utils import (ARCHIVER_TIME_INTERVAL, CAL_HEATER_DELTA, CryomodulePVs, HOURS_NEEDED_FOR_FLATNESS,
                   JT_SEARCH_HOURS_PER_STEP, JT_SEARCH_TIME_RANGE, RUN_STATUS_MSSG, TimeParams, ValveParams,
                   getArchiverData, q0Hash, writeAndWait, TARGET_LL_DIFF, compatibleMkdirs,
                   FULL_MODULE_CALIBRATION_LOAD)
from numpy import nanmean


class Q0Cavity(Cavity, object):
    def __init__(self, cavityNum: int, rackObject: Rack):
        super(Q0Cavity, self).__init__(cavityNum, rackObject)

        self._fieldEmissionPVs = None

        self.heaterDesPV = PV("CHTR:CM{CM}:1{CAV}55:HV:{SUFF}".format(CM=self.cryomodule.name,
                                                                      SUFF="POWER_SETPT",
                                                                      CAV=cavityNum))
        self.heaterActPV = "CHTR:CM{CM}:1{CAV}55:HV:{SUFF}".format(SUFF="POWER",
                                                                   CM=self.cryomodule.name,
                                                                   CAV=cavityNum)

        self._idxFile = ("q0Measurements/cm{CM}/cav{CAV}/q0MeasurementsCM{CM}CAV{CAV}.csv"
                         .format(CM=self.cryomodule.name, CAV=cavityNum))

        self._calibIdxFile = ("calibrations/cm{CM}/cav{CAV}/calibrationsCM{CM}CAV{CAV}.csv"
                              .format(CM=self.cryomodule.name, CAV=cavityNum))

        self.amplitudeDesPV = self.pvPrefix + "ADES"
        self.amplitudeActPV = self.pvPrefix + "AACTMEAN"


class Q0Cryomodule(Cryomodule, object):
    def __init__(self, cryoName: str, linacObject: Linac, _):

        super(Q0Cryomodule, self).__init__(cryoName, linacObject, Q0Cavity)
        self.dsPressurePV = "CPT:CM{CM}:2302:DS:PRESS".format(CM=cryoName)
        self.jtModePV = "CPV:CM{CM}:3001:JT:MODE".format(CM=cryoName)
        self.jtPosSetpointPV = "CPV:CM{CM}:3001:JT:POS_SETPT".format(CM=cryoName)

        lvlFormatStr = "CLL:CM{CM}:{{INFIX}}:{{LOC}}:LVL".format(CM=cryoName)
        self.dsLevelPV = lvlFormatStr.format(INFIX="2301", LOC="DS")
        self.dsLevelPVObject = PV(self.dsLevelPV)
        self.usLevelPV = lvlFormatStr.format(INFIX="2601", LOC="US")

        valveLockFormatter = "CPID:CM{CM}:3001:JT:CV_{{SUFF}}".format(CM=cryoName)
        self.cvMaxPV = valveLockFormatter.format(SUFF="MAX")
        self.cvMinPV = valveLockFormatter.format(SUFF="MIN")
        self.valvePV = valveLockFormatter.format(SUFF="VALUE")
        self.valvePVObject = PV(self.valvePV)

        self.q0DataSessions = {}
        self.calibDataSessions = {}

        self.heaterDesPVObjects = []
        self.heaterActPVs = []

        for q0Cavity in self.cavities.values():
            self.heaterActPVs.append(q0Cavity.heaterActPV)
            self.heaterDesPVObjects.append(q0Cavity.heaterDesPV)

        self.heaterDesPVs = [pv.pvname for pv in self.heaterDesPVObjects]
        # self.heaterActPVObjects = list(map(PV, self.heaterActPVs))

        self.valveParamsForNewMeasurement = None

        self._calibIdxFile = ("calibrations/cm{CM}/calibrationsCM{CM}.json"
                              .format(CM=self.name))
        self._q0IdxFile = ("q0Measurements/cm{CM}/q0MeasurementsCM{CM}.json"
                           .format(CM=self.name))

    @property
    def calibIdxFile(self):
        # type: () -> str

        if not isfile(self._calibIdxFile):
            compatibleMkdirs(self._calibIdxFile)

        return self._calibIdxFile

    def addCalibDataSessionFromGUI(self, calibrationSelection: Dict[str, str]) -> dataSession.CalibDataSession:

        startTime = datetime.strptime(calibrationSelection["Start"], "%m/%d/%y %H:%M:%S")
        endTime = datetime.strptime(calibrationSelection["End"], "%m/%d/%y %H:%M:%S")

        try:
            timeInterval = int(calibrationSelection["Archiver Time Interval"])
        except (IndexError, ValueError):
            timeInterval = ARCHIVER_TIME_INTERVAL

        timeParams = TimeParams(startTime=startTime, endTime=endTime,
                                timeInterval=timeInterval)

        valveParams = ValveParams(refValvePos=float(calibrationSelection["JT Valve Position"]),
                                  refHeatLoadDes=float(calibrationSelection["Reference Heat Load (Des)"]),
                                  refHeatLoadAct=float(calibrationSelection["Reference Heat Load (Act)"]))

        return self.addCalibDataSession(timeParams, valveParams)

    def addCalibDataSession(self, timeParams: TimeParams, valveParams: ValveParams) -> dataSession.CalibDataSession:

        sessionHash = q0Hash([timeParams, valveParams])

        # Only create a new calibration data session if one doesn't already
        # exist with those exact parameters
        if sessionHash not in self.calibDataSessions:
            cryomodulePVs = CryomodulePVs(valvePV=self.valvePV,
                                          dsLevelPV=self.dsLevelPV,
                                          usLevelPV=self.usLevelPV,
                                          dsPressurePV=self.dsPressurePV,
                                          heaterDesPVs=self.heaterDesPVs,
                                          heaterActPVs=self.heaterActPVs)

            session = dataSession.CalibDataSession(timeParams=timeParams,
                                                   valveParams=valveParams,
                                                   cryomodulePVs=cryomodulePVs,
                                                   cryoModuleName=self.name)
            self.calibDataSessions[sessionHash] = session

        return self.calibDataSessions[sessionHash]

    # getRefValveParams searches over the last timeRange hours for a period
    # when the liquid level was stable and then fetches an averaged JT valve
    # position during that time as well as summed cavity heater DES and ACT
    # values. All three numbers get packaged and returned in a ValveParams
    # object.
    def getRefValveParams(self, timeRange: float = JT_SEARCH_TIME_RANGE) -> ValveParams:

        def halfHourRoundDown(timeToRound: datetime) -> datetime:
            newMinute = 0 if timeToRound.minute < 30 else 30
            return datetime(timeToRound.year, timeToRound.month,
                            timeToRound.day, timeToRound.hour, newMinute, 0)

        print("\nDetermining required JT Valve position...")

        loopStart = datetime.now() - timedelta(hours=12)
        searchStart = loopStart - timedelta(hours=HOURS_NEEDED_FOR_FLATNESS)
        searchStart = halfHourRoundDown(searchStart)

        numPoints = int((60 / ARCHIVER_TIME_INTERVAL)
                        * (HOURS_NEEDED_FOR_FLATNESS * 60))

        while (loopStart - searchStart) <= timedelta(hours=timeRange):

            formatter = "Checking {START} to {END} for liquid level stability."
            searchEnd = searchStart + timedelta(hours=HOURS_NEEDED_FOR_FLATNESS)
            startStr = searchStart.strftime("%m/%d/%y %H:%M:%S")
            endStr = searchEnd.strftime("%m/%d/%y %H:%M:%S")
            print(formatter.format(START=startStr, END=endStr))

            archiverData = getArchiverData(startTime=searchStart, numPoints=numPoints,
                                           signals=[self.dsLevelPV])

            llVals = archiverData.values[self.dsLevelPV]

            # Fit a line to the liquid level over the last [numHours] hours
            m, b, _, _, _ = linregress(range(len(llVals)), llVals)

            # If the LL slope is small enough, this may be a good period from
            # which to get a reference valve position & heater params
            if log10(abs(m)) < -5:

                signals = ([self.valvePV] + self.heaterDesPVs
                           + self.heaterActPVs)

                data = getArchiverData(startTime=searchStart,
                                       numPoints=numPoints, signals=signals)
                valveVals = data.values[self.valvePV]
                heaterDesVals = [sum(x) for x in zip(*itemgetter(*self.heaterDesPVs)(data.values))]
                heaterActVals = [sum(x) for x in zip(*itemgetter(*self.heaterActPVs)(data.values))]

                desValSet = set(heaterDesVals)

                # We only want to use time periods in which there were no
                # changes made to the heater settings
                if len(desValSet) == 1:
                    desPos = round(mean(valveVals), 1)
                    heaterDes = desValSet.pop()
                    heaterAct = mean(heaterActVals)

                    print("Stable period found.")
                    formatter = "{THING} is {VAL}"
                    print(formatter.format(THING="Desired JT valve position",
                                           VAL=desPos))
                    print(formatter.format(THING="Total heater DES setting",
                                           VAL=heaterDes))

                    return ValveParams(desPos, heaterDes, heaterAct)

            searchStart -= timedelta(hours=JT_SEARCH_HOURS_PER_STEP)

        # If we broke out of the while loop without returning anything, that
        # means that the LL hasn't been stable enough recently. Wait a while for
        # it to stabilize and then try again.
        complaint = ("Cryo conditions were not stable enough over the last"
                     " {NUM} hours - determining new JT valve position. Please"
                     " do not adjust the heaters. Allow the PID loop to "
                     "regulate the JT valve position.")
        print(complaint.format(NUM=timeRange))

        writeAndWait("\nWaiting 30 minutes for LL to stabilize then "
                     "retrying...")

        start = datetime.now()
        while (datetime.now() - start).total_seconds() < 1800:
            writeAndWait(".", 5)

        # Try again but only search the recent past. We have to manipulate the
        # search range a little bit due to how the search start time is rounded
        # down to the nearest half hour.
        return self.getRefValveParams(HOURS_NEEDED_FOR_FLATNESS + 0.5)

    def walkHeaters(self, perHeaterDelta: float):

        if perHeaterDelta == 0:
            return

        formatter = "\nWalking CM{NUM} heaters {DIR} by {VAL}"
        dirStr = "up" if perHeaterDelta > 0 else "down"
        formatter = formatter.format(NUM=self.name, DIR=dirStr,
                                     VAL=abs(perHeaterDelta))
        print(formatter)

        if abs(perHeaterDelta) <= 1:
            for heaterSetpointPV in self.heaterDesPVObjects:
                currVal = heaterSetpointPV.value
                heaterSetpointPV.put(currVal + perHeaterDelta)

        else:

            # This whole thing is so that we only do 8W/min
            # TODO clean this
            steps = abs(int(perHeaterDelta))
            finalDelta = abs(perHeaterDelta) - steps

            # 1 or -1 depending on the direction
            stepDelta = perHeaterDelta / steps

            for i in range(steps):

                for heaterSetpointPV in self.heaterDesPVObjects:
                    currVal = heaterSetpointPV.value
                    heaterSetpointPV.put(currVal + stepDelta)

                sleep(60)

            for heaterSetpointPV in self.heaterDesPVObjects:
                currVal = heaterSetpointPV.value
                heaterSetpointPV.put(currVal + (finalDelta * stepDelta))

        writeAndWait("\nWaiting 5s for cryo to stabilize...\n", 5)

    def waitForLL(self):
        writeAndWait("\nWaiting for downstream liquid level to be {LL}%..."
                     .format(LL=MAX_DS_LL))

        while (MAX_DS_LL - self.liquidLevelDS) > 0:
            writeAndWait(".", 5)

        writeAndWait(" downstream liquid level at required value.")

    @property
    def totalHeatDes(self) -> float:
        heatDes = 0
        for pv in self.heaterDesPVObjects:
            heatDes += pv.value
        return heatDes

    @property
    def liquidLevelDS(self) -> float:
        # try to do averaging of the last NUM_LL_POINTS_TO_AVG points to account
        # for signal noise
        try:
            archiverData = getArchiverData(endTime=datetime.now(),
                                           numPoints=NUM_LL_POINTS_TO_AVG,
                                           signals=[self.dsLevelPV],
                                           timeInt=ARCHIVER_TIME_INTERVAL)

            return nanmean(archiverData.values[self.dsLevelPV])

        # return the most recent value if we can't average for whatever reason
        except AttributeError:
            return self.dsLevelPVObject.value

    def launchHeaterRun(self, delta: float = CAL_HEATER_DELTA) -> None:

        print("Ramping heaters to the next setting...")

        self.walkHeaters(delta)

        writeAndWait(RUN_STATUS_MSSG)

        startingLevel = self.liquidLevelDS
        avgLevel = startingLevel

        while ((startingLevel - avgLevel) < TARGET_LL_DIFF and (
                avgLevel > MIN_DS_LL)):
            writeAndWait(".", 10)
            avgLevel = self.liquidLevelDS

        print("\nDone\n")

    def takeNewCalibration(self, initialCalibrationHeatload: int):

        if not self.valveParamsForNewMeasurement:
            self.valveParamsForNewMeasurement = self.getRefValveParams()

        deltaTot = self.valveParamsForNewMeasurement.refHeatLoadDes - self.totalHeatDes

        startTime = datetime.now().replace(microsecond=0)

        # Lumping in the initial
        self.walkHeaters((initialCalibrationHeatload + deltaTot) / 8)

        self.waitForLL()
        # todo fill to 95%

        writeAndWait("\nWaiting for JT Valve to be locked at {POS}..."
                     .format(POS=self.valveParamsForNewMeasurement.refValvePos))

        # todo set self.jtModePV to 0
        # todo set self.jtPosSetpointPV to valveParams.refValvePos

        # Wait for the valve position to be within tolerance before continuing
        while abs(self.valvePVObject.value - refValvePos) > VALVE_POS_TOL:
            writeAndWait(".", 5)

        writeAndWait(" JT Valve locked.")

        self.launchHeaterRun(0)

        if (self.liquidLevelDS - MIN_DS_LL) < TARGET_LL_DIFF:
            print("Please ask the cryo group to refill to {LL} on the"
                  " downstream sensor".format(LL=MAX_DS_LL))

            self.waitForCryo(valveParams.refValvePos)

        for _ in range(NUM_CAL_STEPS - 1):
            self.launchHeaterRun()

            if (self.liquidLevelDS - MIN_DS_LL) < TARGET_LL_DIFF:
                print("Please ask the cryo group to refill to {LL} on the"
                      " downstream sensor".format(LL=MAX_DS_LL))

                self.waitForCryo(valveParams.refValvePos)

        # Kinda jank way to avoid waiting for cryo conditions after the final
        # run
        self.launchHeaterRun()

        endTime = datetime.now().replace(microsecond=0)

        print("\nStart Time: {START}".format(START=startTime))
        print("End Time: {END}".format(END=datetime.now()))

        duration = (datetime.now() - startTime).total_seconds() / 3600
        print("Duration in hours: {DUR}".format(DUR=duration))

        # Walking the heaters back to their starting settings
        # self.walkHeaters(-NUM_CAL_RUNS)

        self.walkHeaters(-((NUM_CAL_STEPS * CAL_HEATER_DELTA) + 1))

        timeParams = TimeParams(startTime, endTime, MYSAMPLER_TIME_INTERVAL)

        dataSession = self.addCalibDataSession(timeParams, valveParams)

        # Record this calibration dataSession's metadata

        csvWriter.writerow(["JLAB Number", "Reference Heat Load (Des)",
                            "Reference Heat Load (Act)",
                            "JT Valve Position", "Start", "End",
                            "MySampler Time Interval"])

        dataDict = {"Total Heater Setpoint" : valveParams.refHeatLoadDes,
                    "Total Heater Readback" : valveParams.refHeatLoadAct,
                    "JT Valve Position"     : valveParams.refValvePos,
                    "Start Time"            : startTime.strftime("%m/%d/%y %H:%M:%S"),
                    "End Time"              : endTime.strftime("%m/%d/%y %H:%M:%S"),
                    "Archiver Time Interval": ARCHIVER_TIME_INTERVAL}

        newData = dumps(dataDict)

        with open(self.calibIdxFile, 'w') as f:
            data: Dict = json.load(f)
            data.update(newData)
            json.dump(data, f)

        return dataSession, valveParams

    def takeNewQ0Measurement(self, desiredGradients: Dict[int, float],
                             calibSession: dataSession.CalibDataSession = None,
                             valveParams: ValveParams = None) -> (dataSession.Q0DataSession, ValveParams):
        try:
            self._desiredGrads = desiredGradients
            if not valveParams:
                valveParams = self.getRefValveParams()

            deltaTot = valveParams.refHeatLoadDes - self.totalHeatDes
            self.walkHeaters(deltaTot / 8)

            for cavity in self.cavities.values():

                if cavity.cavNum not in desiredGradients:
                    continue

                print("\nRunning up Cavity {CAV}...".format(CAV=cavity.cavNum))

                cavity.checkAcqControl()
                cavity.setPowerStateSSA(True)
                cavity.characterize()

                # Setting the RF low and ramping up is time consuming so we skip it
                # during testing
                if not TEST_MODE:
                    caputPV(cavity.genAcclPV("SEL_ASET"), "15")

                # Start with pulsed mode
                cavity.setModeRF("4")

                cavity.setStateRF(True)
                cavity.pushGoButton()

                cavity.checkAndSetOnTime()
                cavity.checkAndSetDrive()

                cavity.phaseCavity()

                if not TEST_MODE:
                    cavity.lowerAmplitude()

                # go to CW
                cavity.setModeRF("2")

                cavity.walkToGradient(desiredGradients[cavity.cavNum])

            self.waitForCryo(valveParams.refValvePos)

            startTime = self.holdGradient(desiredGradients).replace(microsecond=0)

            for cavity in self.cavities.values():

                if cavity.cavNum not in desiredGradients:
                    continue

                cavity.walkToGradient(5)
                cavity.powerDown()

            # self.waitForCryo(valveParams.refValvePos)
            self.waitForLL()
            self.walkHeaters(FULL_MODULE_CALIBRATION_LOAD)
            self.waitForJT(valveParams.refValvePos)
            self.launchHeaterRun(0)
            endTime = datetime.now().replace(microsecond=0)

            print("\nEnd time: {END}".format(END=endTime))
            self.walkHeaters(-FULL_MODULE_CALIBRATION_LOAD)

            timeParams = TimeParams(startTime, endTime, ARCHIVER_TIME_INTERVAL)

            desiredGradient = 0

            for grad in desiredGradients.values():
                desiredGradient += grad

            session = self.addQ0DataSession(timeParams, valveParams,
                                            refGradVal=desiredGradient,
                                            calibSession=calibSession)

            desGrads = []
            totGrad = 0
            for i in range(8):
                if (i + 1) in desiredGradients:
                    desGrads.append(desiredGradients[i + 1])
                    totGrad += desiredGradients[i + 1]
                else:
                    desGrads.append(0)

            with open(self.q0IdxFile, 'a') as f:
                csvWriter = writer(f)
                csvWriter.writerow(
                        [self.cryModNumJLAB, valveParams.refHeatLoadDes,
                         valveParams.refHeatLoadAct, valveParams.refValvePos]
                        + desGrads + [totGrad, startTime.strftime("%m/%d/%y %H:%M:%S"),
                                      endTime.strftime("%m/%d/%y %H:%M:%S"),
                                      ARCHIVER_TIME_INTERVAL])

            print("\nStart Time: {START}".format(START=startTime))
            print("End Time: {END}".format(END=endTime))

            duration = (endTime - startTime).total_seconds() / 3600
            print("Duration in hours: {DUR}".format(DUR=duration))

            return session, valveParams

        except(CalledProcessError, IndexError, OSError, ValueError,
               AssertionError, KeyboardInterrupt) as e:
            writeAndFlushStdErr(
                    "Procedure failed with error:\n{E}\n".format(E=e))
            for cavity in self.cavities.values():
                cavity.powerDown()


Q0_LINAC_OBJECTS: List[Linac] = []
for name, cryomoduleList in LINACS:
    Q0_LINAC_OBJECTS.append(Linac(name, cryomoduleList, cavityClass=Q0Cavity,
                                  cryomoduleClass=Q0Cryomodule))
