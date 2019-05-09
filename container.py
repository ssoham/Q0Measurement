################################################################################
# Utility classes to hold calibration data for a cryomodule, and Q0 measurement
# data for each of that cryomodule's cavities
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import print_function, division
from csv import writer, reader
from copy import deepcopy
from decimal import Decimal
from os.path import isfile
from sys import stderr
from matplotlib.axes import Axes
from numpy import mean, exp, log10
from scipy.stats import linregress
from numpy import polyfit
from matplotlib import pyplot as plt
from subprocess import check_output, CalledProcessError
from re import compile, findall
from collections import OrderedDict
from datetime import datetime, timedelta
from abc import abstractproperty, ABCMeta, abstractmethod
from utils import (writeAndFlushStdErr, MYSAMPLER_TIME_INTERVAL, TEST_MODE,
                   VALVE_POSITION_TOLERANCE, UPSTREAM_LL_LOWER_LIMIT,
                   HEATER_TOLERANCE, GRAD_TOLERANCE, MIN_RUN_DURATION, getYesNo,
                   get_float_lim, writeAndWait, MAX_DS_LL, cagetPV, caputPV)


class Container(object):
    __metaclass__ = ABCMeta

    def __init__(self, cryModNumSLAC, cryModNumJLAB):
        self.cryModNumSLAC = cryModNumSLAC
        self.cryModNumJLAB = cryModNumJLAB
        self.name = None

        self.name = self.addNumToStr("CM{CM}")

        self.dsPressurePV = self.addNumToStr("CPT:CM0{CM}:2302:DS:PRESS")
        self.jtModePV = self.addNumToStr("CPV:CM0{CM}:3001:JT:MODE")
        self.jtPosSetpointPV = self.addNumToStr("CPV:CM0{CM}:3001:JT:POS_SETPT")

        lvlFormatStr = self.addNumToStr("CLL:CM0{CM}:{{INFIX}}:{{LOC}}:LVL")

        self.dsLevelPV = lvlFormatStr.format(INFIX="2301", LOC="DS")
        self.usLevelPV = lvlFormatStr.format(INFIX="2601", LOC="US")

        self.cvMaxPV = self.addNumToStr("CPID:CM0{CM}:3001:JT:CV_{SUFF}", "MAX")
        self.cvMinPV = self.addNumToStr("CPID:CM0{CM}:3001:JT:CV_{SUFF}", "MIN")
        self.valvePV = self.addNumToStr("CPID:CM0{CM}:3001:JT:CV_{SUFF}",
                                        "VALUE")

        self.dataSessions = {}

    @abstractmethod
    def walkHeaters(self, perHeaterDelta):
        raise NotImplementedError

    @abstractproperty
    def idxFile(self):
        raise NotImplementedError

    @abstractproperty
    def heaterDesPVs(self):
        raise NotImplementedError

    @abstractproperty
    def heaterActPVs(self):
        raise NotImplementedError

    @staticmethod
    def makeTimeFromStr(row, idx):
        return datetime.strptime(row[idx], "%m/%d/%y %H:%M")

    @staticmethod
    def getTimeParams(row, indices):
        startTime = Container.makeTimeFromStr(row, indices["startIdx"])
        endTime = Container.makeTimeFromStr(row, indices["endIdx"])

        timeIntervalStr = row[indices["timeIntIdx"]]
        timeInterval = (int(timeIntervalStr) if timeIntervalStr
                        else MYSAMPLER_TIME_INTERVAL)

        return startTime, endTime, timeInterval

    def getRefValvePos(self, numHours, checkForFlatness=True):
        # type: (float, bool) -> float

        getNewPos = getYesNo("Determine new JT Valve Position? (May take 2 "
                             "hours) ")

        if not getNewPos:
            desPos = get_float_lim("Desired JT Valve Position: ", 0, 100)
            print("\nDesired JT Valve position is {POS}".format(POS=desPos))
            return desPos

        print("\nDetermining Required JT Valve Position...")

        start = datetime.now() - timedelta(hours=numHours)
        numPoints = int((60 / MYSAMPLER_TIME_INTERVAL) * (numHours * 60))
        signals = [self.dsLevelPV, self.valvePV]

        csvReader = DataSession.parseRawData(start, numPoints, signals)

        csvReader.next()
        valveVals = []
        llVals = []

        for row in csvReader:
            try:
                valveVals.append(float(row.pop()))
                llVals.append(float(row.pop()))
            except ValueError:
                pass

        # Fit a line to the liquid level over the last [numHours] hours
        m, b, _, _, _ = linregress(range(len(llVals)), llVals)

        # If the LL slope is small enough, return the average JT valve position
        # over the requested time span
        if not checkForFlatness or (checkForFlatness and log10(abs(m)) < 5):
            desPos = round(mean(valveVals), 1)
            # waitForLL(container)
            # waitForJT(container, desPos)
            print("\nDesired JT Valve position is {POS}".format(POS=desPos))
            return desPos

        # If the LL slope isn't small enough, wait for it to stabilize and then
        # repeat this process (and assume that it's flat enough at that point)
        else:
            print("Need to figure out new JT valve position")

            self.waitForLL()

            writeAndWait("\nWaiting 1 hour 45 minutes for LL to stabilize...")

            start = datetime.now()
            while (datetime.now() - start).total_seconds() < 6300:
                writeAndWait(".", 5)

            return self.getRefValvePos(0.25, False)

    def waitForCryo(self, desPos):
        # type: (float) -> None
        self.waitForLL()
        self.waitForJT(desPos)

    def waitForLL(self):
        # type: () -> None
        writeAndWait("\nWaiting for downstream liquid level to be {LL}%..."
                     .format(LL=MAX_DS_LL))

        while abs(MAX_DS_LL - float(cagetPV(self.dsLevelPV))) > 1:
            writeAndWait(".", 5)

        print("\ndownstream liquid level at required value")

    def waitForJT(self, desPosJT):
        # type: (float) -> None

        writeAndWait("\nWaiting for JT Valve to be locked at {POS}..."
                     .format(POS=desPosJT))

        mode = cagetPV(self.jtModePV)

        if mode == "0":
            while float(cagetPV(self.jtPosSetpointPV)) != desPosJT:
                writeAndWait(".", 5)

        else:

            while float(cagetPV(self.cvMinPV)) != desPosJT:
                writeAndWait(".", 5)

            while float(cagetPV(self.cvMaxPV)) != desPosJT:
                writeAndWait(".", 5)

        print("\nJT Valve locked")

    def addDataSessionFromRow(self, row, indices, calibSession=None,
                              refGradVal=None):
        # type: ([], dict, DataSession, float) -> DataSession

        startTime, endTime, timeInterval = Container.getTimeParams(row, indices)

        refHeatLoad = float(row[indices["refHeatIdx"]])

        return self.addDataSession(startTime, endTime, timeInterval,
                                   float(row[indices["jtIdx"]]), refHeatLoad)

    def addNumToStr(self, formatStr, suffix=None):
        if suffix:
            return formatStr.format(CM=self.cryModNumJLAB, SUFF=suffix)
        else:
            return formatStr.format(CM=self.cryModNumJLAB)

    def addDataSession(self, startTime, endTime, timeInt, refValvePos,
                       refHeatLoad=None, refGradVal=None):
        # type: (datetime, datetime, int, float, float, None) -> DataSession

        if not refHeatLoad:
            refHeatLoad = 0
            for heaterActPV in self.heaterActPVs:
                refHeatLoad += float(cagetPV(heaterActPV))

        session = DataSession(self, startTime, endTime, timeInt, refValvePos,
                              refHeatLoad)
        self.dataSessions[hash(session)] = session
        return session

    # Returns a list of the PVs used for its data acquisition, including
    # the PV of the cavity heater used for calibration
    def getPVs(self):
        return ([self.valvePV, self.dsLevelPV, self.usLevelPV]
                + self.heaterDesPVs + self.heaterActPVs)


class Cryomodule(Container):

    def __init__(self, cryModNumSLAC, cryModNumJLAB):
        super(Cryomodule, self).__init__(cryModNumSLAC, cryModNumJLAB)

        self.name = self.addNumToStr("CM{CM}")

        # Give each cryomodule 8 cavities
        cavities = {}

        self._heaterDesPVs = []
        self._heaterActPVs = []

        heaterDesStr = self.addNumToStr("CHTR:CM0{CM}:1{{CAV}}55:HV:{SUFF}",
                                        "POWER_SETPT")
        heaterActStr = self.addNumToStr("CHTR:CM0{CM}:1{{CAV}}55:HV:{SUFF}",
                                        "POWER")

        for i in range(1, 9):
            cavities[i] = Cavity(cryMod=self, cavNumber=i)
            self._heaterDesPVs.append(heaterDesStr.format(CAV=i))
            self._heaterActPVs.append(heaterActStr.format(CAV=i))

        self.cavities = OrderedDict(sorted(cavities.items()))

    def walkHeaters(self, perHeaterDelta):
        # type: (int) -> None

        # negative if we're decrementing heat
        step = 1 if perHeaterDelta > 0 else -1

        for _ in range(abs(perHeaterDelta)):
            for heaterSetpointPV in self.heaterDesPVs:
                currVal = float(cagetPV(heaterSetpointPV))
                caputPV(heaterSetpointPV, str(currVal + step))
                writeAndWait("\nWaiting 30s for cryo to stabilize...\n", 30)

    @property
    def idxFile(self):
        return "calibrationsCM{CM}.csv".format(CM=self.cryModNumSLAC)

    @property
    def heaterDesPVs(self):
        return self._heaterDesPVs

    @property
    def heaterActPVs(self):
        return self._heaterActPVs


class Cavity(Container):
    def __init__(self, cryMod, cavNumber):
        # type: (Cryomodule, int) -> None

        super(Cavity, self).__init__(cryMod.cryModNumSLAC,
                                     cryMod.cryModNumJLAB)
        self.parent = cryMod

        self.name = "Cavity {cavNum}".format(cavNum=cavNumber)
        self.cavNum = cavNumber
        self.delta = 0

    def walkHeaters(self, perHeaterDelta):
        return self.parent.walkHeaters(perHeaterDelta)

    @property
    def idxFile(self):
        return "q0MeasurementsCM{CM}.csv".format(CM=self.parent.cryModNumSLAC)

    @property
    def heaterDesPVs(self):
        return self.parent.heaterDesPVs

    @property
    def heaterActPVs(self):
        return self.parent.heaterActPVs

    def addDataSessionFromRow(self, row, indices, calibSession=None,
                              refGradVal=None):
        # type: ([], dict, DataSession, float) -> Q0DataSession

        startTime, endTime, timeInterval = Container.getTimeParams(row, indices)

        try:
            refHeatLoad = float(row[indices["refHeatIdx"]])
        except ValueError:
            refHeatLoad = calibSession.refHeatLoad

        if not refGradVal:
            refGradVal = float(row[indices["gradIdx"]])

        return self.addDataSession(startTime, endTime, timeInterval,
                                   float(row[indices["jtIdx"]]), refHeatLoad,
                                   refGradVal, calibSession)

    def genPV(self, formatStr, suffix):
        return formatStr.format(CM=self.cryModNumJLAB, CAV=self.cavNum,
                                SUFF=suffix)

    def genAcclPV(self, suffix):
        return self.genPV("ACCL:L1B:0{CM}{CAV}0:{SUFF}", suffix)

    def getPVs(self):
        return ([self.parent.valvePV, self.parent.dsLevelPV,
                 self.parent.usLevelPV, self.gradPV,
                 self.parent.dsPressurePV] + self.parent.heaterDesPVs
                + self.parent.heaterActPVs)

    @property
    def gradPV(self):
        return self.genAcclPV("GACT")

    def addDataSession(self, startTime, endTime, timeInt, refValvePos,
                       refHeatLoad=None, refGradVal=None, calibSession=None):
        # type: (datetime, datetime, int, float, float, float, DataSession) -> Q0DataSession

        if not refHeatLoad:
            refHeatLoad = 0
            for heaterActPV in self.heaterActPVs:
                refHeatLoad += float(cagetPV(heaterActPV))

        session = Q0DataSession(self, startTime, endTime, timeInt, refValvePos,
                                refHeatLoad, refGradVal, calibSession)
        self.dataSessions[hash(session)] = session
        return session


class DataSession(object):

    def __init__(self, container, startTime, endTime, timeInt, refValvePos,
                 refHeatLoad):
        # type: (Container, datetime, datetime, int, float, float) -> None
        self.container = container

        # e.g. calib_CM12_2019-02-25--11-25_18672.csv
        self.fileNameFormatter = "data/calib_{cryoMod}{suff}"

        self._dataFileName = None
        self._numPoints = None
        self.refValvePos = refValvePos
        self.refHeatLoad = refHeatLoad
        self.timeInt = timeInt
        self.startTime = startTime
        self.endTime = endTime

        self.unixTimeBuff = []
        self.timeBuff = []
        self.valvePosBuff = []
        self.dsLevelBuff = []
        self.usLevelBuff = []
        self.gradBuff = []
        self.dsPressBuff = []
        self.elecHeatDesBuff = []
        self.elecHeatActBuff = []

        self.pvBuffMap = {container.valvePV: self.valvePosBuff,
                          container.dsLevelPV: self.dsLevelBuff,
                          container.usLevelPV: self.usLevelBuff}

        self.calibSlope = None
        self.calibIntercept = None

        self.delta = 0

        self.liquidVsTimeAxis = None
        self.heaterCalibAxis = None

        self.runs = []

    def __hash__(self):
        return self.hash(self.startTime, self.endTime, self.timeInt,
                         self.container.cryModNumSLAC,
                         self.container.cryModNumJLAB)

    def __str__(self):
        return ("{START} to {END} ({RATE}s sample interval)"
                .format(START=self.startTime, END=self.endTime,
                        RATE=self.timeInt))

    ############################################################################
    # getArchiveData runs a shell command to get archive data. The syntax we're
    # using is:
    #
    #     mySampler -b "%Y-%m-%d %H:%M:%S" -s 1s -n[numPoints] [pv1] ... [pvn]
    #
    # where the "-b" denotes the start time, "-s 1s" says that the desired time
    # step between data points is 1 second, -n[numPoints] tells us how many
    # points we want, and [pv1]...[pvn] are the PVs we want archived
    #
    # Ex:
    #     mySampler -b "2019-03-28 14:16" -s 30s -n11 R121PMES R221PMES
    #
    # @param startTime: datetime object
    # @param signals: list of PV strings
    ############################################################################
    @staticmethod
    def getArchiveData(startTime, numPoints, signals,
                       timeInt=MYSAMPLER_TIME_INTERVAL):
        cmd = (['mySampler', '-b'] + [startTime.strftime("%Y-%m-%d %H:%M:%S")]
               + ['-s', str(timeInt) + 's', '-n' + str(numPoints)]
               + signals)
        try:
            return check_output(cmd)
        except (CalledProcessError, OSError) as e:
            writeAndFlushStdErr("mySampler failed with error: " + str(e) + "\n")
            return None

    @staticmethod
    def parseRawData(startTime, numPoints, signals,
                     timeInt=MYSAMPLER_TIME_INTERVAL):
        print("\nGetting data from the archive...\n")
        rawData = DataSession.getArchiveData(startTime, numPoints, signals,
                                             timeInt)

        if not rawData:
            return None

        else:
            rawDataSplit = rawData.splitlines()
            rows = ["\t".join(rawDataSplit.pop(0).strip().split())]
            rows.extend(list(map(lambda x: DataSession.reformatDate(x),
                                 rawDataSplit)))
            return reader(rows, delimiter='\t')

    @staticmethod
    def reformatDate(row):
        try:
            # This clusterfuck regex is pretty much just trying to find strings
            # that match %Y-%m-%d %H:%M:%S and making them %Y-%m-%d-%H:%M:%S
            # instead (otherwise the csv parser interprets it as two different
            # columns)
            regex = compile("[0-9]{4}-[0-9]{2}-[0-9]{2}"
                            + " [0-9]{2}:[0-9]{2}:[0-9]{2}")
            res = findall(regex, row)[0].replace(" ", "-")
            reformattedRow = regex.sub(res, row)
            return "\t".join(reformattedRow.strip().split())

        except IndexError:

            writeAndFlushStdErr("Could not reformat date for row: " + str(row)
                                + "\n")
            return "\t".join(row.strip().split())

    @property
    def runHeatLoads(self):
        return [run.totalHeatLoad for run in self.runs
                if run.elecHeatLoadDes == 0]

    @property
    def adjustedRunSlopes(self):
        m = self.calibSlope
        b = self.calibIntercept
        return [(m * run.totalHeatLoad) + b for run in self.runs
                if run.elecHeatLoadDes == 0]

    @property
    def runSlopes(self):
        return [run.slope for run in self.runs]

    @property
    def runElecHeatLoads(self):
        return [run.elecHeatLoad for run in self.runs]

    def printReport(self):
        for run in self.runs:
            run.printReport()

    def addRun(self, startIdx, endIdx):
        self.runs.append(DataRun(startIdx, endIdx, self, len(self.runs) + 1))

    @staticmethod
    def hash(startTime, endTime, timeInt, slacNum, jlabNum, calibSession=None,
             refGradVal=None):
        return (hash(startTime) ^ hash(endTime) ^ hash(timeInt) ^ hash(slacNum)
                ^ hash(jlabNum))

    @property
    def numPoints(self):
        if not self._numPoints:
            self._numPoints = int(
                (self.endTime - self.startTime).total_seconds()
                / self.timeInt)
        return self._numPoints

    @property
    def fileName(self):
        if not self._dataFileName:
            # Define a file name for the CSV we're saving. There are calibration
            # files and q0 measurement files. Both include a time stamp in the
            # format year-month-day--hour-minute. They also indicate the number
            # of data points.
            suffixStr = "{start}{nPoints}.csv"
            suffix = suffixStr.format(
                start=self.startTime.strftime("_%Y-%m-%d--%H-%M_"),
                nPoints=self.numPoints)
            cryoModStr = "CM{cryMod}".format(
                cryMod=self.container.cryModNumSLAC)

            self._dataFileName = self.fileNameFormatter.format(
                cryoMod=cryoModStr, suff=suffix)

        return self._dataFileName

    def processData(self):

        self.parseDataFromCSV()
        self.populateRuns()

        if not self.runs:
            print("{name} has no runs to process and plot."
                  .format(name=self.container.name))
            return

        self.adjustForSettle()
        self.processRuns()
        self.plotAndFitData()

    @staticmethod
    def genAxis(title, xlabel, ylabel):
        # type: (str, str, str) -> Axes
        fig = plt.figure()
        ax = fig.add_subplot(111)
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        return ax

    ############################################################################
    # plotAndFitData takes three related arrays, plots them, and fits some trend
    # lines
    ############################################################################
    def plotAndFitData(self):
        # TODO improve plots with human-readable time

        plt.rcParams.update({'legend.fontsize': 'small'})

        suffix = " ({name} Heater Calibration)".format(name=self.container.name)

        self.liquidVsTimeAxis = self.genAxis("Liquid Level vs. Time" + suffix,
                                             "Unix Time (s)",
                                             "Downstream Liquid Level (%)")

        for run in self.runs:  # type: DataRun
            # First we plot the actual run data
            self.liquidVsTimeAxis.plot(run.times, run.data, label=run.label)

            # Then we plot the linear fit to the run data
            self.liquidVsTimeAxis.plot(run.times, [run.slope * x + run.intercept
                                                   for x in run.times])

        self.liquidVsTimeAxis.legend(loc='best')
        self.heaterCalibAxis = self.genAxis("Liquid Level Rate of Change vs."
                                            " Heat Load", "Heat Load (W)",
                                            "dLL/dt (%/s)")

        self.heaterCalibAxis.plot(self.runElecHeatLoads, self.runSlopes,
                                  marker="o", linestyle="None",
                                  label="Heater Calibration Data")

        slopeStr = '{:.2e}'.format(Decimal(self.calibSlope))
        labelStr = "Calibration Fit:  {slope} %/(s*W)".format(
            slope=slopeStr)

        self.heaterCalibAxis.plot(self.runElecHeatLoads,
                                  [self.calibSlope * x + self.calibIntercept
                                   for x in self.runElecHeatLoads],
                                  label=labelStr)

        self.heaterCalibAxis.legend(loc='best')

    # noinspection PyTupleAssignmentBalance
    def processRuns(self):

        for run in self.runs:
            run.slope, run.intercept, r_val, p_val, std_err = linregress(
                run.times, run.data)

            # Print R^2 to diagnose whether or not we had a long enough data run
            print("Run {NUM} R^2: ".format(NUM=run.num) + str(r_val ** 2))

        # TODO we should consider whether all runs should be weighted equally

        # We're dealing with a cryomodule here so we need to calculate the
        # fit for the heater calibration curve.
        self.calibSlope, yIntercept = polyfit(self.runElecHeatLoads,
                                              self.runSlopes, 1)

        xIntercept = -yIntercept / self.calibSlope

        self.delta = -xIntercept
        print("Calibration curve intercept adjust = {DELTA} W"
              .format(DELTA=self.delta))

        self.calibIntercept = 0

        if TEST_MODE:
            for i, run in enumerate(self.runs):
                startTime = self.unixTimeBuff[run.startIdx]
                endTime = self.unixTimeBuff[run.endIdx]
                runStr = "Duration of run {runNum}: {duration}"
                print(runStr.format(runNum=(i + 1),
                                    duration=((endTime - startTime) / 60.0)))

    ############################################################################
    # adjustForSettle cuts off data that's corrupted because the heat load on
    # the 2 K helium bath is changing. (When the cavity heater setting or the RF
    # gradient change, it takes time for that change to become visible to the
    # helium because there are intermediate structures with heat capacity.)
    ############################################################################
    def adjustForSettle(self):

        for i, run in enumerate(self.runs):

            startIdx = run.startIdx
            elecHeatBuff = self.elecHeatDesBuff

            if i == 0:
                totalHeatDelta = (elecHeatBuff[startIdx] - self.refHeatLoad)

            else:

                prevStartIdx = self.runs[i - 1].startIdx

                elecHeatDelta = (elecHeatBuff[startIdx]
                                 - elecHeatBuff[prevStartIdx])

                totalHeatDelta = abs(elecHeatDelta)

            # Calculate the number of data points to be chopped off the
            # beginning of the data run based on the expected change in the
            # cryomodule heat load. The scale factor is derived from the
            # assumption that a 1 W change in the heat load leads to about 25
            # useless points (and that this scales linearly with the change in
            # heat load, which isn't really true).
            # TODO scale this with sample rate
            cutoff = int(totalHeatDelta * 25)

            idx = self.runs[i].startIdx
            startTime = self.unixTimeBuff[idx]
            duration = 0

            while duration < cutoff:
                idx += 1
                duration = self.unixTimeBuff[idx] - startTime

            self.runs[i].startIdx = idx

            if TEST_MODE:
                print("cutoff: " + str(cutoff))

    ############################################################################
    # generateCSV is a function that generates a CSV data file if one doesn't
    # already exist
    ############################################################################
    def generateCSV(self):
        if isfile(self.fileName):
            return self.fileName

        csvReader = DataSession.parseRawData(self.startTime, self.numPoints,
                                             self.container.getPVs(),
                                             self.timeInt)

        if not csvReader:
            return None

        else:

            header = csvReader.next()
            trimmedHeader = deepcopy(header)

            heaterCols = []

            for heaterPV in self.container.heaterDesPVs:
                index = header.index(heaterPV)
                heaterCols.append(index)

            heaterActCols = []

            for heaterActPV in self.container.heaterActPVs:
                index = header.index(heaterActPV)
                heaterActCols.append(index)

            colsToDelete = sorted(heaterCols + heaterActCols, reverse=True)

            for index in colsToDelete:
                del trimmedHeader[index]

            trimmedHeader.append("Electric Heat Load Setpoint")
            trimmedHeader.append("Electric Heat Load Readback")

            with open(self.fileName, 'wb') as f:
                csvWriter = writer(f, delimiter=',')
                csvWriter.writerow(trimmedHeader)

                for row in csvReader:
                    trimmedRow = deepcopy(row)

                    heatLoadSetpoint = 0

                    for col in heaterCols:
                        try:
                            heatLoadSetpoint += float(row[col])
                        except ValueError:
                            heatLoadSetpoint = None
                            break

                    heatLoadAct = 0

                    for col in heaterActCols:
                        try:
                            heatLoadAct += float(row[col])
                        except ValueError:
                            heatLoadAct = None
                            break

                    for index in colsToDelete:
                        del trimmedRow[index]

                    trimmedRow.append(str(heatLoadSetpoint))
                    trimmedRow.append(str(heatLoadAct))
                    csvWriter.writerow(trimmedRow)

            return self.fileName

    ############################################################################
    # parseDataFromCSV parses CSV data to populate the given session's data
    # buffers
    ############################################################################
    def parseDataFromCSV(self):
        def linkBuffToColumn(column, dataBuff, headerRow):
            try:
                columnDict[column] = {"idx": headerRow.index(column),
                                      "buffer": dataBuff}
            except ValueError:
                writeAndFlushStdErr("Column " + column + " not found in CSV\n")

        columnDict = {}

        with open(self.fileName) as csvFile:

            csvReader = reader(csvFile)
            header = csvReader.next()

            # Figures out the CSV column that has that PV's data and maps it
            for pv, dataBuffer in self.pvBuffMap.items():
                linkBuffToColumn(pv, dataBuffer, header)

            linkBuffToColumn("Electric Heat Load Setpoint",
                             self.elecHeatDesBuff, header)

            linkBuffToColumn("Electric Heat Load Readback",
                             self.elecHeatActBuff, header)

            try:
                # Data fetched from the JLab archiver has the timestamp column
                # labeled "Date"
                timeIdx = header.index("Date")
                datetimeFormatStr = "%Y-%m-%d-%H:%M:%S"

            except ValueError:
                # Data exported from MyaPlot has the timestamp column labeled
                # "time"
                timeIdx = header.index("time")
                datetimeFormatStr = "%Y-%m-%d %H:%M:%S"

            timeZero = datetime.utcfromtimestamp(0)

            for row in csvReader:
                dt = datetime.strptime(row[timeIdx], datetimeFormatStr)

                self.timeBuff.append(dt)

                # We use the Unix time to make the math easier during data
                # processing
                self.unixTimeBuff.append((dt - timeZero).total_seconds())

                # Actually parsing the CSV data into the buffers
                for col, idxBuffDict in columnDict.items():
                    try:
                        idxBuffDict["buffer"].append(
                            float(row[idxBuffDict["idx"]]))
                    except ValueError:
                        writeAndFlushStdErr("Could not fill buffer: " + str(col)
                                            + "\n")
                        idxBuffDict["buffer"].append(None)

    def isEndOfCalibRun(self, idx, elecHeatLoad):
        # Find inflection points for the desired heater setting
        prevElecHeatLoad = (self.elecHeatDesBuff[idx - 1]
                            if idx > 0 else elecHeatLoad)

        heaterChanged = (elecHeatLoad != prevElecHeatLoad)
        liqLevelTooLow = (self.usLevelBuff[idx]
                          < UPSTREAM_LL_LOWER_LIMIT)
        valveOutsideTol = (abs(self.valvePosBuff[idx] - self.refValvePos)
                           > VALVE_POSITION_TOLERANCE)
        isLastElement = (idx == len(self.elecHeatDesBuff) - 1)

        heatersOutsideTol = (abs(elecHeatLoad - self.elecHeatActBuff[idx])
                             >= HEATER_TOLERANCE)

        # A "break" condition defining the end of a run if the desired heater
        # value changed, or if the upstream liquid level dipped below the
        # minimum, or if the valve position moved outside the tolerance, or if
        # we reached the end (which is a kinda jank way of "flushing" the last
        # run)
        return (heaterChanged or liqLevelTooLow or valveOutsideTol
                or isLastElement or heatersOutsideTol)

    def checkAndFlushRun(self, isEndOfRun, idx, runStartIdx):
        if isEndOfRun:
            runDuration = (self.unixTimeBuff[idx]
                           - self.unixTimeBuff[runStartIdx])

            if runDuration >= MIN_RUN_DURATION:
                self.addRun(runStartIdx, idx - 1)

            return idx

        return runStartIdx

    ############################################################################
    # populateRuns takes the data in an session's buffers and slices it into
    # data "runs" based on cavity heater settings.
    ############################################################################
    def populateRuns(self):
        runStartIdx = 0

        for idx, elecHeatLoad in enumerate(self.elecHeatDesBuff):
            runStartIdx = self.checkAndFlushRun(
                self.isEndOfCalibRun(idx, elecHeatLoad), idx, runStartIdx)

    def __eq__(self, other):
        return (isinstance(other, DataSession)
                and self.startTime == other.startTime
                and self.endTime == other.endTime
                and self.timeInt == other.timeInt)

    def __ne__(self, other):
        return not self.__eq__(other)


class Q0DataSession(DataSession):

    def __init__(self, container, startTime, endTime, timeInt, refValvePos,
                 refHeatLoad, refGradVal, calibSession):
        # type: (Cavity, datetime, datetime, int, float, float, float, DataSession) -> None
        super(Q0DataSession, self).__init__(container, startTime, endTime,
                                            timeInt, refValvePos, refHeatLoad)
        self.fileNameFormatter = "data/q0meas_{cryoMod}_cav{cavityNum}{suff}"
        self.pvBuffMap = {container.parent.valvePV: self.valvePosBuff,
                          container.parent.dsLevelPV: self.dsLevelBuff,
                          container.parent.usLevelPV: self.usLevelBuff,
                          container.gradPV: self.gradBuff,
                          container.parent.dsPressurePV: self.dsPressBuff}
        self.refGradVal = refGradVal
        self.calibSession = calibSession
        # Another jank thing to tell the IDE that it's a Cavity
        self.container = container

    def __hash__(self):
        return self.hash(self.startTime, self.endTime, self.timeInt,
                         self.container.cryModNumSLAC,
                         self.container.cryModNumJLAB, self.calibSession,
                         self.refGradVal)

    @property
    def offset(self):
        offsets = []

        for run in self.runs:
            runOffset = run.offset
            if runOffset:
                offsets.append(runOffset)

        return mean(offsets) if offsets else 0

    def plotAndFitData(self):
        # TODO improve plots with human-readable time

        plt.rcParams.update({'legend.fontsize': 'small'})

        suffix = " ({name})".format(name=self.container.name)

        self.liquidVsTimeAxis = self.genAxis("Liquid Level vs. Time" + suffix,
                                             "Unix Time (s)",
                                             "Downstream Liquid Level (%)")

        for run in self.runs:
            # First we plot the actual run data
            self.liquidVsTimeAxis.plot(run.times, run.data, label=run.label)

            # Then we plot the linear fit to the run data
            self.liquidVsTimeAxis.plot(run.times, [run.slope * x + run.intercept
                                                   for x in run.times])

        self.liquidVsTimeAxis.legend(loc='best')

    # noinspection PyTupleAssignmentBalance
    def processRuns(self):

        for run in self.runs:
            run.slope, run.intercept, r_val, p_val, std_err = linregress(
                run.times, run.data)

            # Print R^2 to diagnose whether or not we had a long enough data run
            print("Run {NUM} R^2: ".format(NUM=run.num) + str(r_val ** 2))

        if TEST_MODE:
            for i, run in enumerate(self.runs):
                startTime = self.unixTimeBuff[run.startIdx]
                endTime = self.unixTimeBuff[run.endIdx]
                runStr = "Duration of run {runNum}: {duration}"
                print(runStr.format(runNum=(i + 1),
                                    duration=((endTime - startTime) / 60.0)))

    @staticmethod
    def hash(startTime, endTime, timeInt, slacNum, jlabNum, calibSession=None,
             refGradVal=None):
        return (hash(startTime) ^ hash(endTime) ^ hash(timeInt) ^ hash(slacNum)
                ^ hash(jlabNum) ^ hash(calibSession) ^ hash(refGradVal))

    @property
    def adjustedRunSlopes(self):
        m = self.calibSession.calibSlope
        b = self.calibSession.calibIntercept
        return [(m * run.totalHeatLoad) + b for run in self.runs
                if run.elecHeatLoadDes == 0]

    def populateRuns(self):

        runStartIdx = 0

        for idx, elecHeatLoad in enumerate(self.elecHeatDesBuff):

            try:
                gradChanged = (abs(self.gradBuff[idx] - self.gradBuff[idx - 1])
                               > GRAD_TOLERANCE) if idx != 0 else False
            except TypeError:
                gradChanged = False

            isEndOfQ0Run = (self.isEndOfCalibRun(idx, elecHeatLoad)
                            or gradChanged)

            runStartIdx = self.checkAndFlushRun(isEndOfQ0Run, idx, runStartIdx)

    # Approximates the expected heat load on a cavity from its RF gradient. A
    # cavity with the design Q of 2.7E10 should produce about 9.6 W of heat with
    # a gradient of 16 MV/m. The heat scales quadratically with the gradient. We
    # don't know the correct Q yet when we call this function so we assume the
    # design values.
    @staticmethod
    def approxHeatFromGrad(grad):
        # Gradients < 0 are non-physical so assume no heat load in that case.
        # The gradient values we're working with are readbacks from cavity
        # gradient PVs so it's possible that they could go negative.
        return ((grad / 16) ** 2) * 9.6 if grad > 0 else 0

    def adjustForSettle(self):

        for i, run in enumerate(self.runs):

            startIdx = run.startIdx
            elecHeatBuff = self.elecHeatDesBuff

            if i == 0:
                totalHeatDelta = (elecHeatBuff[startIdx] - self.refHeatLoad)
                # This is the big difference, I think
                totalHeatDelta += self.approxHeatFromGrad(
                    self.gradBuff[startIdx])

            else:

                prevStartIdx = self.runs[i - 1].startIdx

                elecHeatDelta = (elecHeatBuff[startIdx]
                                 - elecHeatBuff[prevStartIdx])

                currGrad = self.gradBuff[startIdx]
                currGradHeatLoad = self.approxHeatFromGrad(currGrad)

                prevGrad = self.gradBuff[prevStartIdx]
                prevGradHeatLoad = self.approxHeatFromGrad(prevGrad)

                gradHeatDelta = currGradHeatLoad - prevGradHeatLoad
                totalHeatDelta = abs(elecHeatDelta + gradHeatDelta)

            cutoff = int(totalHeatDelta * 25)

            idx = self.runs[i].startIdx
            startTime = self.unixTimeBuff[idx]
            duration = 0

            while duration < cutoff:
                idx += 1
                duration = self.unixTimeBuff[idx] - startTime

            self.runs[i].startIdx = idx

            if TEST_MODE:
                print("cutoff: " + str(cutoff))

    @property
    def fileName(self):
        if not self._dataFileName:
            suffixStr = "{start}{nPoints}.csv"
            suffix = suffixStr.format(
                start=self.startTime.strftime("_%Y-%m-%d--%H-%M_"),
                nPoints=self.numPoints)
            cryoModStr = "CM{cryMod}".format(
                cryMod=self.container.cryModNumSLAC)

            self._dataFileName = self.fileNameFormatter.format(
                cryoMod=cryoModStr, suff=suffix,
                cavityNum=self.container.cavNum)

        return self._dataFileName

    def addRun(self, startIdx, endIdx):
        self.runs.append(Q0DataRun(startIdx, endIdx, self, len(self.runs) + 1))


class DataRun(object):
    # __metaclass__ = ABCMeta

    def __init__(self, runStartIdx=None, runEndIdx=None, container=None,
                 num=None):
        # type: (int, int, DataSession, int) -> None
        # startIdx and endIdx define the beginning and the end of this data run
        # within the cryomodule or cavity's data buffers
        self.startIdx = runStartIdx
        self.endIdx = runEndIdx

        # All data runs have liquid level information which gets fitted with a
        # line (giving us dLL/dt). The slope and intercept parametrize the line.
        self.slope = None
        self.intercept = None

        self.dataSession = container
        self.num = num

    @property
    def data(self):
        return self.dataSession.dsLevelBuff[self.startIdx:self.endIdx]

    @property
    def times(self):
        return self.dataSession.unixTimeBuff[self.startIdx:self.endIdx]

    # elecHeatLoad is the electric heat load over baseline for this run
    @property
    def elecHeatLoad(self):
        return (self.dataSession.elecHeatActBuff[self.endIdx]
                - self.dataSession.elecHeatActBuff[0]) + self.dataSession.delta

    @property
    def elecHeatLoadDes(self):
        return (self.dataSession.elecHeatDesBuff[self.endIdx]
                - self.dataSession.refHeatLoad)

    @property
    def label(self):
        labelStr = "{slope} %/s @ {heatLoad} W Electric Load"

        return labelStr.format(slope='%.2E' % Decimal(self.slope),
                               heatLoad=round(self.elecHeatLoad, 2))


# Q0DataRun stores all the information about cavity Q0 measurement runs that
# isn't included in the parent class DataRun
class Q0DataRun(DataRun):

    def __init__(self, runStartIdx, runEndIdx, dataSession, num):
        # type: (int, int, Q0DataSession, int) -> None
        super(Q0DataRun, self).__init__(runStartIdx, runEndIdx, dataSession,
                                        num)

        # The average gradient
        self.grad = None
        self._calculatedQ0 = None
        # jank ass thing to tell the IDE that it's a Q0DataSession
        self.dataSession = dataSession

    # Q0 measurement runs have a total heat load value which we calculate
    # by projecting the run's dLL/dt on the cryomodule's heater calibration
    # curve
    @property
    def totalHeatLoad(self):
        if self.elecHeatLoadDes != 0:
            return self.elecHeatLoad
        else:
            return (((self.slope - self.dataSession.calibSession.calibIntercept)
                     / self.dataSession.calibSession.calibSlope)
                    + self.dataSession.offset)

    # The RF heat load is equal to the total heat load minus the electric
    # heat load
    @property
    def rfHeatLoad(self):
        if self.elecHeatLoadDes != 0:
            return 0
        else:
            return self.totalHeatLoad - self.elecHeatLoad

    @property
    def offset(self):
        if self.elecHeatLoadDes != 0:
            calcHeatLoad = ((self.slope
                             - self.dataSession.calibSession.calibIntercept)
                            / self.dataSession.calibSession.calibSlope)
            return self.elecHeatLoad - calcHeatLoad
        else:
            return None

    # The calculated Q0 value for this run
    # Magical formula from Mike Drury (drury@jlab.org) to calculate Q0 from the
    # measured heat load on a cavity, the RF gradient used during the test, and
    # the pressure of the incoming 2 K helium.
    @property
    def q0(self):
        if self.elecHeatLoadDes != 0:
            return None

        if not self._calculatedQ0:
            q0s = []
            numInvalidGrads = self.dataSession.gradBuff.count(0)

            for idx in range(self.startIdx, self.endIdx):
                archiveGrad = self.dataSession.gradBuff[idx]

                q0s.append(self.calcQ0(archiveGrad if archiveGrad
                                       else self.dataSession.refGradVal,
                                       self.rfHeatLoad,
                                       self.dataSession.dsPressBuff[idx]))

            if numInvalidGrads:
                writeAndFlushStdErr("\nGradient buffer had {NUM} invalid points"
                                    " (used reference gradient value instead) "
                                    "- Consider refetching the data from the "
                                    "archiver\n"
                                    .format(NUM=numInvalidGrads))
                stderr.flush()

            self._calculatedQ0 = mean(q0s)

        return self._calculatedQ0

    @property
    def label(self):
        # This is a heater run. It could be part of a cryomodule heater
        # calibration or it could be part of a cavity Q0 measurement.
        if self.elecHeatLoadDes != 0:
            return super(Q0DataRun, self).label

        # This is an RF run taken during a cavity Q0 measurement.
        else:

            labelStr = "{slope} %/s @ {grad} MV/m\nCalculated Q0: {Q0}"
            q0Str = '{:.2e}'.format(Decimal(self.q0))

            return labelStr.format(slope='%.2E' % Decimal(self.slope),
                                   grad=self.dataSession.refGradVal, Q0=q0Str)

    def printReport(self):
        reportStr = ("\n{cavName} run {runNum} total heat load: {TOT} W\n"
                     "            Electric heat load: {ELEC} W\n"
                     "                  RF heat load: {RF} W\n"
                     "                 Calculated Q0: {{Q0Val}}\n")

        report = reportStr.format(cavName=self.dataSession.container.name,
                                  runNum=self.num,
                                  TOT=round(self.totalHeatLoad, 2),
                                  ELEC=round(self.elecHeatLoad, 2),
                                  RF=round(self.rfHeatLoad, 2))

        if self.elecHeatLoadDes != 0:
            print(report.format(Q0Val=None))

        else:
            Q0 = '{:.2e}'.format(Decimal(self.q0))
            print(report.format(Q0Val=Q0))

    @staticmethod
    def calcQ0(grad, rfHeatLoad, avgPressure=None):
        # The initial Q0 calculation doesn't account for the temperature
        # variation of the 2 K helium
        uncorrectedQ0 = ((grad * 1000000) ** 2) / (939.3 * rfHeatLoad)

        # We can correct Q0 for the helium temperature!
        if avgPressure:
            tempFromPress = (avgPressure * 0.0125) + 1.705

            C1 = 271
            C2 = 0.0000726
            C3 = 0.00000214
            C4 = grad - 0.7
            C5 = 0.000000043
            C6 = -17.02
            C7 = C2 - (C3 * C4) + (C5 * (C4 ** 2))

            correctedQ0 = C1 / ((C7 / 2) * exp(C6 / 2)
                                + C1 / uncorrectedQ0
                                - (C7 / tempFromPress)
                                * exp(C6 / tempFromPress))
            return correctedQ0

        else:
            return uncorrectedQ0


def main():
    cryomodule = Cryomodule(cryModNumSLAC=12, cryModNumJLAB=2)
    for idx, cav in cryomodule.cavities.items():  # type: (int, Cavity)
        print(cav.gradPV)


if __name__ == '__main__':
    main()
