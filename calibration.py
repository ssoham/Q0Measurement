from __future__ import division
from csv import reader, writer
from datetime import datetime
from subprocess import check_output
from re import compile, findall
from os import walk
from os.path import isfile, join, abspath, dirname
from fnmatch import fnmatch
from matplotlib import pyplot as plt
from numpy import mean, std, polyfit, linspace
from sys import maxint, stderr
from scipy.stats import linregress
from json import dumps
from user_input import *
from cryomodule import *


def linkBufferToPV(pv, dataBuffer, columnDict, header):
    try:
        columnDict[pv] = {"idx": header.index(pv), "buffer": dataBuffer}
    except ValueError:
        print >> stderr, "Column " + pv + " not found in CSV"


def parseDataFromCSV(fileName, bufferMap, timeBuffer, isCalibration=False,
                     heaterPV=None, heatLoad=None):
    columnDict = {}

    with open(fileName) as csvFile:

        csvReader = reader(csvFile)
        header = csvReader.next()

        for pv, dataBuffer in bufferMap.iteritems():
            linkBufferToPV(pv, dataBuffer, columnDict, header)

        if isCalibration:
            linkBufferToPV(heaterPV, heatLoad, columnDict, header)

        timeIdx = header.index("time")
        timeZero = datetime.utcfromtimestamp(0)

        for row in csvReader:

            timeBuffer.append((datetime.strptime(row[timeIdx],
                                                 "%Y-%m-%d %H:%M:%S")
                               - timeZero).total_seconds())

            for col, idxBuffDict in columnDict.iteritems():
                try:
                    idxBuffDict["buffer"].append(float(row[idxBuffDict["idx"]]))
                except ValueError:
                    print >> stderr, "Could not parse row: " + str(row)


def parseCalibData(cryMod):
    heaterPV = cryMod.cavities[cryMod.calCavNum - 1].heaterPV
    parseDataFromCSV(cryMod.calFileName, cryMod.pvBufferMap, cryMod.calTime,
                     True, heaterPV, cryMod.calHeatLoad)


def parseQ0MeasData(cavity):
    parseDataFromCSV(cavity.q0MeasFileName, cavity.pvBufferMap,
                     cavity.q0MeasTime)


def processCalibrationData(cryMod, valveTolerance):
    parseCalibData(cryMod)

    # The readings get wonky when the upstream liquid level dips below 66, and
    # when the  valve position is +/- 1.2 from our locked position (found
    # empirically)
    runs, timeRuns, heaterVals = populateCalibRuns(cryMod, 66, valveTolerance)

    return genAndPlotRuns(heaterVals, runs, timeRuns, True)


def processQ0MeasData(cavity, valveTolerance):
    parseQ0MeasData(cavity)

    runs, timeRuns, heaterVals = populateQ0MeasRuns(cavity, 66, valveTolerance)

    return genAndPlotRuns(heaterVals, runs, timeRuns, False)


def genAndPlotRuns(heaterVals, runs, timeRuns, isCalibration):
    print "Heat Loads: " + str(heaterVals)
    adjustForHeaterSettle(heaterVals, runs, timeRuns)

    for timeRun in timeRuns:
        print "Duration of run: " + str((timeRun[-1] - timeRun[0]) / 60.0)

    ax1 = genAxis("Liquid Level as a Function of Time"
                  + (" (Calibration)" if isCalibration else ""),
                  "Unix Time (s)", "Downstream Liquid Level (%)")

    slopes = []

    for idx, run in enumerate(runs):
        m, b, r_val, p_val, std_err = linregress(timeRuns[idx], run)
        print"R^2: " + str(r_val ** 2)

        slopes.append(m)

        ax1.plot(timeRuns[idx], run, label=(str(round(m, 6)) + "%/s @ "
                                            + str(heaterVals[idx])
                                            + "W Electric Load"))

        ax1.plot(timeRuns[idx], [m * x + b for x in timeRuns[idx]])

    if isCalibration:
        ax1.legend(loc='lower right')
        ax2 = genAxis("Rate of Change of Liquid Level as a Function of Heat Load",
                      "Heat Load (W)", "dLL/dt (%/s)")

        ax2.plot(heaterVals, slopes, marker="o", linestyle="None",
                 label="Calibration Data")

        m, b = polyfit(heaterVals, slopes, 1)

        ax2.plot(heaterVals, [m*x + b for x in heaterVals],
                 label=(str(m)+" %/(s*W)"))

        ax2.legend(loc='upper right')

        return m, b, ax2, heaterVals

    else:
        ax1.legend(loc='upper right')
        return slopes


# Sometimes the heater takes a little while to settle, especially after large
# jumps, which renders the points taken during that time useless
def adjustForHeaterSettle(heaterVals, runs, timeRuns):
    for idx, heaterVal in enumerate(heaterVals):

        # Scaling factor 55 is derived from an observation that an 11W jump
        # leads to about 600 useless points (assuming it scales linearly)
        cutoff = (int(abs(heaterVal - heaterVals[idx - 1]) * 55)
                  if idx > 0 else 0)
        print "cutoff: " + str(cutoff)

        # Adjusting both buffers to keep them "synchronous"
        runs[idx] = runs[idx][cutoff:]
        timeRuns[idx] = timeRuns[idx][cutoff:]


def populateCalibRuns(cryMod, levelLimitUS, valvePosTolerance, cutoff=1000):

    def appendToBuffers(dataBuffers, startIdx, endIdx):
        for (runBuffer, dataBuffer) in dataBuffers:
            runBuffer.append(dataBuffer[startIdx: endIdx])

    runStartIdx = 0

    runs = []
    timeRuns = []
    inputVals = []

    for idx, val in enumerate(cryMod.calHeatLoad):

        prevInputVal = cryMod.calHeatLoad[idx - 1] if idx > 0 else val

        # A "break" condition defining the end of a run
        if (val != prevInputVal
                or cryMod.calUpstreamLevel[idx] < levelLimitUS
                or (abs(cryMod.calValvePos[idx] - cryMod.refValvePos)
                    > valvePosTolerance)
                or idx == len(cryMod.calHeatLoad) - 1):

            # Keeping only those runs with at least <cutoff> points
            if idx - runStartIdx > cutoff:
                inputVals.append(prevInputVal - cryMod.refHeaterVal)
                appendToBuffers([(runs, cryMod.calDownstreamLevel),
                                 (timeRuns, cryMod.calTime)],
                                runStartIdx, idx)

            runStartIdx = idx

    return runs, timeRuns, inputVals


def populateQ0MeasRuns(cavity, levelLimitUS, valvePosTolerance, cutoff=500):

    def appendToBuffers(dataBuffers, startIdx, endIdx):
        for (runBuffer, dataBuffer) in dataBuffers:
            runBuffer.append(dataBuffer[startIdx: endIdx])

    runStartIdx = 0

    runs = []
    timeRuns = []
    inputVals = []

    for idx, val in enumerate(cavity.q0MeasHeatLoad):

        prevInputVal = cavity.q0MeasHeatLoad[idx - 1] if idx > 0 else val

        # A "break" condition defining the end of a run
        if (val != prevInputVal
                or cavity.q0MeasUpstreamLevel[idx] < levelLimitUS
                or (abs(cavity.q0MeasValvePos[idx] - cavity.parent.refValvePos)
                    > valvePosTolerance)
                or idx == len(cavity.q0MeasHeatLoad) - 1):

            # Keeping only those runs with at least <cutoff> points
            if idx - runStartIdx > cutoff:
                inputVals.append(prevInputVal - cavity.parent.refHeaterVal)
                appendToBuffers([(runs, cavity.q0MeasDownstreamLevel),
                                 (timeRuns, cavity.q0MeasTime)],
                                runStartIdx, idx)

            runStartIdx = idx

    return runs, timeRuns, inputVals


def genAxis(title, xlabel, ylabel):
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    return ax


def calcQ0(gradient, inputHeatLoad, refGradient=16.0, refHeatLoad=9.6,
           refQ0=2.7E10):
    return refQ0 * (refHeatLoad / inputHeatLoad) * ((gradient / refGradient)**2)


def getArchiveData(startTime, nSecs, signals):
    # startTime & endTime are datetime objects, signals is a list of PV names
    cmd = (['mySampler', '-b'] + [startTime.strftime("%Y-%m-%d %H:%M:%S")]
           + ['-s', '1s', '-n'] + [str(nSecs)] + signals)
    return check_output(cmd)


def reformatDate(row):
    try:
        regex = compile(
            "[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}")
        res = findall(regex, row)[0].replace(" ", "-")
        reformattedRow = regex.sub(res, row)
        return "\t".join(reformattedRow.strip().split())
    except IndexError:
        print row
        return "\t".join(row.strip().split())


# def getQ0MeasCSV(startTime, endTime, signals, cryomodule, cavity):
#     nSecs = int((endTime - startTime).total_seconds())
#
#     # Define a file name for the CSV we're saving. There are calibration files
#     # and q0 measurement files. Both include a time stamp in the format
#     # year-month-day--hour-minute. They also indicate the number of data points.
#     suffix = startTime.strftime("_%Y-%m-%d--%H-%M_") + str(nSecs) + '.csv'
#     cryoModStr = 'CM' + str(cryomodule)
#
#     fileName = ('q0meas_' + cryoModStr + '_cav' + str(cavity) + suffix)
#
#     if isfile(fileName):
#         overwriteFile = get_str('Overwrite previous CSV file (y/n)? ', True,
#                                 ['y', 'n']) == 'y'
#         if not overwriteFile:
#             return fileName
#
#     rawData = getArchiveData(startTime, nSecs, signals)
#     rows = list(map(lambda x: reformatDate(x), rawData.splitlines()))
#     csvReader = reader(rows, delimiter='\t')
#
#     with open(fileName, 'wb') as f:
#         csvWriter = writer(f, delimiter='\t')
#         for row in csvReader:
#             csvWriter.writerow(row)
#
#     return fileName
#
#
# def getCalibCSV(startTime, endTime, signals, cryomodule):
#     nSecs = int((endTime - startTime).total_seconds())
#
#     # Define a file name for the CSV we're saving. There are calibration files
#     # and q0 measurement files. Both include a time stamp in the format
#     # year-month-day--hour-minute. They also indicate the number of data points.
#     suffix = startTime.strftime("_%Y-%m-%d--%H-%M_") + str(nSecs) + '.csv'
#     cryoModStr = 'CM' + str(cryomodule)
#
#     fileName = ('calib_' + cryoModStr + suffix)
#
#     if isfile(fileName):
#         overwriteFile = get_str('Overwrite previous CSV file (y/n)? ', True,
#                                 ['y', 'n']) == 'y'
#         if not overwriteFile:
#             return fileName
#
#     rawData = getArchiveData(startTime, nSecs, signals)
#     rows = list(map(lambda x: reformatDate(x), rawData.splitlines()))
#     csvReader = reader(rows, delimiter='\t')
#
#     with open(fileName, 'wb') as f:
#         csvWriter = writer(f, delimiter='\t')
#         for row in csvReader:
#             csvWriter.writerow(row)
#
#     return fileName


################################################################################
# generateCSV is a function that takes a date range and a list of PVs in order
# to generate a CSV data file if one doesn't already exist
#
# @param startTime, endTime: datetime objects
# @param signals: list of PV strings
################################################################################
def generateCSV(startTime, endTime, signals, cryomodule, cavity=0, isCalib=True):
    nSecs = int((endTime - startTime).total_seconds())

    # Define a file name for the CSV we're saving. There are calibration files
    # and q0 measurement files. Both include a time stamp in the format
    # year-month-day--hour-minute. They also indicate the number of data points.
    suffix = startTime.strftime("_%Y-%m-%d--%H-%M_") + str(nSecs) + '.csv'
    cryoModStr = 'CM' + str(cryomodule)

    if isCalib:
        fileName = ('calib_' + cryoModStr + suffix)
    else:
        fileName = ('q0meas_' + cryoModStr + '_cav' + str(cavity) + suffix)

    if isfile(fileName):
        overwriteFile = get_str('Overwrite previous CSV file (y/n)? ', True,
                                ['y', 'n']) == 'y'
        if not overwriteFile:
            return fileName

    rawData = getArchiveData(startTime, nSecs, signals)
    rows = list(map(lambda x: reformatDate(x), rawData.splitlines()))
    csvReader = reader(rows, delimiter='\t')

    with open(fileName, 'wb') as f:
        csvWriter = writer(f, delimiter='\t')
        for row in csvReader:
            csvWriter.writerow(row)

    return fileName


def demo():

    # 17.5 is the locked JT valve position during the calibration run
    # 1.91 was our reference heater value during the calibration
    cryoModuleObj = Cryomodule(12, 2, "calib_CM12_2019-02-25--11-25_18672.csv",
                               17.5, 1.91)

    calValvePosTol = 1.2

    m, b, ax, calibrationVals = processCalibrationData(cryoModuleObj,
                                                       calValvePosTol)

    # We're running this on cavity 2, which is index 1 because of 0-indexing
    cavityObj = cryoModuleObj.cavities[1]
    cavityObj.q0MeasFileName = "q0meas_CM12_cav2_2019-03-03--12-00_10800.csv"

    slopes = processQ0MeasData(cavityObj, calValvePosTol)

    heaterVals = []

    for dLL in slopes:
        heaterVal = (dLL - b) / m
        heaterVals.append(heaterVal)

    print heaterVals

    ax.plot(heaterVals, slopes, marker="o", linestyle="None",
            label="Projected Data")
    ax.legend(loc="lower left")

    minHeatProjected = min(heaterVals)
    minCalibrationHeat = min(calibrationVals)

    if minHeatProjected < minCalibrationHeat:
        yRange = linspace(minHeatProjected, minCalibrationHeat)
        ax.plot(yRange, [m * i + b for i in yRange])

    maxHeatProjected = max(heaterVals)
    maxCalibrationHeat = max(calibrationVals)

    if maxHeatProjected > maxCalibrationHeat:
        yRange = linspace(maxCalibrationHeat, maxHeatProjected)
        ax.plot(yRange, [m * i + b for i in yRange])

    for heatLoad in heaterVals:
        print calcQ0(18.0, heatLoad)

    plt.draw()

    # for i in plt.get_fignums():
    #     plt.figure(i)
    #     plt.savefig("figure%d.png" % i)

    plt.show()


def buildDatetimeFromInput(prompt):
    now = datetime.now()
    year = get_int("Year " + prompt, True, 2019, now.year)

    month = get_int("Month " + prompt, True, 1,
                    now.month if year == now.year else 12)

    day = get_int("Day " + prompt, True, 1,
                  now.day if (year == now.year and month == now.month) else 31)

    hour = get_int("Hour " + prompt, True, 0, 23)
    minute = get_int("Minute " + prompt, True, 0, 59)

    return datetime(year, month, day, hour, minute)


def buildCalibFile():
    print ("\n***Now we'll start building a calibration file " +
           "- please be patient***\n")

    startTimeCalib = buildDatetimeFromInput("calibration run began: ")
    endTimeCalib = buildDatetimeFromInput("calibration run ended: ")

    cryoModuleObj = Cryomodule(cryomoduleSLAC, cryomoduleLERF, None,
                               valveLockedPos, refHeaterVal)

    cryoModuleObj.calFileName = generateCSV(startTimeCalib,
                                            endTimeCalib,
                                            cryoModuleObj.getPVs(),
                                            cryomoduleSLAC)

    return cryoModuleObj


def findDataFiles(prefix):
    fileDict = {}
    # If the directory is empty
    numFiles = 1
    for root, dirs, files in walk(abspath(dirname(__file__))):
        for idx, name in enumerate(files):
            if fnmatch(name, prefix + "*"):
                fileDict[numFiles] = name
                # fileDict[idx] = join(root, name)
                numFiles += 1
    fileDict[numFiles] = "Generate a new CSV"
    return fileDict


def getQ0Measurements():
    global refHeaterVal, valveLockedPos, cryomoduleSLAC, cryomoduleLERF
    refHeaterVal = 1.91
    valveLockedPos = 17.5
    calValvePosTol = 1.2
    cryomoduleSLAC = 12
    cryomoduleLERF = 2
    # refHeaterVal = get_float("Reference Heater Value: ", True, 0, 15)
    # valveLockedPos = get_float("JT Valve locked position: ", True, 0, 100)
    #
    # cryomoduleSLAC = get_int("SLAC Cryomodule Number: ", True, 1, 33)
    # cryomoduleLERF = get_int("LERF Cryomodule Number: ", True, 2, 3)

    print "\n---------- CRYOMODULE " + str(cryomoduleSLAC) + " ----------\n"

    calibFiles = findDataFiles("calib_CM" + str(cryomoduleSLAC))

    print "Options for Calibration Data:"

    print "\n" + dumps(calibFiles, indent=4) + "\n"

    option = get_int("Please choose one of the options above: ", True, 1,
                     len(calibFiles))

    if option == len(calibFiles):
        cryoModuleObj = buildCalibFile()

    else:
        cryoModuleObj = Cryomodule(cryomoduleSLAC, cryomoduleLERF,
                                   calibFiles[option], valveLockedPos,
                                   refHeaterVal)

    m, b, ax, calibrationVals = processCalibrationData(cryoModuleObj,
                                                       calValvePosTol)

    numCavs = get_int("Number of cavities to analyze: ", True, 0, 8)

    cavities = []
    for _ in xrange(numCavs):
        cavity = get_int("Next cavity to analyze: ", True, 1, 8)
        while cavity in cavities:
            cavity = get_int("Please enter a cavity not previously entered: ",
                             True, 1, 8)
        cavities.append(cavity)

    for cav in cavities:
        print "\n---------- CAVITY " + str(cav) + " ----------\n"
        # 0 indexing means we have to subtract 1 from the cavity number
        cavityObj = cryoModuleObj.cavities[cav - 1]

        q0MeasFiles = findDataFiles("q0meas_CM" + str(cryomoduleSLAC) + "_cav"
                                    + str(cav))

        print "Options for Q0 Meaurement Data:"

        print "\n" + dumps(q0MeasFiles, indent=4) + "\n"

        option = get_int("Please choose one of the options above: ", True, 1,
                         len(q0MeasFiles))

        if option == len(q0MeasFiles):
            print "not implemented"
            return

        else:
            cavityObj.q0MeasFileName = q0MeasFiles[option]

        slopes = processQ0MeasData(cavityObj, calValvePosTol)

        heaterVals = []

        for dLL in slopes:
            heaterVal = (dLL - b) / m
            heaterVals.append(heaterVal)

        ax.plot(heaterVals, slopes, marker="o", linestyle="None",
                label="Projected Data for Cavity " + str(cav))
        ax.legend(loc="lower left")

        minHeatProjected = min(heaterVals)
        minCalibrationHeat = min(calibrationVals)

        if minHeatProjected < minCalibrationHeat:
            yRange = linspace(minHeatProjected, minCalibrationHeat)
            ax.plot(yRange, [m * i + b for i in yRange])

        maxHeatProjected = max(heaterVals)
        maxCalibrationHeat = max(calibrationVals)

        if maxHeatProjected > maxCalibrationHeat:
            yRange = linspace(maxCalibrationHeat, maxHeatProjected)
            ax.plot(yRange, [m * i + b for i in yRange])

        for heatLoad in heaterVals:
            print "Calculated Heat Load: " + str(heatLoad)
            print "    Q0: " + str(calcQ0(16.05, heatLoad))

    plt.draw()
    plt.show()


if __name__ == "__main__":
    # demo()
    getQ0Measurements()
