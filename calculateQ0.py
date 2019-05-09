################################################################################
# A utility used to calculate Q0 for a set of cavities in a given cryomodules
# using the change in 2K helium liquid level (LL) per unit time
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import division, print_function
from collections import OrderedDict
from json import dumps
from csv import reader
from matplotlib import pyplot as plt
from matplotlib.axes import Axes
from numpy import linspace
from container import Cryomodule, Container, Cavity, DataSession, Q0DataSession
from runCalibration import runCalibration
from runQ0Measurement import runQ0Meas
from utils import getNumInputFromLst, getYesNo


def addToCryMod(cryModIdxMap, slacNum, cryoModule, calibIdx=None,
                refValvePos=None):
    # type: (dict, int, Cryomodule, int, float) -> (DataSession, float)

    if calibIdx:
        calibSess = addDataSessionAdv("calibrationsCM{CM_SLAC}.csv",
                                      cryModIdxMap[slacNum], slacNum,
                                      cryoModule, calibIdx)

    else:
        calibSess, refValvePos = addDataSession("calibrationsCM{CM_SLAC}.csv",
                                                cryModIdxMap[slacNum], slacNum,
                                                cryoModule,
                                                refValvePos=refValvePos)

    calibSess.generateCSV()
    calibSess.processData()

    return calibSess, refValvePos


# noinspection PyTypeChecker
def addNewCryMod(cryModIdxMap, calIdxKeys, slacNum, cryoModules, calibIdx=None,
                 refValvePos=None):
    # type: (dict, [], int, dict, int, float) -> (DataSession, float)

    populateIdxMap("calibrationsCM{CM_SLAC}.csv", cryModIdxMap, calIdxKeys,
                   slacNum)

    if calibIdx:
        calibSess, _ = addDataSessionAdv("calibrationsCM{CM_SLAC}.csv",
                                         cryModIdxMap[slacNum], slacNum, None,
                                         calibIdx)

    else:
        calibSess, refValvePos = addDataSession("calibrationsCM{CM_SLAC}.csv",
                                                cryModIdxMap[slacNum], slacNum,
                                                None, refValvePos=refValvePos)

    cryoModules[slacNum] = calibSess.container
    calibSess.generateCSV()
    calibSess.processData()

    return calibSess, refValvePos


def genQ0Session(addDataSessionFunc, dataSessionFuncParam, cavIdxMap, slacNum,
                 cavity, calibSession, cavIdxKeys, refValvePos=None):
    # type: (callable, float, dict, int, Cavity, DataSession, [], float) -> float
    if slacNum not in cavIdxMap:
        populateIdxMap("q0MeasurementsCM{CM_SLAC}.csv", cavIdxMap, cavIdxKeys,
                       slacNum)

    sessionQ0, refValvePos = addDataSessionFunc("q0MeasurementsCM{CM_SLAC}.csv",
                                                cavIdxMap[slacNum], slacNum,
                                                cavity, dataSessionFuncParam,
                                                calibSession, refValvePos)

    sessionQ0.generateCSV()
    sessionQ0.processData()

    print("\n---------- {CM} {CAV} ----------\n"
          .format(CM=calibSession.container.name, CAV=cavity.name))
    sessionQ0.printReport()

    updateCalibCurve(calibSession.heaterCalibAxis, sessionQ0,
                     calibSession)

    return refValvePos


def parseInputFile(inputFile):
    csvReader = reader(open(inputFile))
    header = csvReader.next()
    slacNumIdx = header.index("SLAC Cryomodule Number")

    dataSessions = {}
    cryModIdxMap = {}
    cavIdxMap = {}
    cryoModules = {}

    baseIdxKeys = [("startIdx", "Start"), ("endIdx", "End"),
                   ("refHeatIdx", "Reference Heat Load"),
                   ("jtIdx", "JT Valve Position"),
                   ("timeIntIdx", "MySampler Time Interval")]

    cavIdxKeys = baseIdxKeys + [("cavNumIdx", "Cavity"),
                                ("gradIdx", "Gradient")]

    calIdxKeys = baseIdxKeys + [("jlabNumIdx", "JLAB Number")]

    figStartIdx = 1

    refValvePos = None

    if "Adv" in inputFile or "Demo" in inputFile:
        cryModFileIdx = header.index("Calibration Index")

        for row in csvReader:
            slacNum = int(row[slacNumIdx])
            calibIdx = int(row[cryModFileIdx])

            if slacNum not in dataSessions:
                calibSess, _ = addNewCryMod(cryModIdxMap, calIdxKeys,
                                            slacNum, cryoModules, calibIdx)
                dataSessions[slacNum] = {calibIdx: calibSess}

            else:
                if calibIdx not in dataSessions[slacNum]:
                    calibSess, _ = addToCryMod(cryModIdxMap, slacNum,
                                               cryoModules[slacNum], calibIdx)

                    # calibSess = addNewCryMod(cryModIdxMap, calIdxKeys,
                    #                             slacNum, cryoModules,
                    #                             calibIdx)

                    dataSessions[slacNum][calibIdx] = calibSess

                calibSess = dataSessions[slacNum][calibIdx]

            cryoModule = cryoModules[slacNum]
            print("\n---------- {CM} ----------\n".format(CM=cryoModule.name))

            for _, cavity in cryoModule.cavities.items():
                cavIdx = header.index("Cavity {NUM} Index"
                                      .format(NUM=cavity.cavNum))
                try:
                    cavIdx = int(row[cavIdx])

                    genQ0Session(addDataSessionAdv, cavIdx, cavIdxMap, slacNum,
                                 cavity, calibSess, cavIdxKeys)

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
                calibSess, refValvePos = addNewCryMod(cryModIdxMap, calIdxKeys,
                                                      slacNum, cryoModules,
                                                      refValvePos=refValvePos)

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
                    calibSess, refValvePos = addToCryMod(cryModIdxMap, slacNum,
                                                         cryoModules[slacNum],
                                                         refValvePos=refValvePos)
                    # calibSess = addNewCryMod(cryModIdxMap, calIdxKeys,
                    #                             slacNum, cryoModules)

                else:
                    calibSess = idx2session[selection]

            cryoModule = cryoModules[slacNum]
            print("\n---------- {CM} ----------\n".format(CM=cryoModule.name))

            for _, cavity in cryoModule.cavities.items():
                cavGradIdx = header.index("Cavity {NUM} Gradient"
                                          .format(NUM=cavity.cavNum))

                try:
                    gradDes = float(row[cavGradIdx])

                    print("\n---- Cavity {CAV} @ {GRAD} MV/m ----"
                          .format(CM=slacNum, CAV=cavity.cavNum, GRAD=gradDes))

                    refValvePos = genQ0Session(addDataSession, gradDes,
                                               cavIdxMap, slacNum, cavity,
                                               calibSess, cavIdxKeys,
                                               refValvePos)

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
    # type: (Axes, Q0DataSession, DataSession) -> None

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
                   refGradVal=None, calibSession=None, refValvePos=None):
    # type: (str, dict, int, Container, float, DataSession, float) -> (DataSession, float)

    def addOption(csvRow, lineNum):
        startTime = Container.makeTimeFromStr(csvRow, indices["startIdx"])
        endTime = Container.makeTimeFromStr(csvRow, indices["endIdx"])
        rate = csvRow[indices["timeIntIdx"]]
        options[lineNum] = ("{START} to {END} ({RATE}s sample interval)"
                            .format(START=startTime, END=endTime,
                                    RATE=rate))

    def getSelection(duration, suffix):
        options[max(options) + 1] = ("Launch new {TYPE} ({DUR} hours)"
                                     .format(TYPE=suffix, DUR=duration))
        printOptions(options)
        return getNumInputFromLst(("Please select a {TYPE} option: "
                                   .format(TYPE=suffix)), options.keys(), int)

    sessionCSV = fileFormatter.format(CM_SLAC=slacNum)
    rows = open(sessionCSV).readlines()
    rows.reverse()
    reader([rows.pop()]).next()

    fileReader = reader(rows)
    options = OrderedDict()

    if isinstance(container, Cavity):
        for row in fileReader:

            if (len(options) + 1) % 10 == 0:
                printOptions(options)
                showMore = getYesNo("Search for more options? ")
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
            return (container.addDataSessionFromRow(selectedRow, indices,
                                                    calibSession, refGradVal),
                    None)

        else:
            return runQ0Meas(container, refGradVal, calibSession, refValvePos)

    else:
        for row in fileReader:

            if (len(options) + 1) % 10 == 0:
                printOptions(options)
                showMore = getYesNo("Search for more options? ")
                if not showMore:
                    break

            addOption(row, fileReader.line_num)

        selection = getSelection(5, "calibration")

        if selection != max(options):

            calibRow = reader([rows[selection - 1]]).next()

            if not container:
                container = Cryomodule(slacNum, calibRow[indices["jlabNumIdx"]])

            return container.addDataSessionFromRow(calibRow, indices), None

        else:
            if not container:
                container = Cryomodule(slacNum,
                                       getNumInputFromLst("JLab cryomodule "
                                                          "number: ", [2, 3],
                                                          int))

            return runCalibration(container, refValvePos)


def addDataSessionAdv(fileFormatter, indices, slacNum, container, idx,
                      calibSession=None, refValvePos=None):
    # type: (str, dict, int, Container, int, DataSession, float) -> (DataSession, float)

    sessionCSV = fileFormatter.format(CM_SLAC=slacNum)
    row = open(sessionCSV).readlines()[idx - 1]
    selectedRow = reader([row]).next()

    if not container:
        jlabIdx = indices["jlabNumIdx"]
        container = Cryomodule(cryModNumSLAC=slacNum,
                               cryModNumJLAB=int(selectedRow[jlabIdx]))

    return (container.addDataSessionFromRow(selectedRow, indices, calibSession),
            None)


def populateIdxMap(fileFormatter, idxMap, idxkeys, slacNum):
    sessionCSV = fileFormatter.format(CM_SLAC=slacNum)
    with open(sessionCSV) as csvFile:
        csvReader = reader(csvFile)
        header = csvReader.next()
        indices = {}
        for key, column in idxkeys:
            indices[key] = header.index(column)
        idxMap[slacNum] = indices


if __name__ == "__main__":
    parseInputFile("inputAdv.csv")
