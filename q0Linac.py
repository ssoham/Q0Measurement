import json
from datetime import datetime, timedelta
from operator import itemgetter
from os.path import isfile
from time import sleep
from typing import Dict, List, Tuple

from epics import PV
from numpy import log10, mean, nanmean
from scipy.stats import linregress

import dataSession
import utils
from lcls_tools.devices.scLinac import Cavity, Cryomodule, Linac, Rack


class Q0Cavity(Cavity):
    def __init__(self, cavityNum: int, rackObject: Rack):
        super(Q0Cavity).__init__(cavityNum, rackObject)

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

        self.amplitudeDesPV: str = self.pvPrefix + "ADES"
        self.amplitudeActPV: str = self.pvPrefix + "AACTMEAN"

        self.llrfDataAcqEnablePVs: List[PV] = [PV(self.pvPrefix
                                                  + "{infix}:ENABLE".format(infix=infix))
                                               for infix in ["CAV", "FWD", "REV"]]

        self.llrfPVValuePairs: List[Tuple[PV, float]] = [(PV(self.pvPrefix + "MODE"), 1),
                                                         (PV(self.pvPrefix + "HLDOFF"), 0.1),
                                                         (PV(self.pvPrefix + "STAT_START"), 0.065),
                                                         (PV(self.pvPrefix + "STAT_WIDTH"), 0.004),
                                                         (PV(self.pvPrefix + "DECIM"), 255)]

        self.ssaStatusPV = PV(self.pvPrefix + "SSA:StatusMsg")
        self.ssaTurnOnPV = PV(self.pvPrefix + "SSA:PowerOn")
        self.ssaTurnOffPV = PV(self.pvPrefix + "SSA:PowerOff")
        self.ssaResetPV = PV(self.pvPrefix + "SSA:FaultReset")

        self.ssaCalibrationStartPV = PV(self.pvPrefix + "SSACAL:STRT")
        self.ssaCalibrationStatusPV = PV(self.pvPrefix + "SSACAL:STS")

        self.probeCalibrationStartPV = PV(self.pvPrefix + "PROBECAL:STRT")
        self.probeCalibrationStatusPV = PV(self.pvPrefix + "PROBECAL:STS")

        self.interlockResetPV = PV(self.pvPrefix + "INTLK_RESET_ALL")

        self.savedSSASlopePV = PV(self.pvPrefix + "SSA:SLOPE")
        self.measuredSSASlopePV = PV(self.pvPrefix + "SSA:SLOPE_NEW")
        self.pushSSASlopePV = PV(self.pvPrefix + "PUSH_SSASLOPE.PROC")

        self.savedQLoadedPV = PV(self.pvPrefix + "QLOADED")
        self.measuredQLoadedPV = PV(self.pvPrefix + "QLOADED_NEW")
        self.pushQLoadedPV = PV(self.pvPrefix + "PUSH_QLOADED.PROC")

        self.savedCavityScale = PV(self.pvPrefix + "CAV:SCALER_SEL.B")
        self.measuredCavityScalePV = PV(self.pvPrefix + "CAV:CAL_SCALEB_NEW")
        self.pushCavityScalePV = PV(self.pvPrefix + "PUSH_CAV_SCALE.PROC")

        self.SELAmplPV: PV = PV(self.pvPrefix + "SEL_ASET")

    def characterize(self):
        """
        Characterize various cavity parameters.
        * Runs the SSA through its range and constructs a polynomial describing
        the relationship between requested SSA output and actual output
        * Calibrates the cavity's RF probe so that the gradient readback will be
        accurate.
        :return:
        """

        self.runCalibration(startPV=self.ssaCalibrationStartPV,
                            statusPV=self.ssaCalibrationStatusPV)

        self.pushCalibrationChange(measuredPV=self.measuredSSASlopePV,
                                   savedPV=self.savedSSASlopePV,
                                   tolerance=utils.SSA_SLOPE_CHANGE_TOL,
                                   pushPV=self.pushSSASlopePV)

        self.interlockResetPV.put(1)
        sleep(2)

        self.runCalibration(startPV=self.probeCalibrationStartPV,
                            statusPV=self.probeCalibrationStatusPV)

        self.pushCalibrationChange(measuredPV=self.measuredQLoadedPV,
                                   savedPV=self.savedQLoadedPV,
                                   tolerance=utils.LOADED_Q_CHANGE_TOL,
                                   pushPV=self.pushQLoadedPV)

        self.pushCalibrationChange(measuredPV=self.measuredCavityScalePV,
                                   savedPV=self.savedCavityScale,
                                   tolerance=utils.CAVITY_SCALE_CHANGE_TOL,
                                   pushPV=self.pushCavityScalePV)

    def checkAcqControl(self):
        """
        Checks that the parameters associated with acquisition of the cavity RF
        waveforms are configured properly
        :return:
        """
        print("Checking Waveform Data Acquisition Control...")
        for pv in self.llrfDataAcqEnablePVs:
            if pv.value != 1:
                print("Enabling {pv}".format(pv=pv.pvname))
                pv.put(1)

        for pv, expectedValue in self.llrfPVValuePairs:
            if pv.value != expectedValue:
                print("Setting {pv}".format(pv=pv.pvname))
                pv.put(expectedValue)

    @staticmethod
    def pushCalibrationChange(measuredPV: PV, savedPV: PV, tolerance: float,
                              pushPV: PV):
        if abs(measuredPV.value - savedPV.value) < tolerance:
            pushPV.put(1)
        else:
            raise utils.RFError("Change to {pv} too large".format(pv=savedPV.pvname))

    # Checks that the parameters associated with acquisition of the cavity RF
    # waveforms are configured properly
    @staticmethod
    def runCalibration(startPV: PV, statusPV: PV):
        startPV.put(1)

        # 2 is running
        while statusPV.value == 2:
            sleep(1)

        # 0 is crashed
        if statusPV.value == 0:
            # TODO break these up into separate GUI calls for handling
            raise utils.RFError("{pv} crashed".format(pv=startPV))

    def setPowerStateSSA(self, turnOn: bool):

        if turnOn:
            stateMap = utils.SSAStateMap(desired=3, opposite=2, pv=self.ssaTurnOnPV)
        else:
            stateMap = utils.SSAStateMap(desired=2, opposite=3, pv=self.ssaTurnOffPV)

        if self.ssaStatusPV.value != stateMap.desired:
            if self.ssaStatusPV.value == stateMap.opposite:
                print("\nSetting SSA power...")
                stateMap.pv.put(1)

                if self.ssaStatusPV.value != stateMap.desired:
                    raise utils.RFError("Could not set SSA Power")

            else:
                print("\nResetting SSA...")
                self.ssaResetPV.put(1)

                if self.ssaStatusPV.value not in [2, 3]:
                    raise utils.RFError("Could not reset SSA")

                self.setPowerStateSSA(turnOn)

        print("SSA power set\n")


class Q0Cryomodule(Cryomodule, object):
    def __init__(self, cryoName: str, linacObject: Linac, _):

        super(Q0Cryomodule, self).__init__(cryoName, linacObject, Q0Cavity)
        self.cavities: Dict[int, Q0Cavity]
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

        self.valveParams = None

        self._calibIdxFile = ("calibrations/cm{CM}/calibrationsCM{CM}.json"
                              .format(CM=self.name))
        self._q0IdxFile = ("q0Measurements/cm{CM}/q0MeasurementsCM{CM}.json"
                           .format(CM=self.name))

        self._desiredGradients = None

    @property
    def calibIdxFile(self) -> str:

        if not isfile(self._calibIdxFile):
            utils.compatibleMkdirs(self._calibIdxFile)

        return self._calibIdxFile

    def addCalibDataSessionFromGUI(self, calibrationSelection: Dict[str, str]) -> dataSession.CalibDataSession:

        startTime = datetime.strptime(calibrationSelection["Start"], "%m/%d/%y %H:%M:%S")
        endTime = datetime.strptime(calibrationSelection["End"], "%m/%d/%y %H:%M:%S")

        try:
            timeInterval = int(calibrationSelection["Archiver Time Interval"])
        except (IndexError, ValueError):
            timeInterval = utils.ARCHIVER_TIME_INTERVAL

        utils.TimeParams = utils.TimeParams(startTime=startTime, endTime=endTime,
                                            timeInterval=timeInterval)

        utils.ValveParams = utils.ValveParams(refValvePos=float(calibrationSelection["JT Valve Position"]),
                                              refHeatLoadDes=float(calibrationSelection["Reference Heat Load (Des)"]),
                                              refHeatLoadAct=float(calibrationSelection["Reference Heat Load (Act)"]))

        return self.addCalibDataSession(utils.TimeParams, utils.ValveParams)

    def addCalibDataSession(self, timeParams: utils.TimeParams,
                            valveParams: utils.ValveParams) -> dataSession.CalibDataSession:

        sessionHash = utils.q0Hash([utils.TimeParams, utils.ValveParams])

        # Only create a new calibration data session if one doesn't already
        # exist with those exact parameters
        if sessionHash not in self.calibDataSessions:
            cryomodulePVs = utils.CryomodulePVs(valvePV=self.valvePV,
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

    # getRefutils.ValveParams searches over the last timeRange hours for a period
    # when the liquid level was stable and then fetches an averaged JT valve
    # position during that time as well as summed cavity heater DES and ACT
    # values. All three numbers get packaged and returned in a utils.ValveParams
    # object.
    def getRefValveParams(self, timeRange: float = utils.JT_SEARCH_TIME_RANGE) -> utils.ValveParams:

        def halfHourRoundDown(timeToRound: datetime) -> datetime:
            newMinute = 0 if timeToRound.minute < 30 else 30
            return datetime(timeToRound.year, timeToRound.month,
                            timeToRound.day, timeToRound.hour, newMinute, 0)

        print("\nDetermining required JT Valve position...")

        loopStart = datetime.now() - timedelta(hours=12)
        searchStart = loopStart - timedelta(hours=utils.HOURS_NEEDED_FOR_FLATNESS)
        searchStart = halfHourRoundDown(searchStart)

        numPoints = int((60 / utils.ARCHIVER_TIME_INTERVAL)
                        * (utils.HOURS_NEEDED_FOR_FLATNESS * 60))

        while (loopStart - searchStart) <= timedelta(hours=timeRange):

            formatter = "Checking {START} to {END} for liquid level stability."
            searchEnd = searchStart + timedelta(hours=utils.HOURS_NEEDED_FOR_FLATNESS)
            startStr = searchStart.strftime("%m/%d/%y %H:%M:%S")
            endStr = searchEnd.strftime("%m/%d/%y %H:%M:%S")
            print(formatter.format(START=startStr, END=endStr))

            archiverData = utils.getArchiverData(startTime=searchStart, numPoints=numPoints,
                                                 signals=[self.dsLevelPV])

            llVals = archiverData.values[self.dsLevelPV]

            # Fit a line to the liquid level over the last [numHours] hours
            m, b, _, _, _ = linregress(range(len(llVals)), llVals)

            # If the LL slope is small enough, this may be a good period from
            # which to get a reference valve position & heater params
            if log10(abs(m)) < -5:

                signals = ([self.valvePV] + self.heaterDesPVs
                           + self.heaterActPVs)

                data = utils.getArchiverData(startTime=searchStart,
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

                    return utils.ValveParams(desPos, heaterDes, heaterAct)

            searchStart -= timedelta(hours=utils.JT_SEARCH_HOURS_PER_STEP)

        # If we broke out of the while loop without returning anything, that
        # means that the LL hasn't been stable enough recently. Wait a while for
        # it to stabilize and then try again.
        complaint = ("Cryo conditions were not stable enough over the last"
                     " {NUM} hours - determining new JT valve position. Please"
                     " do not adjust the heaters. Allow the PID loop to "
                     "regulate the JT valve position.")
        print(complaint.format(NUM=timeRange))

        utils.writeAndWait("\nWaiting 30 minutes for LL to stabilize then "
                           "retrying...")

        start = datetime.now()
        while (datetime.now() - start).total_seconds() < 1800:
            utils.writeAndWait(".", 5)

        # Try again but only search the recent past. We have to manipulate the
        # search range a little bit due to how the search start time is rounded
        # down to the nearest half hour.
        return self.getRefValveParams(utils.HOURS_NEEDED_FOR_FLATNESS + 0.5)

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

        utils.writeAndWait("\nWaiting 5s for cryo to stabilize...\n", 5)

    def waitForLL(self):
        utils.writeAndWait("\nWaiting for downstream liquid level to be {LL}%..."
                           .format(LL=utils.MAX_DS_LL))

        while (utils.MAX_DS_LL - self.liquidLevelDS) > 0:
            utils.writeAndWait(".", 5)

        utils.writeAndWait(" downstream liquid level at required value.")

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
            archiverData = utils.getArchiverData(endTime=datetime.now(),
                                                 numPoints=utils.NUM_LL_POINTS_TO_AVG,
                                                 signals=[self.dsLevelPV],
                                                 timeInt=utils.ARCHIVER_TIME_INTERVAL)

            return nanmean(archiverData.values[self.dsLevelPV])

        # return the most recent value if we can't average for whatever reason
        except AttributeError:
            return self.dsLevelPVObject.value

    def launchHeaterRun(self, delta: float = utils.CAL_HEATER_DELTA) -> None:

        print("Ramping heaters to the next setting...")

        self.walkHeaters(delta)

        utils.writeAndWait(utils.RUN_STATUS_MSSG)

        startingLevel = self.liquidLevelDS
        avgLevel = startingLevel

        while ((startingLevel - avgLevel) < utils.TARGET_LL_DIFF and (
                avgLevel > utils.MIN_DS_LL)):
            utils.writeAndWait(".", 10)
            avgLevel = self.liquidLevelDS

        print("\nDone\n")

    def takeNewCalibration(self, initialCalibrationHeatload: int):

        if not self.valveParams:
            self.valveParams = self.getRefValveParams()

        deltaTot = self.valveParams.refHeatLoadDes - self.totalHeatDes

        startTime = datetime.now().replace(microsecond=0)

        # Lumping in the initial
        self.walkHeaters((initialCalibrationHeatload + deltaTot) / 8)

        self.waitForLL()
        # todo fill to 95%

        utils.writeAndWait("\nWaiting for JT Valve to be locked at {POS}..."
                           .format(POS=self.valveParams.refValvePos))

        # todo set self.jtModePV to 0
        # todo set self.jtPosSetpointPV to valveParams.refValvePos

        # Wait for the valve position to be within tolerance before continuing
        while abs(self.valvePVObject.value - self.valveParams.refValvePos) > utils.VALVE_POS_TOL:
            utils.writeAndWait(".", 5)

        utils.writeAndWait(" JT Valve locked.")

        self.launchHeaterRun(0)

        if (self.liquidLevelDS - utils.MIN_DS_LL) < utils.TARGET_LL_DIFF:
            print("Please ask the cryo group to refill to {LL} on the"
                  " downstream sensor".format(LL=utils.MAX_DS_LL))

            self.waitForCryo(self.valveParams.refValvePos)

        for _ in range(utils.NUM_CAL_STEPS - 1):
            self.launchHeaterRun()

            if (self.liquidLevelDS - utils.MIN_DS_LL) < utils.TARGET_LL_DIFF:
                print("Please ask the cryo group to refill to {LL} on the"
                      " downstream sensor".format(LL=utils.MAX_DS_LL))

                self.waitForCryo(self.valveParams.refValvePos)

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

        self.walkHeaters(-((utils.NUM_CAL_STEPS * utils.CAL_HEATER_DELTA) + 1))

        utils.TimeParams = utils.TimeParams(startTime, endTime, utils.ARCHIVER_TIME_INTERVAL)

        dataSession = self.addCalibDataSession(utils.TimeParams, self.valveParams)

        # Record this calibration dataSession's metadata

        dataDict = {"Total Heater Setpoint" : self.valveParams.refHeatLoadDes,
                    "Total Heater Readback" : self.valveParams.refHeatLoadAct,
                    "JT Valve Position"     : self.valveParams.refValvePos,
                    "Start Time"            : startTime.strftime("%m/%d/%y %H:%M:%S"),
                    "End Time"              : endTime.strftime("%m/%d/%y %H:%M:%S"),
                    "Archiver Time Interval": utils.ARCHIVER_TIME_INTERVAL}

        newData = json.dumps(dataDict)

        with open(self.calibIdxFile, 'w') as f:
            data: Dict = json.load(f)
            data.update(newData)
            json.dump(data, f)

        return dataSession, self.valveParams

    def takeNewQ0Measurement(self, desiredGradients: Dict[int, float],
                             calibSession: dataSession.CalibDataSession = None,
                             valveParams: utils.ValveParams = None) -> (dataSession.Q0DataSession, utils.ValveParams):
        try:
            self._desiredGradients = desiredGradients
            if not valveParams:
                valveParams = self.getRefValveParams()

            deltaTot = utils.ValveParams.refHeatLoadDes - self.totalHeatDes
            self.walkHeaters(deltaTot / 8)

            for cavity in self.cavities.values():

                print("\nRunning up Cavity {CAV}...".format(CAV=cavity.cavNum))

                cavity.checkAcqControl()
                cavity.setPowerStateSSA(True)
                cavity.characterize()

                cavity.SELAmplPV.put(15)

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

            self.waitForCryo(utils.ValveParams.refValvePos)

            startTime = self.holdGradient(desiredGradients).replace(microsecond=0)

            for cavity in self.cavities.values():

                if cavity.cavNum not in desiredGradients:
                    continue

                cavity.walkToGradient(5)
                cavity.powerDown()

            # self.waitForCryo(utils.ValveParams.refValvePos)
            self.waitForLL()
            self.walkHeaters(utils.FULL_MODULE_CALIBRATION_LOAD)
            self.waitForJT(utils.ValveParams.refValvePos)
            self.launchHeaterRun(0)
            endTime = datetime.now().replace(microsecond=0)

            print("\nEnd time: {END}".format(END=endTime))
            self.walkHeaters(-utils.FULL_MODULE_CALIBRATION_LOAD)

            utils.TimeParams = utils.TimeParams(startTime, endTime, utils.ARCHIVER_TIME_INTERVAL)

            desiredGradient = 0

            for grad in desiredGradients.values():
                desiredGradient += grad

            session = self.addQ0DataSession(utils.TimeParams, utils.ValveParams,
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
                        [self.cryModNumJLAB, utils.ValveParams.refHeatLoadDes,
                         utils.ValveParams.refHeatLoadAct, utils.ValveParams.refValvePos]
                        + desGrads + [totGrad, startTime.strftime("%m/%d/%y %H:%M:%S"),
                                      endTime.strftime("%m/%d/%y %H:%M:%S"),
                                      utils.ARCHIVER_TIME_INTERVAL])

            print("\nStart Time: {START}".format(START=startTime))
            print("End Time: {END}".format(END=endTime))

            duration = (endTime - startTime).total_seconds() / 3600
            print("Duration in hours: {DUR}".format(DUR=duration))

            return session, utils.ValveParams

        except(CalledProcessError, IndexError, OSError, ValueError,
               AssertionError, KeyboardInterrupt) as e:
            writeAndFlushStdErr(
                    "Procedure failed with error:\n{E}\n".format(E=e))
            for cavity in self.cavities.values():
                cavity.powerDown()


Q0_LINAC_OBJECTS: List[Linac] = []
Q0_CRYOMODULES: Dict[str, Q0Cryomodule] = {}
for name, cryomoduleList in LINACS:
    linacObject = Linac(name, cryomoduleList, cavityClass=Q0Cavity, cryomoduleClass=Q0Cryomodule)
    Q0_LINAC_OBJECTS.append(linacObject)
    Q0_CRYOMODULES.update(linacObject.cryomodules)
