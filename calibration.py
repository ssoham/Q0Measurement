from __future__ import division
from csv import reader, writer
from datetime import datetime
import subprocess
import re
import os
from matplotlib import pyplot as plt
from numpy import mean, std, polyfit, linspace
from sys import maxint, stderr
from scipy.stats import linregress
import user_input


# Could probably figure out a way to use numpy arrays if I get the line count
# from the CSV
time = []
unixTime = []
valvePos = []
flowRate = []
heatLoad = []
downstreamLevel = []
upstreamLevel = []


def parseData(fileName, cryoModule, cavity):

    def genHeader(prefix, suffix):
        return prefix + cryoModule + suffix

    with open(fileName) as csvFile:

        csvReader = reader(csvFile)
        header = csvReader.next()

        columnDict = {}

        for buff, col in [(unixTime, "Unix time"),
                          (valvePos, genHeader("CPV:CM0", ":3001:JT:POS_RBV")),
                          (flowRate, "CFICM0312"),
                          (heatLoad, genHeader("CHTR:CM0", ":1" + cavity
                                                           + "55:HV:POWER")),
                          (downstreamLevel, genHeader("CLL:CM0", ":2301:DS:LVL")),
                          (upstreamLevel, genHeader("CLL:CM0", ":2601:US:LVL"))]:
            try:
                columnDict[col] = {"idx": header.index(col), "buffer": buff}

            except ValueError:
                print >> stderr, "Column " + col + " not found in CSV"

        timeIdx = header.index("time")
        timeZero = datetime.utcfromtimestamp(0)

        for row in csvReader:

            time.append((datetime.strptime(row[timeIdx], "%Y-%m-%d %H:%M:%S")
                         - timeZero).total_seconds())
                                          
            for col, idxBuffDict in columnDict.iteritems():
                idxBuffDict["buffer"].append(float(row[idxBuffDict["idx"]]))


###############################################################################
# Analyzing change in downstream liquid level vs heat load (we're not using the
# mass flow rate because SLAC doesn't have that particular diagnostic)
#
# CAVEAT: This only works if we refill to the same level before every run (in
# this case, we refilled to 97%)
###############################################################################
def getLiquidLevelChange(dataFile, cryoModule, refHeaterVal, refValvePos,
                         valveTolerance, isCalibration, cavity, cutoff=1000):
    parseData(dataFile, cryoModule, cavity)
    
    # The readings get wonky when the upstream liquid level dips below 66, and
    # when the  valve position is +/- 1.2 from our locked position (found
    # empirically)
    runs, timeRuns, heaterVals = populateRuns(heatLoad, downstreamLevel, 66,
                                              refValvePos, valveTolerance,
                                              refHeaterVal, cutoff)
                                 
    print "Heat Loads: " + str(heaterVals)
    adjustForHeaterSettle(heaterVals, runs, timeRuns)
    
    for timeRun in timeRuns:
        print "Duration of run: " + str((timeRun[-1] - timeRun[0])/60.0)
    
    ax1 = genAxis("Liquid Level as a Function of Time",
                  "Unix Time (s)", "Downstream Liquid Level (%)")

    slopes = []

    for idx, run in enumerate(runs):
        m, b, r_val, p_val, std_err = linregress(timeRuns[idx], run)
        print r_val**2

        slopes.append(m)

        ax1.plot(timeRuns[idx], run, label=(str(round(m, 6)) + "%/s @ "
                                            + str(heaterVals[idx]) + " W"))

        ax1.plot(timeRuns[idx], [m*x + b for x in timeRuns[idx]])

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

    
# Analyzing mass flow rate vs heat load
def getAvgMassFlow():
    parseData("data_new.csv", "3")
    
    # The liquid level was constant and the JT valve position was changing for
    # this test, so we put conditions that are never met in order to bypass them
    runs, timeRuns, heaterVals = populateRuns(heatLoad, flowRate, 0,
                                              calValveLockedPos, maxint)

    print "Heat loads: " + str(heaterVals)
    adjustForHeaterSettle(heaterVals, runs, timeRuns)

    ax = genAxis("Average Flow Rate as a Function of Heat Load", "Time (s)",
                 "Flow Rate")

    slopes = []
    for idx, run in enumerate(runs):
        m, b = polyfit(timeRuns[idx], run, 1)
        slopes.append(m)
        ave = mean(run)

        print "Average: " + str(ave)
        print "Standard Deviation: " + str(std(run))

        ax.plot(timeRuns[idx], run, label=(str(ave) + " @ "
                                           + str(heaterVals[idx]) + " W"))

        ax.plot(timeRuns[idx], [ave for _ in timeRuns[idx]])

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


def populateRuns(inputBuffer, outputBuffer, levelLimit, refValvePos,
                 valvePosTolerance, adjustment=0.0, cutoff=1000):

    def appendToBuffers(dataBuffers, startIdx, endIdx):
        for (runBuffer, dataBuffer) in dataBuffers:
            runBuffer.append(dataBuffer[startIdx: endIdx])

    runStartIdx = 0

    runs = []
    timeRuns = []
    inputVals = []
    
    for idx, val in enumerate(inputBuffer):

        prevInputVal = inputBuffer[idx - 1] if idx > 0 else val

        # A "break" condition defining the end of a run
        if (val != prevInputVal
                or upstreamLevel[idx] < levelLimit
                or abs(valvePos[idx] - refValvePos) > valvePosTolerance
                or idx == len(inputBuffer) - 1):

            # Keeping only those runs with at least <cutoff> points
            if idx - runStartIdx > cutoff:
                inputVals.append(prevInputVal - adjustment)
                appendToBuffers([(runs, outputBuffer), (timeRuns, time)],
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
    return subprocess.check_output(cmd)


def reformatDate(row):
    try:
        regex = re.compile(
            "[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}")
        res = re.findall(regex, row)[0].replace(" ", "-")
        reformattedRow = regex.sub(res, row)
        return "\t".join(reformattedRow.strip().split())
    except IndexError:
        print row
        return "\t".join(row.strip().split())


def generateCSV(startTime, endTime, signals, cryomodule, cavity=0, calib=True):
    # startTime & endTime are datetime objects, signals is a list of PV names
    nSecs = int((endTime - startTime).total_seconds())
    rawData = getArchiveData(startTime, nSecs, signals)
    rows = list(map(lambda x: reformatDate(x), rawData.splitlines()))
    reader = csv.reader(rows, delimiter='\t')
    # Define a file name for the CSV we're saving. There are calibration files
    # and q0 measurement files. Both include a time stamp in the format
    # year-month-day--hour-minute. They also indicate the number of data points.
    if calib == True:
        fileName = ('calib_' + startTime.strftime("%Y-%m-%d--%H-%M_")
                    + str(nSecs) + '_CM' + str(cryomodule) + '.csv')
    else:
        fileName = ('q0meas_' + startTime.strftime("%Y-%m-%d--%H-%M_")
                    + '_CM' + str(cryomodule) + '_cav' + str(cavity) + '.csv')
    if os.path.isfile(fileName):
        response = user_input.get_str('Overwrite previous CSV file (y/n)? ',
                                      True, ['y', 'n'])
        if response is 'n':
            return
    with open(fileName, 'wb') as f:
        writer = csv.writer(f, delimiter='\t')
        for row in reader:
            writer.writerow(row)


calValveLockedPos = 17
calRefHeaterVal = 1.91
calValvePosTol = 1.2

m, b, ax, calibrationVals = getLiquidLevelChange("calib_2019-02-25--11-25_18672_CM12.csv",
                                                 "2", calRefHeaterVal,
                                                 calValveLockedPos, calValvePosTol, True,
                                                 "1")

del time[:]
del unixTime[:]
del valvePos[:]
del flowRate[:]
del heatLoad[:]
del downstreamLevel[:]
del upstreamLevel[:]

# refHeaterVal = float(raw_input("Reference Heater Value: "))
# valveLockedPos = float(raw_input("JT Valve locked position: "))
# valvePosTolerance = float(raw_input("JT Valve position tolerance: "))

refHeaterVal = 1.91
valveLockedPos = 17.5
valvePosTol = 1

slopes = getLiquidLevelChange("3_3_2019_1.csv", "2", refHeaterVal,
                              valveLockedPos, valvePosTol, False, "2", 500)

# slopes = getAvgMassFlow()

heaterVals = []
for dLL in slopes:
    heaterVal = (dLL - b)/m
    heaterVals.append(heaterVal)

print heaterVals

ax.plot(heaterVals, slopes, marker="o", linestyle="None",
        label="Projected Data")
ax.legend(loc="lower left")

minHeatProjected = min(heaterVals)
minCalibrationHeat = min(calibrationVals)

if minHeatProjected < minCalibrationHeat:
    yRange = linspace(minHeatProjected, minCalibrationHeat)
    ax.plot(yRange, [m * x + b for x in yRange])

maxHeatProjected = max(heaterVals)
maxCalibrationHeat = max(calibrationVals)

if maxHeatProjected > maxCalibrationHeat:
    yRange = linspace(maxCalibrationHeat, maxHeatProjected)
    ax.plot(yRange, [m * x + b for x in yRange])
    
for heatLoad in heaterVals:
    print calcQ0(18.0, heatLoad)

plt.draw()

for i in plt.get_fignums():
    plt.figure(i)
    plt.savefig("figure%d.png" % i)

plt.show()
