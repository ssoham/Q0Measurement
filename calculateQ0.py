################################################################################
# A utility used to calculate Q0 for a set of cavities in a given cryomodules
# using the change in 2K helium liquid level (LL) per unit time
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import division, print_function

from collections import OrderedDict
from json import dumps
from csv import reader
from time import sleep

from matplotlib import pyplot as plt
from numpy import linspace
from sys import stderr
from cryomodule import (Cryomodule, Container, Cavity, DataSession,
                        Q0DataSession)
from runQ0Measurement import runQ0Meas


ERROR_MESSAGE = "Please provide valid input"

# Trying to make this compatible with both 2.7 and 3 (input in 3 is the same as
# raw_input in 2.7, but input in 2.7 calls evaluate)
# This somehow broke having it in a separate util file, so moved it here
if hasattr(__builtins__, 'raw_input'):
    input = raw_input


def get_float_lim(prompt, low_lim, high_lim):
    return getNumericalInput(prompt, low_lim, high_lim, float)


def getNumInputFromLst(prompt, lst, inputType):
    response = get_input(prompt, inputType)
    while response not in lst:
        stderr.write(ERROR_MESSAGE + "\n")
        # Need to pause briefly for some reason to make sure the error message
        # shows up before the next prompt
        sleep(0.01)
        response = get_input(prompt, inputType)

    return response


def getNumericalInput(prompt, lowLim, highLim, inputType):
    response = get_input(prompt, inputType)

    while response < lowLim or response > highLim:
        stderr.write(ERROR_MESSAGE + "\n")
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


def getStrLim(prompt, acceptable_strings):
    response = get_input(prompt, str)

    while response not in acceptable_strings:
        stderr.write(ERROR_MESSAGE + "\n")
        sleep(0.01)
        response = get_input(prompt, str)

    return response


def genCalibSession(cryModIdxMap, calIdxKeys, slacNum, cryoModules,
                    calibIdx=None):

    populateIdxMap("calibrationsCM{CM_SLAC}.csv", cryModIdxMap, calIdxKeys,
                   slacNum)

    if calibIdx:
        calibSession = addDataSessionAdv("calibrationsCM{CM_SLAC}.csv",
                                         cryModIdxMap[slacNum], slacNum, None,
                                         calibIdx)

    else:
        calibSession = addDataSession("calibrationsCM{CM_SLAC}.csv",
                                      cryModIdxMap[slacNum], slacNum, None)

    cryoModules[slacNum] = calibSession.container
    calibSession.generateCSV()
    calibSession.processData()

    return calibSession


def genQ0Session(addDataSessionFunc, dataSessionFuncParam, cavIdxMap, slacNum,
                 cavity, calibSession, cavIdxKeys):
    # type: (callable, float, dict, int, Cavity, DataSession, []) -> None
    if slacNum not in cavIdxMap:
        populateIdxMap("q0MeasurementsCM{CM_SLAC}.csv",
                       cavIdxMap, cavIdxKeys, slacNum)

    sessionQ0 = addDataSessionFunc("q0MeasurementsCM{CM_SLAC}.csv",
                                   cavIdxMap[slacNum], slacNum, cavity,
                                   dataSessionFuncParam, calibSession)

    sessionQ0.generateCSV()
    sessionQ0.processData()

    print("\n---------- {CM} {CAV} ----------\n"
          .format(CM=calibSession.container.name, CAV=cavity.name))
    sessionQ0.printReport()

    updateCalibCurve(calibSession.heaterCalibAxis, sessionQ0,
                     calibSession)


def parseInputFile(inputFile):
    csvReader = reader(open(inputFile))
    header = csvReader.next()
    slacNumIdx = header.index("SLAC Cryomodule Number")

    dataSessions = {}
    cryModIdxMap = {}
    cavIdxMap = {}
    cryoModules ={}

    baseIdxKeys = [("startIdx", "Start"), ("endIdx", "End"),
                   ("refHeatIdx", "Reference Heat Load"),
                   ("jtIdx", "JT Valve Position"),
                   ("timeIntIdx", "MySampler Time Interval")]

    cavIdxKeys = baseIdxKeys + [("cavNumIdx", "Cavity"),
                                ("gradIdx", "Gradient"),
                                ("rfIdx", "RF Heat Load"),
                                ("elecIdx", "Electric Heat Load")]

    calIdxKeys = baseIdxKeys + [("jlabNumIdx", "JLAB Number")]

    figStartIdx = 1

    if "Adv" in inputFile or "Demo" in inputFile:
        cryModFileIdx = header.index("Calibration Index")

        for row in csvReader:
            slacNum = int(row[slacNumIdx])
            calibIdx = int(row[cryModFileIdx])

            if slacNum not in dataSessions:
                calibSession = genCalibSession(cryModIdxMap, calIdxKeys,
                                               slacNum, cryoModules, calibIdx)
                dataSessions[slacNum] = {calibIdx: calibSession}

            else:
                if calibIdx not in dataSessions[slacNum]:

                    calibSession = genCalibSession(cryModIdxMap, calIdxKeys,
                                                   slacNum, cryoModules,
                                                   calibIdx)

                    dataSessions[slacNum][calibIdx] = calibSession

                calibSession = dataSessions[slacNum][calibIdx]

            cryoModule = cryoModules[slacNum]
            print("\n---------- {CM} ----------\n".format(CM=cryoModule.name))

            for _, cavity in cryoModule.cavities.items():
                cavIdx = header.index("Cavity {NUM} Index"
                                      .format(NUM=cavity.cavNum))
                try:
                    cavIdx = int(row[cavIdx])

                    genQ0Session(addDataSessionAdv, cavIdx, cavIdxMap, slacNum,
                                 cavity, calibSession, cavIdxKeys)

                except ValueError:
                    pass

            lastFigNum = len(plt.get_fignums()) + 1
            for i in range(figStartIdx, lastFigNum):
                plt.figure(i)
                plt.savefig("figures/{CM}_{FIG}.png".format(CM=cryoModule.name,
                                                            FIG=i))
            figStartIdx = lastFigNum

        plt.draw()
        plt.show()

    else:
        for row in csvReader:
            slacNum = int(row[slacNumIdx])

            if slacNum not in cryoModules:
                calibSession = genCalibSession(cryModIdxMap, calIdxKeys,
                                               slacNum, cryoModules)

            else:
                options = {}
                idx = 1
                idx2session = {}

                for _, dataSession in cryoModules[slacNum].dataSessions.items():
                    options[idx] = str(dataSession)
                    idx2session[idx] = dataSession
                    idx += 1

                options[idx] = "Use a different calibration"
                printOptions(options)

                selection = getNumInputFromLst(("Please select a calibration"
                                                " option: "), options.keys(),
                                               int)

                reuseCalibration = (selection != max(options))

                if not reuseCalibration:
                    # TODO cryModIdxMap or cryModIdxMap[slacNum]?
                    calibSession = genCalibSession(cryModIdxMap, calIdxKeys,
                                                   slacNum, cryoModules)

                else:
                    calibSession = idx2session[selection]

            cryoModule = cryoModules[slacNum]
            print("\n---------- {CM} ----------\n".format(CM=cryoModule.name))

            for _, cavity in cryoModule.cavities.items():
                cavGradIdx = header.index("Cavity {NUM} Gradient"
                                          .format(NUM=cavity.cavNum))

                try:
                    gradDes = float(row[cavGradIdx])

                    print("\n---- Cavity {CAV} @ {GRAD} MV/m ----"
                          .format(CM=slacNum, CAV=cavity.cavNum, GRAD=gradDes))

                    genQ0Session(addDataSession, gradDes, cavIdxMap, slacNum,
                                 cavity, calibSession, cavIdxKeys)

                # If blank
                except ValueError:
                    pass

            lastFigNum = len(plt.get_fignums()) + 1
            for i in range(figStartIdx, lastFigNum):
                plt.figure(i)
                plt.savefig("figures/{CM}_{FIG}.png".format(CM=cryoModule.name,
                                                            FIG=i))
            figStartIdx = lastFigNum

        plt.draw()
        plt.show()


def updateCalibCurve(calibCurveAxis, q0Session, calibSession):
    # type: (object, Q0DataSession, DataSession) -> None

    calibCurveAxis.plot(q0Session.runHeatLoads,
                        q0Session.adjustedRunSlopes,
                        marker="o", linestyle="None",
                        label="Projected Data for " + q0Session.container.name)

    calibCurveAxis.legend(loc='best', shadow=True, numpoints=1)

    minCavHeatLoad = min(q0Session.runHeatLoads)
    minCalibHeatLoad = min(calibSession.runElecHeatLoads)

    if minCavHeatLoad < minCalibHeatLoad:
        yRange = linspace(minCavHeatLoad, minCalibHeatLoad)
        calibCurveAxis.plot(yRange, [calibSession.calibSlope * i
                                     + calibSession.calibIntercept
                                     for i in yRange])

    maxCavHeatLoad = max(q0Session.runHeatLoads)
    maxCalibHeatLoad = max(calibSession.runElecHeatLoads)

    if maxCavHeatLoad > maxCalibHeatLoad:
        yRange = linspace(maxCalibHeatLoad, maxCavHeatLoad)
        calibCurveAxis.plot(yRange, [calibSession.calibSlope * i
                                     + calibSession.calibIntercept
                                     for i in yRange])


def printOptions(options):
    print(("\n" + dumps(options, indent=4) + "\n")
          .replace('"', '').replace(',', ''))


def addDataSession(fileFormatter, indices, slacNum, container,
                   refGradVal=None, calibSession=None):
    # type: (str, dict, int, Container, float, DataSession) -> DataSession

    def addOption(row, lineNum):
        startTime = Container.makeTimeFromStr(row, indices["startIdx"])
        endTime = Container.makeTimeFromStr(row, indices["endIdx"])
        rate = row[indices["timeIntIdx"]]
        options[lineNum] = ("{START} to {END} ({RATE}s sample interval)"
                            .format(START=startTime, END=endTime,
                                    RATE=rate))

    def getSelection(duration, suffix):
        options[max(options) + 1] = ("Launch new {TYPE} ({DUR} hours)"
                                     .format(TYPE=suffix, DUR=duration))
        printOptions(options)
        return getNumInputFromLst(("Please select a {TYPE} option: "
                                        .format(TYPE=suffix)), options.keys(),
                                       int)

    file = fileFormatter.format(CM_SLAC=slacNum)
    rows = open(file).readlines()
    rows.reverse()
    reader([rows.pop()]).next()

    fileReader = reader(rows)
    options = OrderedDict()

    if isinstance(container, Cavity):
        for row in fileReader:

            if (len(options) + 1) % 10 == 0:
                printOptions(options)
                showMore = (getStrLim("Search for more options? ",
                                      ["y", "n", "Y", "N"]) in ["y", "Y"])
                if not showMore:
                    break

            grad = float(row[indices["gradIdx"]])
            cavNum = int(row[indices["cavNumIdx"]])

            if (grad != refGradVal) or (cavNum != container.cavNum):
                continue

            addOption(row, fileReader.line_num)

        selection = getSelection(2, "Q0 Measurement")

        if selection != max(options):
            selectedRow = reader([rows[selection - 1]]).next()
            return container.addDataSessionFromRow(selectedRow, indices,
                                                   calibSession, refGradVal)

        else:
            # TODO this currently always waits for cryo before starting
            return runQ0Meas(container, refGradVal, calibSession)

    else:
        for row in fileReader:

            if (len(options) + 1) % 10 == 0:
                printOptions(options)
                showMore = (getStrLim("Search for more options? ",
                                        ["y", "n", "Y", "N"]) in ["y", "Y"])
                if not showMore:
                    break

            addOption(row, fileReader.line_num)

        selection = getSelection(5, "calibration")

        if selection != max(options):

            calibRow = reader([rows[selection - 1]]).next()

            if not container:
                container = Cryomodule(slacNum, calibRow[indices["jlabNumIdx"]])

            return container.addDataSessionFromRow(calibRow, indices)

        else:
            # TODO launch new calibration
            pass


def addDataSessionAdv(fileFormatter, indices, slacNum, container, idx,
                      calibSession=None):
    # type: (str, dict, int, int, Container, DataSession) -> DataSession

    file = fileFormatter.format(CM_SLAC=slacNum)
    row = open(file).readlines()[idx - 1]
    selectedRow = reader([row]).next()

    if not container:
        jlabIdx = indices["jlabNumIdx"]
        container = Cryomodule(cryModNumSLAC=slacNum,
                               cryModNumJLAB=int(selectedRow[jlabIdx]))

    return container.addDataSessionFromRow(selectedRow, indices, calibSession)


def populateIdxMap(fileFormatter, idxMap, idxkeys, slacNum):
    file = fileFormatter.format(CM_SLAC=slacNum)
    with open(file) as csvFile:
        csvReader = reader(csvFile)
        header = csvReader.next()
        indices = {}
        for key, column in idxkeys:
            indices[key] = header.index(column)
        idxMap[slacNum] = indices


if __name__ == "__main__":
    parseInputFile("input.csv")
