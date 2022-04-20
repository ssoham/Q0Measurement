import json
from datetime import datetime, timedelta
from operator import itemgetter
from os.path import isfile
from subprocess import CalledProcessError
from time import sleep
from typing import Dict, List, Tuple

from epics import PV
from numpy import log10, mean, nanmean, sign
from scipy.stats import linregress

import dataSession
import utils
from lcls_tools.devices.scLinac import Cavity, Cryomodule, LINAC_TUPLES, Linac, Rack


class Q0Cavity(Cavity, object):
    def __init__(self, cavityNum: int, rackObject: Rack):
        super(Q0Cavity, self).__init__(cavityNum, rackObject)

        self._fieldEmissionPVs = None

        self.heaterDesPV = PV("CHTR:CM{CM}:1{CAV}55:HV:{SUFF}".format(CM=self.cryomodule.name,
                                                                      SUFF="POWER_SETPT",
                                                                      CAV=cavityNum))
        self.heaterActPV = PV("CHTR:CM{CM}:1{CAV}55:HV:{SUFF}".format(SUFF="POWER",
                                                                      CM=self.cryomodule.name,
                                                                      CAV=cavityNum))

        self._idxFile = ("q0Measurements/cm{CM}/cav{CAV}/q0MeasurementsCM{CM}CAV{CAV}.csv"
                         .format(CM=self.cryomodule.name, CAV=cavityNum))

        self._calibIdxFile = ("calibrations/cm{CM}/cav{CAV}/calibrationsCM{CM}CAV{CAV}.csv"
                              .format(CM=self.cryomodule.name, CAV=cavityNum))

        self.amplitudeDesPVStr: str = self.pvPrefix + "ADES"
        self.amplitudeDesPVObject: PV = PV(self.amplitudeDesPVStr)

        self.amplitudeActPVStr: str = self.pvPrefix + "AACTMEAN"
        self.amplitudeActPVObject = PV(self.amplitudeActPVStr)
        self.amplitudeActPVObject.add_callback(self.quenchCheckCallback)

        self.gradientActPVObject = PV(self.pvPrefix + "GACTMEAN")

        self.llrfDataAcqEnablePVs: List[PV] = [PV(self.pvPrefix
                                                  + "{infix}:ENABLE".format(infix=infix))
                                               for infix in ["CAV", "FWD", "REV"]]

        self.llrfPVValuePairs: List[Tuple[PV, float]] = [(PV(self.pvPrefix + "ACQ_MODE"), 1),
                                                         (PV(self.pvPrefix + "ACQ_HLDOFF"), 0.1),
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

        self.pulseDriveLevelPV: PV = PV(self.pvPrefix + "SEL_ASET")
        self.rfModePV = PV(self.pvPrefix + "RFMODECTRL")

        self.rfStatePV = PV(self.pvPrefix + "RFSTATE")
        self.rfControlPV = PV(self.pvPrefix + "RFCTRL")

        self.pulseGoButtonPV = PV(self.pvPrefix + "PULSE_DIFF_SUM")
        self.pulseStatusPV = PV(self.pvPrefix + "PULSE_STATUS")

        self.quenchBypassPVObject = PV(self.pvPrefix + "QUENCH_BYP_RBV")

        self.pulseOnTimePVObject = PV(self.pvPrefix + "PULSE_ONTIME")

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

    def checkAndSetDrive(self):
        """
        Ramps the cavity's RF drive (only relevant in pulsed mode) up until the RF
        gradient is high enough for phasing
        :return:
        """

        print("Checking drive...")

        while (self.gradientActPVObject.value < 1) or (self.pulseDriveLevelPV.value < 15):
            print("Increasing drive...")
            self.pulseDriveLevelPV.put(self.pulseDriveLevelPV.value + 1)
            self.pushGoButton()

        print("Drive set")

    def checkAndSetOnTime(self):
        """
        In pulsed mode the cavity has a duty cycle determined by the on time and
        off time. We want the on time to be 70 ms or else the various cavity
        parameters calculated from the waveform (e.g. the RF gradient) won't be
        accurate.
        :return:
        """
        print("Checking RF Pulse On Time...")
        if self.pulseOnTimePVObject.value != 70:
            print("Setting RF Pulse On Time to 70 ms")
            self.pulseOnTimePVObject.put(70)
            self.pushGoButton()

    def quenchCheckCallback(self, **kw):
        sleep(0.1)

        if self.amplitudeActPVObject.value < (self.amplitudeDesPVObject.value * 0.9):
            # If the EPICs quench detection is disabled and we see a quench,
            # shut the cavity down
            if self.quenchBypassPVObject.value == 1:
                raise utils.QuenchError
            # If the EPICs quench detection is enabled just print a warning
            # message
            else:
                print(str(utils.QuenchError))

    @staticmethod
    def pushCalibrationChange(measuredPV: PV, savedPV: PV, tolerance: float,
                              pushPV: PV):
        if abs(measuredPV.value - savedPV.value) < tolerance:
            pushPV.put(1)
        else:
            raise utils.RFError("Change to {pv} too large".format(pv=savedPV.pvname))

    def pushGoButton(self):
        """
        Many of the changes made to a cavity don't actually take effect until the
        go button is pressed
        :return:
        """
        self.pulseGoButtonPV.put(1)
        while self.pulseStatusPV.value < 2:
            sleep(1)
        if self.pulseStatusPV.value > 2:
            raise utils.RFError("Unable to pulse cavity")

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

    def setStateRF(self, turnOn: bool):
        """
        Turn the cavity on or off
        :param turnOn:
        :return:
        """

        rfState = self.rfStatePV.value

        desiredState = (1 if turnOn else 0)

        if rfState != desiredState:
            print("\nSetting RF State...")
            self.rfControlPV.put(desiredState)

        print("RF state set\n")

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

    # Walks the cavity to a given gradient in CW mode with exponential back-off
    # in the step size (steps get smaller each time you cross over the desired
    # gradient until the error is very low)
    def walkToAmplitude(self, desiredAmplitude: float, step: float = 0.5,
                        loopTime: timedelta = timedelta(seconds=2.5), gradTol: float = 0.05,
                        printStatus: bool = True):

        if printStatus:
            utils.writeAndWait("\nWalking gradient...")

        diff = desiredAmplitude - self.amplitudeActPVObject.value

        if abs(diff) <= step:
            self.amplitudeDesPVObject.put(self.amplitudeActPVObject.value + diff)
            print("\nGradient at desired value")
            return

        else:
            self.amplitudeDesPVObject.put(self.amplitudeActPVObject.value + sign(diff) * step)
            utils.writeAndWait(".", loopTime.total_seconds())
            self.walkToAmplitude(desiredAmplitude, step, loopTime, gradTol, False)


class Q0Cryomodule(Cryomodule, object):
    def __init__(self, cryoName: str, linacObject: Linac, _):

        super(Q0Cryomodule, self).__init__(cryoName, linacObject, Q0Cavity)
        self.cavities: Dict[int, Q0Cavity]
        self.dsPressurePVStr = "CPT:CM{CM}:2302:DS:PRESS".format(CM=cryoName)

        jtPrefix = "CLIC:CM{CM}:3001:PVJT".format(CM=cryoName)

        self.jtModePVObject = PV(jtPrefix + ":MODE")
        self.jtManualSelectPVObject = PV(jtPrefix + ":MANUAL")
        self.jtAutoSelectPVObject = PV(jtPrefix + ":AUTO")
        self.dsLiqLevSetpointPVObj = PV(jtPrefix + ":SP_RQST")

        self.jtManPosSetpointPVStr = (jtPrefix + ":MANPOS_RQST")
        self.jtManPosSetpointPVObject = PV(self.jtManPosSetpointPVStr)

        self.jtValveReadbackPVStr = jtPrefix + ":ORBV"
        self.jtValveReadbackPVObject = PV(self.jtValveReadbackPVStr)

        lvlFormatStr = "CLL:CM{CM}:{{INFIX}}:{{LOC}}:LVL".format(CM=cryoName)
        self.dsLevelPVStr = lvlFormatStr.format(INFIX="2301", LOC="DS")
        self.dsLevelPVObject = PV(self.dsLevelPVStr)
        self.usLevelPVStr = lvlFormatStr.format(INFIX="2601", LOC="US")

        self.q0DataSessions = {}
        self.calibDataSessions = {}

        self.heaterDesPVObjects: List[PV] = []
        self.heaterActPVObjects: List[PV] = []

        for q0Cavity in self.cavities.values():
            self.heaterActPVObjects.append(q0Cavity.heaterActPV)
            self.heaterDesPVObjects.append(q0Cavity.heaterDesPV)

        self.heaterDesPVStrings = [pv.pvname for pv in self.heaterDesPVObjects]
        self.heaterActPVStrings = [pv.pvname for pv in self.heaterActPVObjects]
        # self.heaterActPVObjects = list(map(PV, self.heaterActPVs))

        self.valveParams = None

        self._calibIdxFile = ("calibrations/cm{CM}/calibrationsCM{CM}.json"
                              .format(CM=self.name))
        self._q0IdxFile = ("q0Measurements/cm{CM}/q0MeasurementsCM{CM}.json"
                           .format(CM=self.name))

    def addCalibDataSession(self, timeParams: utils.TimeParams,
                            valveParams: utils.ValveParams) -> dataSession.CalibDataSession:

        sessionHash = utils.q0Hash([timeParams, valveParams])

        # Only create a new calibration data session if one doesn't already
        # exist with those exact parameters
        if sessionHash not in self.calibDataSessions:
            cryomodulePVs = utils.CryomodulePVs(valvePV=self.jtValveReadbackPVStr,
                                                dsLevelPV=self.dsLevelPVStr,
                                                usLevelPV=self.usLevelPVStr,
                                                dsPressurePV=self.dsPressurePVStr,
                                                heaterDesPVs=self.heaterDesPVStrings,
                                                heaterActPVs=self.heaterActPVStrings)

            session = dataSession.CalibDataSession(timeParams=timeParams,
                                                   valveParams=valveParams,
                                                   cryomodulePVs=cryomodulePVs,
                                                   cryoModuleName=self.name)
            self.calibDataSessions[sessionHash] = session

        return self.calibDataSessions[sessionHash]

    def addCalibDataSessionFromGUI(self, calibrationSelection: Dict[str, str]) -> dataSession.CalibDataSession:

        startTime = datetime.strptime(calibrationSelection["Start"], "%m/%d/%y %H:%M:%S")
        endTime = datetime.strptime(calibrationSelection["End"], "%m/%d/%y %H:%M:%S")

        try:
            timeInterval = int(calibrationSelection["Archiver Time Interval"])
        except (IndexError, ValueError):
            timeInterval = utils.ARCHIVER_TIME_INTERVAL

        timeParams = utils.TimeParams(startTime=startTime, endTime=endTime,
                                      timeInterval=timeInterval)

        valveParams = utils.ValveParams(refValvePos=float(calibrationSelection["JT Valve Position"]),
                                        refHeatLoadDes=float(calibrationSelection["Reference Heat Load (Des)"]),
                                        refHeatLoadAct=float(calibrationSelection["Reference Heat Load (Act)"]))

        return self.addCalibDataSession(timeParams=timeParams, valveParams=valveParams)

    def addQ0DataSession(self, timeParams: utils.TimeParams,
                         valveParams: utils.ValveParams, refGradVal: float = None,
                         calibSession: dataSession.CalibDataSession = None) -> dataSession.CalibDataSession:

        sessionHash = utils.q0Hash([timeParams, self.name, calibSession, refGradVal])

        # Only create a new calibration data session if one doesn't already
        # exist with those exact parameters
        if sessionHash not in self.q0DataSessions:
            session = self.genQ0DataSession(timeParams, valveParams, refGradVal,
                                            calibSession)
            self.q0DataSessions[sessionHash] = session

        return self.q0DataSessions[sessionHash]

    @property
    def averagedLiquidLevelDS(self) -> float:
        # try to do averaging of the last NUM_LL_POINTS_TO_AVG points to account
        # for signal noise
        try:
            archiverData = utils.getArchiverData(endTime=datetime.now(),
                                                 numPoints=utils.NUM_LL_POINTS_TO_AVG,
                                                 signals=[self.dsLevelPVStr],
                                                 timeInt=utils.ARCHIVER_TIME_INTERVAL)

            return nanmean(archiverData.values[self.dsLevelPVStr])

        # return the most recent value if we can't average for whatever reason
        except AttributeError:
            return self.dsLevelPVObject.value

    @property
    def calibIdxFile(self) -> str:

        if not isfile(self._calibIdxFile):
            with open(self._calibIdxFile, "w+") as f:
                json.dump([], f)

        return self._calibIdxFile

    def fillAndLock(self, desiredLevel=utils.MAX_DS_LL):
        # set the liquid level setpoint to its current readback
        self.dsLiqLevSetpointPVObj.put(self.jtValveReadbackPVObject.value)

        # Allow the JT valve to regulate so that we fill (slowly)
        self.jtAutoSelectPVObject.put(True)

        self.rampLiquidLevel(desiredLevel)
        self.waitForLL()

        # set to manual
        self.jtManualSelectPVObject.put(True)

        if self.jtModePVObject.value != utils.JT_MANUAL_MODE_VALUE:
            raise utils.CryoError("Unable to set JT to manual")

        self.jtManPosSetpointPVObject.put(self.valveParams.refValvePos)
        self.waitForJT(self.valveParams.refValvePos)

    def genQ0DataSession(self, timeParams: utils.TimeParams,
                         valveParams: utils.ValveParams, refGradVal: float = None,
                         calibSession: dataSession.CalibDataSession = None) -> dataSession.Q0DataSession:
        return dataSession.Q0DataSession(self, timeParams, valveParams, refGradVal,
                                         calibSession)

    def getRefValveParams(self, timeRange: float = utils.JT_SEARCH_TIME_RANGE) -> utils.ValveParams:
        """
        searches over the last timeRange hours for a period
        when the liquid level was stable and then fetches an averaged JT valve
        position during that time as well as summed cavity heater DES and ACT
        values. All three numbers get packaged and returned in a utils.ValveParams
        object.
        :param timeRange: float
        :return:
        """

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
                                                 signals=[self.dsLevelPVStr])

            llVals = archiverData.values[self.dsLevelPVStr]

            # Fit a line to the liquid level over the last [numHours] hours
            m, b, _, _, _ = linregress(range(len(llVals)), llVals)

            # If the LL slope is small enough, this may be a good period from
            # which to get a reference valve position & heater params
            if log10(abs(m)) < -5:

                signals = ([self.jtValveReadbackPVStr] + self.heaterDesPVStrings
                           + self.heaterActPVStrings)

                data = utils.getArchiverData(startTime=searchStart,
                                             numPoints=numPoints, signals=signals)
                valveVals = data.values[self.jtValveReadbackPVStr]
                heaterDesVals = [sum(x) for x in zip(*itemgetter(*self.heaterDesPVStrings)(data.values))]
                heaterActVals = [sum(x) for x in zip(*itemgetter(*self.heaterActPVStrings)(data.values))]

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

    def holdAmplitude(self, desiredAmplitudes, minLL=utils.MIN_DS_LL, amplitudeTolerance=0.01):
        # type: (Dict[int, float], float, float) -> datetime

        startTime = datetime.now()

        print("\nStart time: {START}".format(START=startTime))

        utils.writeAndWait(
                "\nWaiting for the LL to drop {DIFF}% or below {MIN}%...".format(
                        MIN=minLL, DIFF=utils.TARGET_LL_DIFF))

        startingLevel = self.averagedLiquidLevelDS
        avgLevel = startingLevel

        prevDiffs = {i: (self.cavities[i].amplitudeActPVObject.value
                         - desiredAmplitudes[i]) for i in desiredAmplitudes.keys()}
        steps = {i: 0.01 for i in desiredAmplitudes.keys()}
        amplitudes = {i: self.cavities[i].amplitudeActPVObject.value
                      for i in desiredAmplitudes.keys()}

        # TODO figure out how to squish this with FE measurements
        while ((startingLevel - avgLevel) < utils.TARGET_LL_DIFF
               and (avgLevel > minLL)):

            for cavity in self.cavities.values():
                if cavity.cavNum not in desiredAmplitudes:
                    continue

                currAmp = cavity.amplitudeActPVObject.value

                amplitudes[cavity.cavNum] = cavity.quenchCheckCallback(amplitudes[cavity.cavNum])
                diff = amplitudes[cavity.cavNum] - desiredAmplitudes[cavity.cavNum]

                mult = 1 if (diff <= 0) else -1

                overshot = ((prevDiffs[cavity.cavNum] >= 0 > diff)
                            or (prevDiffs[cavity.cavNum] <= 0 < diff))

                step = steps[cavity.cavNum]

                # This only works if we're in SEL mode; in pulsed mode the scaling
                # is messed up because a 1% change in the drive doesn't correspond
                # to a 1 MV/m change in the gradient
                if abs(diff) < amplitudeTolerance:
                    pass
                elif (abs(diff) < (2 * step) or overshot) and (step > amplitudeTolerance):
                    step *= 0.5
                else:
                    step *= 1.5

                cavity.amplitudeActPVObject.put(currAmp + (mult * step))

                prevDiffs[cavity.cavNum] = diff

            utils.writeAndWait(".")
            avgLevel = self.averagedLiquidLevelDS

        print("\nEnd Time: {END}".format(END=datetime.now()))
        duration = (datetime.now() - startTime).total_seconds() / 3600
        print("Duration in hours: {DUR}".format(DUR=duration))
        return startTime

    def launchHeaterRun(self, delta: float = utils.CAL_HEATER_DELTA) -> None:

        print("Ramping heaters to the next setting...")

        self.walkHeaters(delta)

        utils.writeAndWait(utils.RUN_STATUS_MSSG)

        startingLevel = self.averagedLiquidLevelDS
        avgLevel = startingLevel

        while ((startingLevel - avgLevel) < utils.TARGET_LL_DIFF and (
                avgLevel > utils.MIN_DS_LL)):
            utils.writeAndWait(".", 10)
            avgLevel = self.averagedLiquidLevelDS

        print("\nDone\n")

    @property
    def totalHeatAct(self) -> float:
        heatAct = 0
        for pv in self.heaterActPVObjects:
            heatAct += pv.value
        return heatAct

    @property
    def totalHeatDes(self) -> float:
        heatDes = 0
        for pv in self.heaterDesPVObjects:
            heatDes += pv.value
        return heatDes

    def rampLiquidLevel(self, desiredLevel: float):
        """
        We'll see if this ends up being necessary later, but this is currently a
        requirement from the cryo group to slowly ramp the setpoint instead of
        just slamming the desired liquid level in at once
        :param desiredLevel: float
        :return:
        """
        utils.writeAndWait("\nWaiting for the liquid level setpoint to be {setpoint}"
                           .format(setpoint=desiredLevel))
        fullDelta = desiredLevel - self.dsLiqLevSetpointPVObj.value
        steps = abs(fullDelta / utils.JT_STEP_SIZE_PER_SECOND)
        stepDelta = fullDelta / steps

        for _ in range(steps):
            self.dsLiqLevSetpointPVObj.put(self.dsLiqLevSetpointPVObj.value
                                           + stepDelta)
            sleep(1)
            utils.writeAndWait(".")

        if self.dsLiqLevSetpointPVObj.value != desiredLevel:
            raise utils.CryoError("Liquid level setpoint was not set")

        utils.writeAndWait(" liquid level setpoint at required value.")

    def takeNewCalibration(self, initialCalibrationHeatload: int):
        """
        Launches a new cryomodule calibration. Expected to take ~4/5 hours
        :param initialCalibrationHeatload: provided as user input in the GUI
                                           measurement settings
        :return:
        """

        if not self.valveParams:
            self.valveParams = self.getRefValveParams()

        deltaTot = self.valveParams.refHeatLoadDes - self.totalHeatDes

        startTime = datetime.now().replace(microsecond=0)

        # Lumping in the initial
        self.walkHeaters((initialCalibrationHeatload + deltaTot) / 8)

        self.fillAndLock()

        self.launchHeaterRun(0)

        for _ in range(utils.NUM_CAL_STEPS):
            if (self.averagedLiquidLevelDS - utils.MIN_DS_LL) < utils.TARGET_LL_DIFF:
                self.fillAndLock()
            self.launchHeaterRun()

        endTime = datetime.now().replace(microsecond=0)

        print("\nStart Time: {START}".format(START=startTime))
        print("End Time: {END}".format(END=datetime.now()))

        duration = (datetime.now() - startTime).total_seconds() / 3600
        print("Duration in hours: {DUR}".format(DUR=duration))

        self.walkHeaters(-((utils.NUM_CAL_STEPS * utils.CAL_HEATER_DELTA) + 1))

        timeParams = utils.TimeParams(startTime, endTime, utils.ARCHIVER_TIME_INTERVAL)

        dataSession = self.addCalibDataSession(timeParams, self.valveParams)

        # Record this calibration dataSession's metadata

        newData = {"Total Reference Heater Setpoint": self.valveParams.refHeatLoadDes,
                   "Total Reference Heater Readback": self.valveParams.refHeatLoadAct,
                   "JT Valve Position"              : self.valveParams.refValvePos,
                   "Start Time"                     : startTime.strftime("%m/%d/%y %H:%M:%S"),
                   "End Time"                       : endTime.strftime("%m/%d/%y %H:%M:%S"),
                   "Archiver Time Interval"         : utils.ARCHIVER_TIME_INTERVAL}

        with open(self.calibIdxFile, 'r+') as f:
            data: List = json.load(f)
            data.append(newData)

            # go to the beginning of the file to overwrite the existing data structure
            f.seek(0)
            json.dump(data, f)
            f.truncate()

        return dataSession, self.valveParams

    def takeNewQ0Measurement(self, desiredAmplitudes: Dict[int, float],
                             calibSession: dataSession.CalibDataSession = None,
                             valveParams: utils.ValveParams = None) -> (dataSession.Q0DataSession, utils.ValveParams):
        try:
            if not valveParams:
                valveParams = self.getRefValveParams()

            deltaTot = utils.ValveParams.refHeatLoadDes - self.totalHeatDes
            self.walkHeaters(deltaTot / 8)

            for cavity in self.cavities.values():
                print("\nRunning up Cavity {CAV}...".format(CAV=cavity.cavNum))

                cavity.checkAcqControl()
                cavity.setPowerStateSSA(True)

                cavity.pulseDriveLevelPV.put(utils.SAFE_PULSED_DRIVE_LEVEL)
                cavity.characterize()

                cavity.rfModePV.put(utils.RF_MODE_PULSE)

                cavity.setStateRF(True)
                cavity.pushGoButton()

                cavity.checkAndSetOnTime()
                cavity.amplitudeDesPVObject.put(2)

                cavity.rfModePV.put(utils.RF_MODE_SELA)

                cavity.walkToAmplitude(desiredAmplitudes[cavity.cavNum])

            self.waitForCryo(valveParams.refValvePos)

            startTime = self.holdAmplitude(desiredAmplitudes).replace(microsecond=0)

            for cavity in self.cavities.values():

                if cavity.cavNum not in desiredAmplitudes:
                    continue

                cavity.walkToAmplitude(5)
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

            for grad in desiredAmplitudes.values():
                desiredGradient += grad

            session = self.addQ0DataSession(utils.TimeParams, utils.ValveParams,
                                            refGradVal=desiredGradient,
                                            calibSession=calibSession)

            desGrads = []
            totGrad = 0
            for i in range(8):
                if (i + 1) in desiredAmplitudes:
                    desGrads.append(desiredAmplitudes[i + 1])
                    totGrad += desiredAmplitudes[i + 1]
                else:
                    desGrads.append(0)

            # with open(self.q0IdxFile, 'a') as f:
            #     csvWriter = writer(f)
            #     csvWriter.writerow(
            #             [self.cryModNumJLAB, utils.ValveParams.refHeatLoadDes,
            #              utils.ValveParams.refHeatLoadAct, utils.ValveParams.refValvePos]
            #             + desGrads + [totGrad, startTime.strftime("%m/%d/%y %H:%M:%S"),
            #                           endTime.strftime("%m/%d/%y %H:%M:%S"),
            #                           utils.ARCHIVER_TIME_INTERVAL])

            print("\nStart Time: {START}".format(START=startTime))
            print("End Time: {END}".format(END=endTime))

            duration = (endTime - startTime).total_seconds() / 3600
            print("Duration in hours: {DUR}".format(DUR=duration))

            return session, utils.ValveParams

        except(CalledProcessError, IndexError, OSError, ValueError,
               AssertionError, KeyboardInterrupt) as e:
            utils.writeAndFlushStdErr(
                    "Procedure failed with error:\n{E}\n".format(E=e))
            for cavity in self.cavities.values():
                cavity.powerDown()

    def waitForCryo(self, refValvePos):
        # type: (float) -> None
        self.waitForLL()
        self.waitForJT(refValvePos)

    def waitForJT(self, refValvePos):
        # type: (float) -> None

        utils.writeAndWait("\nWaiting for JT Valve to be in manual and locked at {POS}..."
                           .format(POS=refValvePos))

        # One way for the JT valve to be locked in the correct position is for
        # it to be in manual mode and at the desired value
        while self.jtModePVObject.value != utils.JT_MANUAL_MODE_VALUE:
            utils.writeAndWait(".", 5)

        while self.jtManPosSetpointPVObject.value != refValvePos:
            utils.writeAndWait(".", 5)

        # Wait for the valve position to be within tolerance before continuing
        while abs(self.jtValveReadbackPVObject.value - refValvePos) > utils.VALVE_POS_TOL:
            utils.writeAndWait(".", 5)

        utils.writeAndWait(" JT Valve locked.\n")

    # We consider the cryo situation to be good when the liquid level is high
    # enough and the JT valve is locked in the correct position

    def waitForLL(self, desiredLiquidLevel=utils.MAX_DS_LL):
        utils.writeAndWait("\nWaiting for downstream liquid level to be {LL}%..."
                           .format(LL=desiredLiquidLevel))

        while (desiredLiquidLevel - self.averagedLiquidLevelDS) > 0:
            utils.writeAndWait(".", 5)

        utils.writeAndWait(" downstream liquid level at required value.")

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


Q0_LINAC_OBJECTS: List[Linac] = []
Q0_CRYOMODULES: Dict[str, Q0Cryomodule] = {}
for name, cryomoduleList in LINAC_TUPLES:
    linacObject = Linac(name, cryomoduleList, cavityClass=Q0Cavity, cryomoduleClass=Q0Cryomodule)
    Q0_LINAC_OBJECTS.append(linacObject)
    Q0_CRYOMODULES.update(linacObject.cryomodules)
