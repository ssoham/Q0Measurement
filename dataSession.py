import json
from abc import ABCMeta, abstractmethod
from datetime import datetime
from decimal import Decimal
from json import dumps, load
from operator import itemgetter
from pathlib import Path
from typing import List, Optional

from matplotlib import pyplot as plt
from numpy import polyfit
from scipy.signal import medfilt

from dataRun import DataRun, HeaterDataRun
from utils import (CryomodulePVs, NUM_LL_POINTS_TO_AVG, TimeParams, ValveParams,
                   compatibleMkdirs, genAxis, getArchiverData)


class DataSession(object):
    __metaclass__ = ABCMeta

    def __init__(self, timeParams: TimeParams, valveParams: ValveParams,
                 cryoModuleName: str, cryomodulePVs: CryomodulePVs):

        self.cryoModuleName = cryoModuleName

        self.dataRuns: List[DataRun] = []
        self.heaterRunIdxs = []
        self.rfRunIdxs = []

        self._dataFileName: Optional[Path] = None
        self._numPoints = None

        self.timeParams = timeParams
        self.valveParams = valveParams

        self.unixTimeBuff = None
        self.timestampBuffer = None
        self.valvePercentageBuffer = None
        self.downstreamLiquidLevelBuffer = None
        self.totalGradientBuffer = None
        self.dsPressBuff = None
        self.totalHeaterSetpointBuffer = None
        self.totalHeaterReadbackBuffer = None

        # The plot of the raw downstream liquid level data
        self.liquidVsTimeAxis = None
        self.cryomodulePVs = cryomodulePVs

    def __str__(self):
        return ("{START} to {END} ({RATE}s sample interval)"
                .format(START=self.timeParams.startTime,
                        END=self.timeParams.endTime,
                        RATE=self.timeParams.timeInterval))

    @property
    @abstractmethod
    def calibSlope(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def heatAdjustment(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def filePath(self) -> Path:
        raise NotImplementedError

    @property
    @abstractmethod
    def fileNameFormatter(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def adjustedRunSlopes(self):
        raise NotImplementedError

    # Takes the data in an session's buffers and slices it into data "runs"
    # based on cavity heater settings
    @abstractmethod
    def populateRuns(self):
        raise NotImplementedError

    # Iterates over this session's data runs, plots them, and fits trend lines
    # to them
    @abstractmethod
    def processRuns(self):
        raise NotImplementedError

    # Takes three related arrays, plots them, and fits some trend lines
    @abstractmethod
    def plotAndFitData(self):
        raise NotImplementedError

    @abstractmethod
    def getTotalHeatDelta(self, startIdx, currIdx):
        raise NotImplementedError

    @abstractmethod
    def printSessionReport(self):
        raise NotImplementedError

    @property
    def runElecHeatLoads(self):
        # type: () -> List[float]
        return [self.dataRuns[runIdx].elecHeatLoadAct for runIdx
                in self.heaterRunIdxs]

    @property
    def numPoints(self):
        # type: () -> int
        if not self._numPoints:
            self._numPoints = int((self.timeParams.endTime
                                   - self.timeParams.startTime).total_seconds()
                                  / self.timeParams.timeInterval)
        return self._numPoints

    # Generates a JSON data file (with the raw data from this data session) if
    # one doesn't already exist
    def generateDataFile(self) -> Optional[Path]:

        print(self.filePath)

        if self.filePath.is_file():
            return self.filePath

        pvs = self.cryomodulePVs.asList()

        try:
            data = getArchiverData(self.timeParams.startTime, self.numPoints,
                                   pvs, self.timeParams.timeInterval)

        except TypeError:
            raise AssertionError("Data not retrieved from Archiver")

        # We're collapsing the readback for each cavity's desired and actual
        # electric heat load into two sum columns (instead of 16 individual
        # columns)
        compatibleMkdirs(self.filePath)

        # itemgetter searches the data.values dictionary for entries that match
        # keys in cryomodulePVs.heaterDesPVs and returns the values as a tuple
        # of lists. Then we call zip to collapse those into a list of tuples,
        # after which we just sum up all the values in each tuple so that we
        # end up with a list of ints
        heatLoadDes = [sum(x) for x in zip(*itemgetter(*self.cryomodulePVs.heaterDesPVs)(data.values))]
        heatLoadAct = [sum(x) for x in zip(*itemgetter(*self.cryomodulePVs.heaterActPVs)(data.values))]
        gradient = ([sum(x) for x in zip(*itemgetter(*self.cryomodulePVs.gradPVs)(data.values))]
                    if self.cryomodulePVs.gradPVs else None)

        dataDict = {"Total Heater Setpoint": heatLoadDes,
                    "Total Heater Readback": heatLoadAct,
                    "Whole Module Gradient": gradient,
                    "Timestamps": data.timeStamps}

        # Add all the other PVs to the output because why not
        jsonData = dumps(dataDict.update(data.values))

        # open file for writing, "w"
        with open(self.filePath, "w") as f:
            json.dump(jsonData, f)

        return self.filePath

    def processData(self):
        # type: () -> None

        self.parseDataFromJSON()
        self.downstreamLiquidLevelBuffer = medfilt(self.downstreamLiquidLevelBuffer,
                                                   NUM_LL_POINTS_TO_AVG)
        self.populateRuns()

        if not self.dataRuns:
            print("{name} has no runs to process and plot."
                  .format(name=self.cryoModuleName))
            return

        self.adjustForSettle()
        self.processRuns()
        self.plotAndFitData()

    # parses JSON data to populate the given session's data buffers
    def parseDataFromJSON(self):
        # type: () -> None

        dataFile = open(self.filePath)
        jsonData = load(dataFile)

        # jsonData = dumps({"Total Heater Setpoint": heatLoadDes,
        #                   "Total Heater Readback": heatLoadAct,
        #                   "Whole Module Gradient": gradient,
        #                   "Timestamps": data.timeStamps}.update(data.values))

        dataFile.close()

        self.timestampBuffer = jsonData["Timestamps"]

        timeZero = datetime.utcfromtimestamp(0)
        self.unixTimeBuff = list(map(lambda dt: (dt - timeZero).total_seconds(),
                                     self.timestampBuffer))

        self.totalHeaterSetpointBuffer = jsonData["Total Heater Setpoint"]
        self.totalHeaterReadbackBuffer = jsonData["Total Heater Readback"]
        self.totalGradientBuffer = jsonData["Whole Module Gradient"]

        self.valvePercentageBuffer = jsonData[self.cryomodulePVs.valvePV]
        self.downstreamLiquidLevelBuffer = jsonData[self.cryomodulePVs.dsLevelPV]

    ############################################################################
    # adjustForSettle cuts off data that's corrupted because the heat load on
    # the 2 K helium bath is changing. (When the cavity heater settings or the
    # RF gradients change, it takes time for that change to become visible to
    # the helium because there are intermediate structures with heat capacity.)
    ############################################################################
    def adjustForSettle(self):
        # type: () -> None

        for i, run in enumerate(self.dataRuns):

            # Calculate the number of data points to be chopped off the
            # beginning of the data run based on the expected change in the
            # cryomodule heat load. The scale factor is derived from the
            # assumption that a 1 W change in the heat load leads to about 25
            # useless seconds (and that this scales linearly with the change in
            # heat load, which isn't really true). We already wait 30 s after
            # walking the heaters, so that's subtracted out
            # noinspection PyTypeChecker
            # cutoff = int(totalHeatDelta * 25) - 30
            cutoff = 0
            cutoff = cutoff if cutoff >= 0 else 0

            run.diagnostics["Cutoff"] = cutoff

            idx = self.dataRuns[i].startIdx
            startTime = self.unixTimeBuff[idx]
            duration = 0

            while duration < cutoff:
                idx += 1
                duration = self.unixTimeBuff[idx] - startTime

            self.dataRuns[i].startIdx = idx

    def _isEndOfCalibRun(self, idx, elecHeatLoadDes):
        # type: (int, float) -> bool
        # Find inflection points for the desired heater setting
        prevElecHeatLoadDes = (self.totalHeaterSetpointBuffer[idx - 1]
                               if idx > 0 else elecHeatLoadDes)

        heaterChanged = (elecHeatLoadDes != prevElecHeatLoadDes)
        liqLevelTooLow = (self.downstreamLiquidLevelBuffer[idx] < MIN_DS_LL)
        valveOutsideTol = (abs(self.valvePercentageBuffer[idx]
                               - self.valveParams.refValvePos)
                           > VALVE_POS_TOL)
        isLastElement = (idx == len(self.totalHeaterSetpointBuffer) - 1)

        heatersOutsideTol = (abs(elecHeatLoadDes - self.totalHeaterReadbackBuffer[idx])
                             >= HEATER_TOL)

        # A "break" condition defining the end of a run if the desired heater
        # value changed, or if the upstream liquid level dipped below the
        # minimum, or if the valve position moved outside the tolerance, or if
        # we reached the end (which is a kinda jank way of "flushing" the last
        # run)
        return (heaterChanged or liqLevelTooLow or valveOutsideTol
                or isLastElement or heatersOutsideTol)

    def _checkAndFlushRun(self, isEndOfRun, idx, runStartIdx):
        # type: (bool, int, int) -> int
        if isEndOfRun:
            runDuration = (self.unixTimeBuff[idx]
                           - self.unixTimeBuff[runStartIdx])

            if runDuration >= MIN_RUN_DURATION:
                self._addRun(runStartIdx, idx - 1)

            return idx

        return runStartIdx

    @abstractmethod
    def _addRun(self, startIdx, endIdx):
        # type: (int, int) -> None
        raise NotImplementedError


class CalibDataSession(DataSession):

    def __init__(self, timeParams: TimeParams, valveParams: ValveParams,
                 cryomodulePVs: CryomodulePVs, cryoModuleName: str):

        super(CalibDataSession, self).__init__(timeParams, valveParams,
                                               cryoModuleName=cryoModuleName,
                                               cryomodulePVs=cryomodulePVs)

        self.dataRuns: List[HeaterDataRun] = []

        self._calibSlope = None

        # If we choose the JT valve position correctly, the calibration curve
        # should intersect the origin (0 heat load should translate to 0
        # dLL/dt). The heat adjustment will be equal to the negative x
        # intercept.
        self._heatAdjustment = None

        # the dLL/dt vs heat load plot with trend line (back-calculated points
        # for cavity Q0 sessions are added later)
        self.heaterCalibAxis = None

        self.generateDataFile()
        self.processData()

    @property
    def calibSlope(self):
        # type: () -> float
        return self._calibSlope

    @property
    def heatAdjustment(self):
        # type: () -> float
        return self._heatAdjustment

    # returns a list of electric heat loads corrected with self.heatAdjustment
    @property
    def runElecHeatLoadsAdjusted(self):
        # type: () -> List[float]
        # noinspection PyUnresolvedReferences
        return [self.dataRuns[runIdx].elecHeatLoadActAdjusted for runIdx
                in self.heaterRunIdxs]

    @property
    def fileNameFormatter(self):
        # type: () -> str
        # e.g. calib_CM12_2019-02-25--11-25_18672.csv
        return "../data/calib/cm{CM}/calib_{cryoMod}{suff}"

    @property
    def adjustedRunSlopes(self):
        # type: () -> List[float]
        return [self.dataRuns[runIdx].slope for runIdx in self.heaterRunIdxs]

    @property
    def filePath(self):
        # type: () -> Path
        if not self._dataFileName:
            # Define a file name for the CSV we're saving. There are calibration
            # files and q0 measurement files. Both include a time stamp in the
            # format year-month-day--hour-minute. They also indicate the number
            # of data points.
            suffixStr = "{start}{nPoints}.json"
            suffix = suffixStr.format(
                    start=self.timeParams.startTime.strftime("_%Y-%m-%d--%H-%M_"),
                    nPoints=self.numPoints)
            cryoModStr = "CM{cryMod}".format(
                    cryMod=self.container.cryModNumSLAC)

            fileName = self.fileNameFormatter.format(cryoMod=cryoModStr,
                                                     suff=suffix,
                                                     CM=self.container.cryModNumSLAC)
            self._dataFileName = Path(fileName)

        return self._dataFileName

    def populateRuns(self):
        # type: () -> None
        runStartIdx = 0

        for idx, elecHeatLoad in enumerate(self.totalHeaterSetpointBuffer):
            runStartIdx = self._checkAndFlushRun(
                    self._isEndOfCalibRun(idx, elecHeatLoad), idx, runStartIdx)

    def _addRun(self, startIdx, endIdx):
        # type: (int, int) -> None
        runIdx = len(self.dataRuns)
        runNum = runIdx + 1

        self.dataRuns.append(HeaterDataRun(startIdx, endIdx, self, runNum))
        self.heaterRunIdxs.append(runIdx)

    # noinspection PyTupleAssignmentBalance
    def processRuns(self):
        # type: () -> None
        for run in self.dataRuns:
            run.process()

        # We're dealing with a cryomodule here so we need to calculate the
        # fit for the heater calibration curve.
        self._calibSlope, yIntercept = polyfit(self.runElecHeatLoads,
                                               self.adjustedRunSlopes, 1)

        xIntercept = -yIntercept / self._calibSlope

        self._heatAdjustment = -xIntercept

    def plotAndFitData(self):
        # type: () -> None
        # TODO improve plots with human-readable time axis

        plt.rcParams.update({'legend.fontsize': 'small'})

        suffix = " ({name} Heater Calibration)".format(name=self.cryoModuleName)

        self.liquidVsTimeAxis = genAxis("Liquid Level vs. Time" + suffix,
                                        "Unix Time (s)",
                                        "Downstream Liquid Level (%)")

        for run in self.dataRuns:
            # First we plot the actual run data
            self.liquidVsTimeAxis.plot(run.timeStamps, run.data,
                                       label=run.label)

            # Then we plot the linear fit to the run data
            self.liquidVsTimeAxis.plot(run.timeStamps, [run.slope * x
                                                        + run.intercept
                                                        for x
                                                        in run.timeStamps])

        self.liquidVsTimeAxis.legend(loc='best')
        self.heaterCalibAxis = genAxis("Liquid Level Rate of Change vs."
                                       " Heat Load", "Heat Load (W)",
                                       "dLL/dt (%/s)")

        self.heaterCalibAxis.plot(self.runElecHeatLoadsAdjusted,
                                  self.adjustedRunSlopes,
                                  marker="o", linestyle="None",
                                  label="Heater Calibration Data")

        slopeStr = '{:.2e}'.format(Decimal(self._calibSlope))
        labelStr = "Calibration Fit:  {slope} %/(s*W)".format(slope=slopeStr)

        self.heaterCalibAxis.plot(self.runElecHeatLoadsAdjusted,
                                  [self._calibSlope * x
                                   for x in self.runElecHeatLoadsAdjusted],
                                  label=labelStr)

        self.heaterCalibAxis.legend(loc='best')

    def getTotalHeatDelta(self, startIdx, currIdx):
        # type: (int, int) -> float
        if currIdx == 0:
            return self.totalHeaterSetpointBuffer[startIdx] - self.valveParams.refHeatLoadDes

        else:

            prevStartIdx = self.dataRuns[currIdx - 1].startIdx

            elecHeatDelta = (self.totalHeaterSetpointBuffer[startIdx]
                             - self.totalHeaterSetpointBuffer[prevStartIdx])

            return abs(elecHeatDelta)

    def printSessionReport(self):
        # type: () -> None

        print("\n-------------------------------------")
        print("---------------- {CM} ----------------"
              .format(CM=self.container.name))
        print("-------------------------------------\n")

        for run in self.dataRuns:
            run.printRunReport()

        print("Calibration curve intercept adjust = {ADJUST} W\n"
              .format(ADJUST=round(self.heatAdjustment, 4)))


class Q0DataSession(DataSession):

    def __init__(self, timeParams: TimeParams, valveParams: ValveParams,
                 cryoModuleNumber: str, valvePV: str,
                 dsLevelPV: str, dsPressurePV: str, refGradVal: float):

        super(Q0DataSession, self).__init__(timeParams, valveParams,
                                            cryoModuleNumber)

        # self.container._desiredGrads = refGradVal
        self._pvBuffMap = {valvePV     : self.valvePercentageBuffer,
                           dsLevelPV   : self.downstreamLiquidLevelBuffer,
                           # self.container.gradPV: self.gradBuff,
                           dsPressurePV: self.dsPressBuff}

        self.refGradVal = refGradVal

        self.generateDataFile()
        self.processData()

    @property
    def calibSlope(self):
        # type: () -> float
        return self.calibSession.calibSlope

    @property
    def heatAdjustment(self):
        # type: () -> float
        return self.calibSession.heatAdjustment

    @property
    def fileNameFormatter(self):
        # type: () -> str
        return "../data/q0meas/cm{CM}/q0meas_{cryoMod}_cav{cavityNum}{suff}"

    # For Q0 data sessions we use the heater run(s) to calculate the heat
    # adjustment we should apply to the calculated RF heat load before
    # turning that into a Q0 value
    @property
    def avgHeatAdjustment(self):
        # type: () -> float
        adjustments = []

        for runIdx in self.heaterRunIdxs:
            # noinspection PyUnresolvedReferences
            runAdjustment = self.dataRuns[runIdx].heatAdjustment
            if runAdjustment:
                adjustments.append(runAdjustment)

        return mean(adjustments) if adjustments else 0

    # y = (m * x) + b, where y is the dLL/dt, x is the adjusted RF heat load,
    # and b is the y intercept for the calibration curve (which we normalized
    # to be 0). This is used when overlaying the RF run slopes on the
    # calibration curve.
    # noinspection PyUnresolvedReferences
    @property
    def adjustedRunSlopes(self):
        # type: () -> List[float]
        m = self.calibSession.calibSlope
        return [(m * self.dataRuns[runIdx].rfHeatLoad) for runIdx
                in self.rfRunIdxs]

    # noinspection PyUnresolvedReferences
    @property
    def adjustedRunHeatLoadsRF(self):
        # type: () -> List[float]
        return [self.dataRuns[runIdx].rfHeatLoad for runIdx
                in self.rfRunIdxs]

    @property
    def filePath(self):
        # type: () -> str
        if not self._dataFileName:
            suffixStr = "{start}{nPoints}.json"
            suffix = suffixStr.format(
                    start=self.timeParams.startTime.strftime("_%Y-%m-%d--%H-%M_"),
                    nPoints=self.numPoints)
            cryoModStr = "CM{cryMod}".format(
                    cryMod=self.container.cryModNumSLAC)

            self._dataFileName = self.fileNameFormatter.format(
                    cryoMod=cryoModStr, suff=suffix,
                    cavityNum=self.container.cavNum,
                    CM=self.container.cryModNumSLAC)

        return self._dataFileName

    def _addRun(self, startIdx, endIdx):
        # type: (int, int) -> None
        runIdx = len(self.dataRuns)
        runNum = runIdx + 1

        isHeaterRun = (self.totalHeaterSetpointBuffer[startIdx] - self.valveParams.refHeatLoadDes) != 0
        if isHeaterRun:
            self.dataRuns.append(HeaterDataRun(startIdx, endIdx, self, runNum))
            self.heaterRunIdxs.append(runIdx)
        else:
            # noinspection PyTypeChecker
            self.dataRuns.append(RFDataRun(startIdx, endIdx, self, runNum))
            self.rfRunIdxs.append(runIdx)

    def populateRuns(self):
        # type: () -> None

        runStartIdx = 0

        for idx, elecHeatLoad in enumerate(self.totalHeaterSetpointBuffer):

            try:
                gradChanged = (abs(self.totalGradientBuffer[idx] - self.totalGradientBuffer[idx - 1])
                               > self.container.gradTol) if idx != 0 else False
            except TypeError:
                gradChanged = False

            isEndOfQ0Run = (self._isEndOfCalibRun(idx, elecHeatLoad)
                            or gradChanged)

            runStartIdx = self._checkAndFlushRun(isEndOfQ0Run, idx, runStartIdx)

    def processRuns(self):
        # type: () -> None
        for run in self.dataRuns:
            run.process()

    def plotAndFitData(self):
        # type: () -> None
        # TODO improve plots with human-readable time axis

        plt.rcParams.update({'legend.fontsize': 'small'})

        suffix = " ({name})".format(name=self.container.name)

        self.liquidVsTimeAxis = genAxis("Liquid Level vs. Time" + suffix,
                                        "Unix Time (s)",
                                        "Downstream Liquid Level (%)")

        for run in self.dataRuns:
            # First we plot the actual run data
            self.liquidVsTimeAxis.plot(run.timeStamps, run.data,
                                       label=run.label)

            # Then we plot the linear fit to the run data
            self.liquidVsTimeAxis.plot(run.timeStamps, [(run.slope * x)
                                                        + run.intercept
                                                        for x
                                                        in run.timeStamps])

        self.liquidVsTimeAxis.legend(loc='best')

    def getTotalHeatDelta(self, startIdx, currIdx):
        # type: (int, int) -> float
        if currIdx == 0:
            totalHeatDelta = (self.totalHeaterSetpointBuffer[startIdx] - self.valveParams.refHeatLoadDes)
            totalHeatDelta += self.approxHeatFromGrad(self.totalGradientBuffer[startIdx])
            return totalHeatDelta

        else:

            prevStartIdx = self.dataRuns[currIdx - 1].startIdx

            elecHeatDelta = (self.totalHeaterSetpointBuffer[startIdx]
                             - self.totalHeaterSetpointBuffer[prevStartIdx])

            currGrad = self.totalGradientBuffer[startIdx]
            currGradHeatLoad = self.approxHeatFromGrad(currGrad)

            prevGrad = self.totalGradientBuffer[prevStartIdx]
            prevGradHeatLoad = self.approxHeatFromGrad(prevGrad)

            gradHeatDelta = currGradHeatLoad - prevGradHeatLoad
            return abs(elecHeatDelta + gradHeatDelta)

    # Approximates the expected heat load on a cavity from its RF gradient. A
    # cavity with the design Q of 2.7E10 should produce about 9.6 W of heat with
    # a gradient of 16 MV/m. The heat scales quadratically with the gradient. We
    # don't know the correct Q yet when we call this function so we assume the
    # design values.
    @staticmethod
    def approxHeatFromGrad(grad):
        # type: (float) -> float
        # Gradients < 0 are non-physical so assume no heat load in that case.
        # The gradient values we're working with are readbacks from cavity
        # gradient PVs so it's possible that they could go negative.
        return ((grad / 16) ** 2) * 9.6 if grad > 0 else 0

    def updateOutput(self):
        self.printSessionReport()
        self.updateCalibCurve()

    def printSessionReport(self):
        # type: () -> None

        print("\n--------------------------------------")
        name = self.container.name if isinstance(self.container, Cryomodule) else self.container.parent.name
        print("------------ {CM} {CAV} ------------"
              .format(CM=name, CAV=self.container.name))
        print("--------------------------------------\n")

        for run in self.dataRuns:
            run.printRunReport()

    def updateCalibCurve(self):
        # type: () -> None

        calibSession = self.calibSession
        calibCurveAxis = calibSession.heaterCalibAxis

        calibCurveAxis.plot(self.adjustedRunHeatLoadsRF,
                            self.adjustedRunSlopes,
                            marker="o", linestyle="None",
                            label="Projected Data for " + self.container.name)

        calibCurveAxis.legend(loc='best', shadow=True, numpoints=1)

        # The rest of this mess is pretty much just extending the fit line to
        # include outliers
        minCavHeatLoad = min(self.adjustedRunHeatLoadsRF)
        minCalibHeatLoad = min(calibSession.runElecHeatLoadsAdjusted)

        if minCavHeatLoad < minCalibHeatLoad:
            yRange = linspace(minCavHeatLoad, minCalibHeatLoad)
            calibCurveAxis.plot(yRange, [calibSession.calibSlope * i
                                         for i in yRange])

        maxCavHeatLoad = max(self.adjustedRunHeatLoadsRF)
        maxCalibHeatLoad = max(calibSession.runElecHeatLoadsAdjusted)

        if maxCavHeatLoad > maxCalibHeatLoad:
            yRange = linspace(maxCalibHeatLoad, maxCavHeatLoad)
            calibCurveAxis.plot(yRange, [calibSession.calibSlope * i
                                         for i in yRange])
