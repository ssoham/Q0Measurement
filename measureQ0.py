################################################################################
# A utility used to calculate Q0 for a set of cavities in a given cryomodules
# using the change in 2K helium liquid level (LL) per unit time
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import division
from __future__ import print_function
from datetime import datetime, timedelta
from math import exp
from decimal import Decimal
from csv import reader, writer
from subprocess import check_output, CalledProcessError
from re import compile, findall
from os import walk
from os.path import isfile, join, abspath, dirname
from fnmatch import fnmatch
from matplotlib import pyplot as plt
from numpy import polyfit, linspace, mean
from sys import stderr
from scipy.stats import linregress
from json import dumps
from time import sleep
from cryomodule import Cryomodule, DataRun, Q0DataRun
from copy import deepcopy


# Set True to use a known data set for debugging and/or demoing
# Set False to prompt the user for real data
IS_DEMO = True


# The LL readings get wonky when the upstream liquid level dips below 66
UPSTREAM_LL_LOWER_LIMIT = 66


# Used to reject data where the JT valve wasn't at the correct position
VALVE_POSITION_TOLERANCE = 2


# Used to reject data where the cavity gradient wasn't at the correct value
GRAD_TOLERANCE = 0.7


# Used to reject data where the cavity heater wasn't at the correct value
HEATER_TOLERANCE = 1


# We fetch data from the JLab archiver with a program called MySampler, which
# samples the chosen PVs at a user-specified time interval. Increase to improve
# statistics, decrease to lower the size of the CSV files and speed up
# MySampler data acquisition.
MYSAMPLER_TIME_INTERVAL = 5


# The minimum acceptable run length is fifteen minutes
MIN_RUN_DURATION = 900


# Used in custom input functions just below
ERROR_MESSAGE = "Please provide valid input"


# Trying to make this compatible with both 2.7 and 3 (input in 3 is the same as
# raw_input in 2.7, but input in 2.7 calls evaluate)
if hasattr(__builtins__, 'raw_input'):
    input = raw_input


def get_float_lim(prompt, low_lim, high_lim):
    return getNumericalInput(prompt, low_lim, high_lim, float)


def getNumericalInput(prompt, lowLim, highLim, inputType):
    response = get_input(prompt, inputType)

    while response < lowLim or response > highLim:
        stderr.write(ERROR_MESSAGE + "\n")
        # Need to pause briefly for some reason to make sure the error message
        # shows up before the next prompt
        sleep(0.01)
        response = get_input(prompt, inputType)

    return response


def get_input(prompt, desired_type):
    response = input(prompt)

    try:
        response = desired_type(response)
    except ValueError:
        stderr.write(str(desired_type) + " required\n")
        sleep(0.01)
        return get_input(prompt, desired_type)

    return response


def get_int_lim(prompt, low_lim, high_lim):
    return getNumericalInput(prompt, low_lim, high_lim, int)


def get_str_lim(prompt, acceptable_strings):
    response = get_input(prompt, str)

    while response not in acceptable_strings:
        stderr.write(ERROR_MESSAGE + "\n")
        sleep(0.01)
        response = get_input(prompt, str)

    return response


# Finds files whose names start with prefix and indexes them consecutively
# (instead of just using its index in the directory)
def findDataFiles(prefix):
    fileDict = {}
    numFiles = 1

    for root, dirs, files in walk(abspath(dirname(__file__))):
        for name in files:
            if fnmatch(name, prefix + "*"):
                fileDict[numFiles] = name
                # fileDict[numFiles] = join(root, name)
                numFiles += 1

    fileDict[numFiles] = "Generate a new CSV"
    return fileDict


def buildCryModObj(cryomoduleSLAC, cryomoduleLERF, valveLockedPos,
                   refHeaterVal):
    print("\n*** Now we'll start building a calibration file " +
          "- please be patient ***\n")

    startTimeCalib = buildDatetimeFromInput("Start time for calibration run:")

    upperLimit = (datetime.now() - startTimeCalib).total_seconds() / 3600
    duration = get_float_lim("Duration of calibration run in hours: ",
                             MIN_RUN_DURATION / 3600, upperLimit)
    endTimeCalib = startTimeCalib + timedelta(hours=duration)

    cryoModuleObj = Cryomodule(cryModNumSLAC=cryomoduleSLAC,
                               cryModNumJLAB=cryomoduleLERF,
                               calFileName=None,
                               refValvePos=valveLockedPos,
                               refHeatLoad=refHeaterVal)

    fileName = generateCSV(startTimeCalib, endTimeCalib, cryoModuleObj)

    if not fileName:
        return None

    else:
        cryoModuleObj.dataFileName = fileName
        return cryoModuleObj


def buildDatetimeFromInput(prompt):
    print(prompt)

    now = datetime.now()
    # The signature is: get_int_limited(prompt, low_lim, high_lim)
    # We're doing a bunch of right-justification here so that the prompts for
    # numerical input line up neatly on the command line.
    year = get_int_lim("Year: ".rjust(16), 2019, now.year)

    month = get_int_lim("Month: ".rjust(16), 1,
                        now.month if year == now.year else 12)

    day = get_int_lim("Day: ".rjust(16), 1,
                      now.day if (year == now.year
                                  and month == now.month) else 31)

    hour = get_int_lim("Hour: ".rjust(16), 0, 23)
    minute = get_int_lim("Minute: ".rjust(16), 0, 59)

    return datetime(year, month, day, hour, minute)


################################################################################
# generateCSV is a function that takes either a Cryomodule or Cavity object and
# generates a CSV data file for it if one doesn't already exist (or overwrites
# the previously existing file if the user desires)
#
# @param startTime, endTime: datetime objects
# @param object: Cryomodule or Cryomodule.Cavity
################################################################################
def generateCSV(startTime, endTime, obj):
    numPoints = int((endTime - startTime).total_seconds()
                    / MYSAMPLER_TIME_INTERVAL)

    # Define a file name for the CSV we're saving. There are calibration files
    # and q0 measurement files. Both include a time stamp in the format
    # year-month-day--hour-minute. They also indicate the number of data points.
    suffixStr = "{start}{nPoints}.csv"
    suffix = suffixStr.format(start=startTime.strftime("_%Y-%m-%d--%H-%M_"),
                              nPoints=numPoints)
    cryoModStr = "CM{cryMod}".format(cryMod=obj.cryModNumSLAC)

    if isinstance(obj, Cryomodule.Cavity):
        # e.g. q0meas_CM12_cav2_2019-03-03--12-00_10800.csv
        fileNameString = "data/q0meas_{cryoMod}_cav{cavityNum}{suff}"
        fileName = fileNameString.format(cryoMod=cryoModStr,
                                         cavityNum=obj.cavNum,
                                         suff=suffix)

    else:
        # e.g. calib_CM12_2019-02-25--11-25_18672.csv
        fileNameString = "data/calib_{cryoMod}{suff}"
        fileName = fileNameString.format(cryoMod=cryoModStr, suff=suffix)

    if isfile(fileName):
        overwrite = get_str_lim("Overwrite previous CSV file '" + fileName
                                + "' (y/n)? ",
                                acceptable_strings=['y', 'n']) == 'y'
        if not overwrite:
            return fileName

    print("\nGetting data from the archive...\n")
    rawData = getArchiveData(startTime, numPoints, obj.getPVs())

    if not rawData:
        return None

    else:

        rawDataSplit = rawData.splitlines()
        rows = ["\t".join(rawDataSplit.pop(0).strip().split())]
        rows.extend(list(map(lambda x: reformatDate(x), rawDataSplit)))
        csvReader = reader(rows, delimiter='\t')

        header = csvReader.next()
        trimmedHeader = deepcopy(header)

        heaterCols = []

        for heaterPV in obj.heaterPVs:
            index = header.index(heaterPV)
            heaterCols.append(index)

        heaterActCols = []

        for heaterActPV in obj.heaterActPVs:
            index = header.index(heaterActPV)
            heaterActCols.append(index)

        colsToDelete = sorted(heaterCols + heaterActCols, reverse=True)

        for index in colsToDelete:
            del trimmedHeader[index]

        trimmedHeader.append("Electric Heat Load Setpoint")
        trimmedHeader.append("Electric Heat Load Readback")

        with open(fileName, 'wb') as f:
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

        return fileName


################################################################################
# getArchiveData runs a shell command to get archive data. The syntax we're
# using is:
#
#     mySampler -b "%Y-%m-%d %H:%M:%S" -s 1s -n[numPoints] [pv1] [pv2] ...[pvn]
#
# where the "-b" denotes the start time, "-s 1s" says that the desired time step
# between data points is 1 second, -n[numPoints] tells us how many points we
# want, and [pv1]...[pvn] are the PVs we want archived
#
# Ex:
#     mySampler -b "201-03-28 14:16" -s 30s -n11 R121PMES R221PMES
#
# @param startTime: datetime object
# @param signals: list of PV strings
################################################################################
def getArchiveData(startTime, numPoints, signals):

    cmd = (['mySampler', '-b'] + [startTime.strftime("%Y-%m-%d %H:%M:%S")]
           + ['-s', str(MYSAMPLER_TIME_INTERVAL) + 's', '-n'] + [str(numPoints)]
           + signals)
    try:
        return check_output(cmd)
    except (CalledProcessError, OSError) as e:
        stderr.write("mySampler failed with error: " + str(e) + "\n")
        return None


def reformatDate(row):
    try:
        # This clusterfuck regex is pretty much just trying to find strings that
        # match %Y-%m-%d %H:%M:%S and making them %Y-%m-%d-%H:%M:%S instead
        # (otherwise the csv parser interprets it as two different columns)
        regex = compile("[0-9]{4}-[0-9]{2}-[0-9]{2}"
                        + " [0-9]{2}:[0-9]{2}:[0-9]{2}")
        res = findall(regex, row)[0].replace(" ", "-")
        reformattedRow = regex.sub(res, row)
        return "\t".join(reformattedRow.strip().split())
    except IndexError:
        stderr.write("Could not reformat date for row: " + str(row) + "\n")
        return "\t".join(row.strip().split())


def generateDataFiles(metadataFile):
    with open(metadataFile) as csvFile:
        csvReader = reader(csvFile)
        calibRow = csvReader.next()

        cryoModuleObj = Cryomodule(cryModNumSLAC=int(calibRow[0]),
                                   cryModNumJLAB=int(calibRow[1]),
                                   calFileName=None,
                                   refValvePos=float(calibRow[3]),
                                   refHeatLoad=float(calibRow[2]))

        datetimeFormatStr = "%m-%d-%Y-%H-%M"

        start = datetime.strptime(calibRow[4], datetimeFormatStr)
        end = datetime.strptime(calibRow[5], datetimeFormatStr)

        cryoModuleObj.dataFileName = generateCSV(start, end, cryoModuleObj)

        for row in csvReader:
            cavObj = cryoModuleObj.cavities[int(row[0])]
            cavObj.refGradVal = float(row[1])

            cavObj.refValvePos = float(row[2])

            start = datetime.strptime(row[3], datetimeFormatStr)
            end = datetime.strptime(row[4], datetimeFormatStr)

            cavObj.dataFileName = generateCSV(start, end, cavObj)

            processData(cavObj)
            cavObj.printReport()

#         SLAC  LERF    refheat jt  start   end
# cav   grad    jt  start   end

# parseDataFromCSV parses CSV data to populate the given object's data buffers
# @param obj: either a Cryomodule or Cavity object
def parseDataFromCSV(obj):
    columnDict = {}

    with open(obj.dataFileName) as csvFile:

        csvReader = reader(csvFile)
        header = csvReader.next()

        # Figures out the CSV column that has that PV's data and maps it
        for pv, dataBuff in obj.pvBuffMap.items():
            linkBuffToColumn(pv, dataBuff, columnDict, header)

        linkBuffToColumn("Electric Heat Load Setpoint", obj.elecHeatDesBuff,
                         columnDict, header)

        linkBuffToColumn("Electric Heat Load Readback", obj.elecHeatActBuff,
                         columnDict, header)

        try:
            # Data fetched from the JLab archiver has the timestamp column
            # labeled "Date"
            timeIdx = header.index("Date")
            datetimeFormatStr = "%Y-%m-%d-%H:%M:%S"

        except ValueError:
            # Data exported from MyaPlot has the timestamp column labeled "time"
            timeIdx = header.index("time")
            datetimeFormatStr = "%Y-%m-%d %H:%M:%S"

        timeZero = datetime.utcfromtimestamp(0)

        for row in csvReader:
            dt = datetime.strptime(row[timeIdx], datetimeFormatStr)

            obj.timeBuff.append(dt)

            # We use the Unix time to make the math easier during data
            # processing
            obj.unixTimeBuff.append((dt - timeZero).total_seconds())

            # Actually parsing the CSV data into the buffers
            for col, idxBuffDict in columnDict.items():
                try:
                    idxBuffDict["buffer"].append(float(row[idxBuffDict["idx"]]))
                except ValueError:
                    stderr.write("Could not fill buffer: " + str(col) + "\n")
                    idxBuffDict["buffer"].append(None)


def linkBuffToColumn(column, dataBuff, columnDict, header):
    try:
        columnDict[column] = {"idx": header.index(column), "buffer": dataBuff}
    except ValueError:
        stderr.write("Column " + column + " not found in CSV\n")


# @param obj: either a Cryomodule or Cavity object
def processData(obj):

    parseDataFromCSV(obj)
    populateRuns(obj)

    if not obj.runs:
        print("{name} has no runs to process and plot.".format(name=obj.name))
        return

    adjustForSettle(obj)
    processRuns(obj)

    return plotAndFitData(obj)


################################################################################
# populateRuns takes the data in an object's buffers and slices it into data
# "runs" based on cavity heater settings.
#
# @param obj: Either a Cryomodule or Cavity object
################################################################################
def populateRuns(obj):
    # noinspection PyShadowingNames
    def isEndOfCalibRun(idx, elecHeatLoad):
        # Find inflection points for the desired heater setting
        prevElecHeatLoad = (obj.elecHeatDesBuff[idx - 1]
                            if idx > 0 else elecHeatLoad)

        heaterChanged = (elecHeatLoad != prevElecHeatLoad)
        liqLevelTooLow = (obj.usLevelBuff[idx]
                          < UPSTREAM_LL_LOWER_LIMIT)
        valveOutsideTol = (abs(obj.valvePosBuff[idx] - obj.refValvePos)
                           > VALVE_POSITION_TOLERANCE)
        isLastElement = (idx == len(obj.elecHeatDesBuff) - 1)
        
        heatersOutsideTol = (abs(elecHeatLoad - obj.elecHeatActBuff[idx])
                             >= HEATER_TOLERANCE)

        # A "break" condition defining the end of a run if the desired heater
        # value changed, or if the upstream liquid level dipped below the
        # minimum, or if the valve position moved outside the tolerance, or if
        # we reached the end (which is a kinda jank way of "flushing" the last
        # run)
        return (heaterChanged or liqLevelTooLow or valveOutsideTol
                or isLastElement or heatersOutsideTol)

    # noinspection PyShadowingNames
    def checkAndFlushRun(isEndOfRun, idx, runStartIdx):
        if isEndOfRun:
            runDuration = obj.unixTimeBuff[idx] - obj.unixTimeBuff[runStartIdx]

            if runDuration >= MIN_RUN_DURATION:

                if isinstance(obj, Cryomodule):

                    # idx is actually right after the end of that run so we need
                    # to save (idx - 1) as the new run's final index.
                    obj.runs.append(DataRun(runStartIdx, idx - 1))

                else:
                    obj.runs.append(Q0DataRun(runStartIdx, idx - 1))
            return idx

        return runStartIdx

    isCryomodule = isinstance(obj, Cryomodule)

    runStartIdx = 0

    if isCryomodule:
        for idx, elecHeatLoad in enumerate(obj.elecHeatDesBuff):

            runStartIdx = checkAndFlushRun(isEndOfCalibRun(idx, elecHeatLoad),
                                           idx, runStartIdx)

    else:
        for idx, elecHeatLoad in enumerate(obj.elecHeatDesBuff):

            try:
                gradChanged = (abs(obj.gradBuff[idx] - obj.gradBuff[idx-1])
                               > GRAD_TOLERANCE) if idx != 0 else False
            except TypeError:
                gradChanged = False

            isEndOfQ0Run = isEndOfCalibRun(idx, elecHeatLoad) or gradChanged

            runStartIdx = checkAndFlushRun(isEndOfQ0Run, idx, runStartIdx)


################################################################################
# adjustForSettle cuts off data that's corrupted because the heat load on the
# 2 K helium bath is changing. (When the cavity heater setting or the RF
# gradient change, it takes time for that change to become visible to the
# helium because there are intermediate structures with heat capacity.)
################################################################################
def adjustForSettle(obj):

    # Approximates the expected heat load on a cavity from its RF gradient. A
    # cavity with the design Q of 2.7E10 should produce about 9.6 W of heat with
    # a gradient of 16 MV/m. The heat scales quadratically with the gradient. We
    # don't know the correct Q yet when we call this function so we assume the
    # design values.
    def approxHeatFromGrad(grad):
        # Gradients < 0 are non-physical so assume no heat load in that case.
        # The gradient values we're working with are readbacks from cavity
        # gradient PVs so it's possible that they could go negative.
        return ((grad / 16) ** 2) * 9.6 if grad > 0 else 0

    for i, run in enumerate(obj.runs):

        startIdx = run.startIdx
        elecHeatBuff = obj.elecHeatDesBuff
        
        if i == 0:
            totalHeatDelta = (elecHeatBuff[startIdx] - obj.refHeatLoad)
            totalHeatDelta += (approxHeatFromGrad(obj.gradBuff[startIdx])
                               if isinstance(obj, Cryomodule.Cavity) else 0)
                              
        else:
        
            prevStartIdx = obj.runs[i - 1].startIdx

            elecHeatDelta = elecHeatBuff[startIdx] - elecHeatBuff[prevStartIdx]

            totalHeatDelta = abs(elecHeatDelta)

            if isinstance(obj, Cryomodule.Cavity):
                gradBuff = obj.gradBuff

                # Checking that gradBuff isn't empty because we have some old
                # data from before the gradient was being archived.
                if gradBuff:
                    currGrad = gradBuff[startIdx]
                    currGradHeatLoad = approxHeatFromGrad(currGrad)

                    prevGrad = gradBuff[prevStartIdx]
                    prevGradHeatLoad = approxHeatFromGrad(prevGrad)

                    gradHeatDelta = currGradHeatLoad - prevGradHeatLoad
                    totalHeatDelta = abs(elecHeatDelta + gradHeatDelta)

        # Calculate the number of data points to be chopped off the beginning of
        # the data run based on the expected change in the cryomodule heat load.
        # The scale factor is derived from the assumption that a 1 W change in
        # the heat load leads to about 25 useless points (and that this scales
        # linearly with the change in heat load, which isn't really true).
        # TODO scale this with sample rate
        cutoff = int(totalHeatDelta * 25)
        
        idx = obj.runs[i].startIdx
        startTime = obj.unixTimeBuff[idx]
        duration = 0
        
        while duration < cutoff:
            idx += 1
            duration = obj.unixTimeBuff[idx] - startTime

        obj.runs[i].startIdx = idx

        if IS_DEMO:
            print("cutoff: " + str(cutoff))


# noinspection PyTupleAssignmentBalance
def processRuns(obj):

    isCalibration = isinstance(obj, Cryomodule)

    for run in obj.runs:

        runData = obj.dsLevelBuff[run.startIdx:run.endIdx]
        runTimes = obj.unixTimeBuff[run.startIdx:run.endIdx]

        run.slope, run.intercept, r_val, p_val, std_err = linregress(runTimes,
                                                                     runData)

        # run.elecHeatLoad = obj.elecHeatActBuff[run.endIdx] - obj.refHeatLoad
        run.elecHeatLoad = (obj.elecHeatActBuff[run.endIdx]
                            - obj.elecHeatActBuff[0])
        run.elecHeatLoadDes = (obj.elecHeatDesBuff[run.endIdx]
                               - obj.refHeatLoad)
        
        print("R^2: " + str(r_val ** 2))

        # Print R^2 to diagnose whether or not we had a long enough data run
        if IS_DEMO:
            print("R^2: " + str(r_val ** 2))

    if isCalibration:

        # TODO we should consider whether all runs should be weighted equally
        # TODO we should probably adjust the calib slope to intersect the origin

        # We're dealing with a cryomodule here so we need to calculate the
        # fit for the heater calibration curve.
        obj.calibSlope, yIntercept = polyfit(obj.runElecHeatLoads,
                                             obj.runSlopes, 1)
        
        xIntercept = -yIntercept / obj.calibSlope
        
        delta = -xIntercept
        print("Delta = " + str(delta))
        
        obj.calibIntercept = 0
        
        for run in obj.runs:
            run.elecHeatLoad += delta

    else:
        offsets = []
        heaterRuns = []

        for idx, run in enumerate(obj.runs):
            if run.elecHeatLoadDes != 0:
                calcHeatLoad = ((run.slope - obj.parent.calibIntercept)
                                / obj.parent.calibSlope)
                offsets.append(run.elecHeatLoad - calcHeatLoad)
                run.totalHeatLoad = run.elecHeatLoad
                run.rfHeatLoad = 0
                heaterRuns.append(idx)

        offset = mean(offsets) if offsets else 0

        for run in [rfRun for i, rfRun in enumerate(obj.runs)
                    if i not in heaterRuns]:

            heatLoad = ((run.slope - obj.parent.calibIntercept)
                        / obj.parent.calibSlope) + offset

            run.totalHeatLoad = heatLoad
            run.rfHeatLoad = heatLoad - run.elecHeatLoad

            q0s = []

            if obj.dsPressBuff:
                for idx in range(run.startIdx, run.endIdx):
                    if obj.gradBuff[idx]:
                        q0s.append(calcQ0(obj.gradBuff[idx], run.rfHeatLoad,
                                          obj.dsPressBuff[idx]))
                    else:
                        q0s.append(calcQ0(obj.refGradVal, run.rfHeatLoad,
                                          obj.dsPressBuff[idx]))
            else:
                for idx in range(run.startIdx, run.endIdx):
                    if obj.gradBuff[idx]:
                        q0s.append(calcQ0(obj.gradBuff[idx], run.rfHeatLoad))
                    else:
                        q0s.append(calcQ0(obj.refGradVal, run.rfHeatLoad))

            run.q0 = mean(q0s)

        # for run in obj.runs:
        #
        #     # We're dealing with a cavity here. We need to start by figuring out
        #     # the overall heat load over baseline for each run. We do this by
        #     # taking the run's slope and projecting it on the parent
        #     # cryomodule's heater calibration curve.
        #     # y = m * x + b  ==>  we want to find the heat load x, where...
        #     # y = run.slope
        #     # m = obj.parent.calibSlope
        #     # b = obj.parent.calibIntercept
        #     # So x = (y - b) / m
        #     heatLoad = ((run.slope - obj.parent.calibIntercept)
        #                 / obj.parent.calibSlope)
        #
        #     run.totalHeatLoad = heatLoad
        #     run.rfHeatLoad = heatLoad - run.elecHeatLoad
        #
        #     q0s = []
        #
        #     # We have some old data sets that are missing pressure and gradient
        #     # information. Unfortunately we need a bunch of if/else statements
        #     # to get around that. We can remove these if we want to discard
        #     # the old data at some point.
        #
        #     # TODO handle Nones in other buffers
        #
        #     if obj.dsPressBuff:
        #         for idx in range(run.startIdx, run.endIdx):
        #             if obj.gradBuff[idx]:
        #                 q0s.append(calcQ0(obj.gradBuff[idx], run.rfHeatLoad,
        #                                   obj.dsPressBuff[idx]))
        #             else:
        #                 q0s.append(calcQ0(obj.refGradVal, run.rfHeatLoad,
        #                                   obj.dsPressBuff[idx]))
        #     else:
        #         for idx in range(run.startIdx, run.endIdx):
        #             if obj.gradBuff[idx]:
        #                 q0s.append(calcQ0(obj.gradBuff[idx], run.rfHeatLoad))
        #             else:
        #                 q0s.append(calcQ0(obj.refGradVal, run.rfHeatLoad))
        #
        #     run.q0 = mean(q0s)

    if IS_DEMO:
        for i, run in enumerate(obj.runs):
            startTime = obj.unixTimeBuff[run.startIdx]
            endTime = obj.unixTimeBuff[run.endIdx]
            runStr = "Duration of run {runNum}: {duration}"
            print(runStr.format(runNum=(i + 1),
                                duration=((endTime - startTime) / 60.0)))
            heatStr = "Electric heat Load: {heat}"
            print(heatStr.format(heat=obj.runs[i].elecHeatLoad))


################################################################################
# plotAndFitData takes three related arrays, plots them, and fits some trend
# lines
#
# heatLoads, runs, and timeRuns are arrays that all have the same size such that
# heatLoads[i] corresponds to runs[i] corresponds to timeRuns[i]
#
# @param heatLoads: an array containing the heat load per data run
# @param runs: an array of arrays, where each runs[i] is a run of LL data for a
#              given heat load
# @param timeRuns: an array of arrays, where each timeRuns[i] is a list of
#                  timestamps that correspond to that run's LL data
# @param obj: Either a Cryomodule or Cavity object
################################################################################
def plotAndFitData(obj):
    # TODO improve plots with human-readable time

    plt.rcParams.update({'legend.fontsize': 'small'})

    isCalibration = isinstance(obj, Cryomodule)

    if isCalibration:
        suffix = " ({name} Heater Calibration)".format(name=obj.name)
    else:
        suffix = " ({name})".format(name=obj.name)

    liquidVsTimeAxis = genAxis("Liquid Level vs. Time" + suffix,
                               "Unix Time (s)", "Downstream Liquid Level (%)")

    for run in obj.runs:

        runData = obj.dsLevelBuff[run.startIdx:run.endIdx]
        runTimes = obj.unixTimeBuff[run.startIdx:run.endIdx]

        if run.elecHeatLoadDes != 0:
            # This is a heater run. It could be part of a cryomodule heater
            # calibration or it could be part of a cavity Q0 measurement.

            labelStr = "{slope} %/s @ {heatLoad} W Electric Load"

            runLabel = labelStr.format(slope='%.2E' % Decimal(run.slope),
                                       heatLoad=round(run.elecHeatLoad, 2))

        else:
            # This is an RF run taken during a cavity Q0 measurement.

            labelStr = "{slope} %/s @ {grad} MV/m\nCalculated Q0: {Q0}"
            q0Str = '{:.2e}'.format(Decimal(run.q0))

            runLabel = labelStr.format(slope='%.2E' % Decimal(run.slope),
                                       grad=obj.refGradVal, Q0=q0Str)

        # First we plot the actual run data
        liquidVsTimeAxis.plot(runTimes, runData, label=runLabel)

        # Then we plot the linear fit to the run data
        liquidVsTimeAxis.plot(runTimes, [run.slope * x + run.intercept
                                         for x in runTimes])

    if isCalibration:
        liquidVsTimeAxis.legend(loc='best')
        heaterCalibAxis = genAxis("Liquid Level Rate of Change vs. Heat Load",
                                  "Heat Load (W)", "dLL/dt (%/s)")

        heaterCalibAxis.plot(obj.runElecHeatLoads, obj.runSlopes, marker="o",
                             linestyle="None", label="Heater Calibration Data")

        slopeStr = '{:.2e}'.format(Decimal(obj.calibSlope))
        labelStr = "Calibration Fit:  {slope} %/(s*W)".format(slope=slopeStr)

        heaterCalibAxis.plot(obj.runElecHeatLoads,
                             [obj.calibSlope * x + obj.calibIntercept
                              for x in obj.runElecHeatLoads], label=labelStr)

        heaterCalibAxis.legend(loc='best')

        return heaterCalibAxis

    else:
        liquidVsTimeAxis.legend(loc='best')


def genAxis(title, xlabel, ylabel):
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    return ax


def addFileToCavity(cavity):
    print("\n*** Now we'll start building a Q0 measurement file " +
          "- please be patient ***\n")

    startTimeQ0Meas = buildDatetimeFromInput("Start time for the data run:")

    # We don't want users to request archive data from a time interval that
    # extends past the current time!
    upperLimit = (datetime.now() - startTimeQ0Meas).total_seconds() / 3600

    duration = get_float_lim("Duration of data run in hours: ",
                             MIN_RUN_DURATION / 3600, upperLimit)

    endTimeCalib = startTimeQ0Meas + timedelta(hours=duration)

    cavity.dataFileName = generateCSV(startTimeQ0Meas, endTimeCalib, cavity)

# Magical formula from Mike Drury (drury@jlab.org) to calculate Q0 from the
# measured heat load on a cavity, the RF gradient used during the test, and the
# pressure of the incoming 2 K helium.
def calcQ0(grad, rfHeatLoad, avgPressure=None):
    # The initial Q0 calculation doesn't account for the temperature variation
    # of the 2 K helium
        
    uncorrectedQ0 = ((grad * 1000000) ** 2) / (939.3 * rfHeatLoad)

    if avgPressure:
        # We can correct Q0 for the helium temperature!
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
                            - (C7 / tempFromPress) * exp(C6 / tempFromPress))
        return correctedQ0
    else:
        return uncorrectedQ0


def getQ0Measurements():
    if IS_DEMO:
        refHeaterVal = 64
        valveLockedPos = 24.1
        cryomoduleSLAC = 5
        cryomoduleLERF = 3
        fileName = "data/calib_CM5_2019-04-03--17-35_3456.csv"

        cryModObj = Cryomodule(cryomoduleSLAC, cryomoduleLERF,
                               fileName, valveLockedPos, refHeaterVal)

        cavities = [1]

    else:
        print("Cryomodule Heater Calibration Parameters:")
        # Signature is: get_float/get_int(prompt, low_lim, high_lim)
        refHeaterVal = get_float_lim("Reference Heater Value: ".rjust(32),
                                     0, 120)
        valveLockedPos = get_float_lim("JT Valve locked position: ".rjust(32),
                                       0, 100)
        cryomoduleSLAC = get_int_lim("SLAC Cryomodule Number: ".rjust(32),
                                     0, 33)
        cryomoduleLERF = get_int_lim("LERF Cryomodule Number: ".rjust(32),
                                     2, 3)

        print("\n---------- CRYOMODULE " + str(cryomoduleSLAC)
              + " ----------\n")

        calibFiles = findDataFiles("calib_CM" + str(cryomoduleSLAC))

        print("Options for Calibration Data:")

        # dumps pretty-prints a dictionary with the key:value pairs nicely
        # indented and separated onto different lines
        print("\n" + dumps(calibFiles, indent=4) + "\n")

        option = get_int_lim(
            "Please choose one of the options above: ", 1, len(calibFiles))

        if option == len(calibFiles):
            cryModObj = buildCryModObj(cryomoduleSLAC, cryomoduleLERF,
                                       valveLockedPos, refHeaterVal)

        else:
            cryModObj = Cryomodule(cryomoduleSLAC, cryomoduleLERF,
                                   "data/" + calibFiles[option], valveLockedPos,
                                   refHeaterVal)

        if not cryModObj:
            stderr.write("Calibration file generation failed - aborting\n")
            return

        else:
            numCavs = get_int_lim("Number of cavities to analyze: ", 0, 8)

            cavities = []
            for _ in range(numCavs):
                cavity = get_int_lim("Next cavity to analyze: ", 1, 8)
                while cavity in cavities:
                    cavity = get_int_lim(
                        "Please enter a cavity not previously entered: ", 1, 8)
                cavities.append(cavity)

    calibCurveAxis = processData(cryModObj)

    for cav in cavities:
        cavObj = cryModObj.cavities[cav]

        print("\n---------- CAVITY " + str(cav) + " ----------\n")

        cavObj.refGradVal = get_float_lim("Gradient used during Q0" +
                                          " measurement: ", 0, 22)

        cavObj.refValvePos = get_float_lim("JT Valve position used during Q0" +
                                           " measurement: ", 0, 100)

        fileStr = "q0meas_CM{cryMod}_cav{cavNum}".format(cryMod=cryomoduleSLAC,
                                                         cavNum=cav)
        q0MeasFiles = findDataFiles(fileStr)

        print("Options for Q0 Meaurement Data:")

        print("\n" + dumps(q0MeasFiles, indent=4) + "\n")

        option = get_int_lim(
            "Please choose one of the options above: ", 1, len(q0MeasFiles))

        if option == len(q0MeasFiles):
            addFileToCavity(cavObj)
            if not cavObj.dataFileName:
                stderr.write("Q0 measurement file generation failed" +
                             " - aborting\n")
                return

        else:
            cavObj.dataFileName = "data/" + q0MeasFiles[option]

        processData(cavObj)

        calibCurveAxis.plot(cavObj.runHeatLoads, cavObj.runSlopes, marker="o",
                            linestyle="None", label="Projected Data for "
                                                    + cavObj.name)

        calibCurveAxis.legend(loc='best', shadow=True, numpoints=1)

        minCavHeatLoad = min(cavObj.runHeatLoads)
        minCalibHeatLoad = min(cryModObj.runElecHeatLoads)

        if minCavHeatLoad < minCalibHeatLoad:
            yRange = linspace(minCavHeatLoad, minCalibHeatLoad)
            calibCurveAxis.plot(yRange, [cryModObj.calibSlope * i
                                         + cryModObj.calibIntercept
                                         for i in yRange])

        maxCavHeatLoad = max(cavObj.runHeatLoads)
        maxCalibHeatLoad = max(cryModObj.runElecHeatLoads)

        if maxCavHeatLoad > maxCalibHeatLoad:
            yRange = linspace(maxCalibHeatLoad, maxCavHeatLoad)
            calibCurveAxis.plot(yRange, [cryModObj.calibSlope * i
                                         + cryModObj.calibIntercept
                                         for i in yRange])

        cavObj.printReport()

    plt.draw()

    # for i in plt.get_fignums():
    #     plt.figure(i)
    #     plt.savefig("figure%d.png" % i)

    plt.show()


if __name__ == "__main__":
    getQ0Measurements()
