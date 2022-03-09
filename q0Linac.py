from scLinac import Cavity, Cryomodule, Rack, Linac, LINACS
from typing import Type, Dict, List
from datetime import datetime
from utils import (ARCHIVER_TIME_INTERVAL, TimeParams, ValveParams, q0Hash,
                   CryomodulePVs, JT_SEARCH_TIME_RANGE, JT_SEARCH_HOURS_PER_STEP,
                   HOURS_NEEDED_FOR_FLATNESS, getArchiverData, writeAndWait)
import dataSession
from epics import PV


class Q0Cavity(Cavity, object):
    def __init__(self, cavityNum, rackObject):
        # type: (int, Rack) -> None

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
    def __init__(self, cryoName, linacObject, _):
        # type: (str, Linac, Q0Cavity) -> None

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

        self.heaterDesPVs = []
        self.heaterActPVs = []

        for q0Cavity in self.cavities.values():
            self.heaterActPVs.append(q0Cavity.heaterActPV)
            self.heaterDesPVs.append(q0Cavity.heaterDesPV)

        self.heaterDesPVObjects = list(map(PV, self.heaterDesPVs))
        # self.heaterActPVObjects = list(map(PV, self.heaterActPVs))

        self.valveParamsForNewMeasurement = None

    def addCalibDataSessionFromGUI(self, calibrationSelection):
        # type: (Dict[str]) -> dataSession.CalibDataSession

        startTime = datetime.strptime(calibrationSelection["Start"], "%m/%d/%y %H:%M:%S")
        endTime = datetime.strptime(calibrationSelection["End"], "%m/%d/%y %H:%M:%S")

        try:
            timeInterval = int(calibrationSelection["MySampler Time Interval"])
        except (IndexError, ValueError):
            timeInterval = ARCHIVER_TIME_INTERVAL

        timeParams = TimeParams(startTime=startTime, endTime=endTime,
                                timeInterval=timeInterval)

        valveParams = ValveParams(refValvePos=float(calibrationSelection["JT Valve Position"]),
                                  refHeatLoadDes=float(calibrationSelection["Reference Heat Load (Des)"]),
                                  refHeatLoadAct=float(calibrationSelection["Reference Heat Load (Act)"]))

        return self.addCalibDataSession(timeParams, valveParams)

    def addCalibDataSession(self, timeParams, valveParams):
        # type: (TimeParams, ValveParams) -> dataSession.CalibDataSession

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

        def halfHourRoundDown(timeToRound):
            # type: (datetime) -> datetime
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

                data = getArchiverData(searchStart, numPoints, signals)
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
        formatter = formatter.format(NUM=self.cryModNumSLAC, DIR=dirStr,
                                     VAL=abs(perHeaterDelta))
        print(formatter)

        if abs(perHeaterDelta) <= 1:
            for heaterSetpointPV in self.heaterDesPVObjects:
                currVal = heaterSetpointPV.value
                heaterSetpointPV.put(currVal + perHeaterDelta)

        # This whole thing is so that we only do 8W/min
        steps = abs(int(perHeaterDelta))
        stepDelta = perHeaterDelta / steps

        for i in range(steps):

            for heaterSetpointPV in self.heaterDesPVObjects:
                currVal = heaterSetpointPV.value
                heaterSetpointPV.put(currVal + stepDelta)

            sleep(60)

        writeAndWait("\nWaiting 5s for cryo to stabilize...\n", 5)

    def waitForLL(self):
        # type: () -> None
        writeAndWait("\nWaiting for downstream liquid level to be {LL}%..."
                     .format(LL=MAX_DS_LL))

        while (MAX_DS_LL - self.dsLevelPVObject.value) > 0:
            writeAndWait(".", 5)

        writeAndWait(" downstream liquid level at required value.")

    @property
    def totalHeatDes(self):
        # type: () -> float
        heatDes = 0
        for pv in self.heaterDesPVObjects:
            heatDes += pv.value
        return heatDes

    def launchHeaterRun(self, delta=CAL_HEATER_DELTA):
        # type: (float) -> None

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

    def takeNewCalibration(self, initialCalibrationHeatload):
        # type: (int) -> None

        if not self.valveParamsForNewMeasurement:
            self.valveParamsForNewMeasurement = self.getRefValveParams()

        deltaTot = self.valveParamsForNewMeasurement.refHeatLoadDes - self.totalHeatDes

        startTime = datetime.now().replace(microsecond=0)

        self.walkHeaters((initialCalibrationHeatload + deltaTot) / 8)

        self.waitForLL()
        # todo mess with cryo

        writeAndWait("\nWaiting for JT Valve to be locked at {POS}..."
                     .format(POS=self.valveParamsForNewMeasurement.refValvePos))
        # set self.jtModePV to 0
        # set self.jtPosSetpointPV to valveParams.refValvePos

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
        with open(self.calibIdxFile, 'a') as f:
            csvWriter = writer(f)
            csvWriter.writerow([self.cryModNumJLAB, valveParams.refHeatLoadDes,
                                valveParams.refHeatLoadAct,
                                valveParams.refValvePos,
                                startTime.strftime("%m/%d/%y %H:%M:%S"),
                                endTime.strftime("%m/%d/%y %H:%M:%S"),
                                MYSAMPLER_TIME_INTERVAL])

        return dataSession, valveParams


Q0_LINAC_OBJECTS: List[Linac] = []
for name, cryomoduleList in LINACS:
    Q0_LINAC_OBJECTS.append(Linac(name, cryomoduleList, cavityClass=Q0Cavity,
                                  cryomoduleClass=Q0Cryomodule))
