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
from utils import getNumInputFromLst, getYesNo, TEST_MODE


def parseInputFile(inputFile):
    csvReader = reader(open(inputFile))
    header = csvReader.next()
    slacNumIdx = header.index("SLAC Cryomodule Number")

    # A dict of dicts where the format is:
    #   {[SLAC cryomodule number]:
    #       {[line number from record-keeping CSV]: [DataSession object]}}
    dataSessions = {}

    # Dicts of dicts where the format is:
    #   {[SLAC cryomodule number]:
    #       {[column name shorthand]: [index in the relevant CSV header]}}
    cryModIdxMap = {}
    cavIdxMap = {}

    # A dict of the form {[SLAC cryomodule number]: [Cryomodule Object]}
    cryoModules = {}

    # Used to populate cryModIdxMap and cavIdxMap. Each tuple in the list is of
    # the form: ([column name shorthand], [column title in the CSV])
    baseIdxKeys = [("startIdx", "Start"), ("endIdx", "End"),
                   ("refHeatIdx", "Reference Heat Load"),
                   ("jtIdx", "JT Valve Position"),
                   ("timeIntIdx", "MySampler Time Interval")]

    cavIdxKeys = baseIdxKeys + [("cavNumIdx", "Cavity"),
                                ("gradIdx", "Gradient")]

    calIdxKeys = baseIdxKeys + [("jlabNumIdx", "JLAB Number")]

    figStartIdx = 1

    # We store the JT Valve position from the first time that we run
    # getRefValvePos (in Container) so that we don't have to rerun that
    # function every time we get new data (each call takes 2 hours)
    refValvePos = None

    # If the program is set to test mode we use different input CSVs and don't
    # prompt the user for any input in determining which data sets to use.
    # Note that this only uses historical data - it'll never actually initiate
    # a calibration or q0 measurement.
    if "Adv" in inputFile or "Demo" in inputFile:
        cryModFileIdx = header.index("Calibration Index")

        for row in csvReader:
            slacNum = int(row[slacNumIdx])
            calibIdx = int(row[cryModFileIdx])

            # Create a cryomodule object if one doesn't already exist and add
            # a calibration DataSession to it
            if slacNum not in dataSessions:
                calibSess, _ = addNewCryMod(cryModIdxMap, calIdxKeys,
                                            slacNum, cryoModules, calibIdx)
                dataSessions[slacNum] = {calibIdx: calibSess}

            else:
                # Add a DataSession object to the cryomodule if one doesn't
                # already exist for the specified calibration
                if calibIdx not in dataSessions[slacNum]:
                    calibSess, _ = addToCryMod(cryModIdxMap, slacNum,
                                               cryoModules[slacNum], calibIdx)

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
                plt.savefig("figures/cm{CM}/{CM}_{FIG}.png"
                            .format(CM=cryoModule.cryModNumSLAC, FIG=i))
            figStartIdx = lastFigNum

        plt.draw()
        plt.show()

    # If the program is not in test mode we use the basic input file (input.csv)
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

                # Multiple rows in the input file may have the same SLAC
                # cryomodule number. However, they might want to use different
                # calibrations. This is where we give the user the option to
                # reuse a calibration we've already loaded up and processed.
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
                    calibSess, refValvePos = addToCryMod(cryModIdxMap, slacNum,
                                                         cryoModules[slacNum],
                                                         refValvePos=refValvePos)

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

                # Don't do anything if this cell in the CSV is blank
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


# noinspection PyTypeChecker
def addNewCryMod(cryModIdxMap, calIdxKeys, slacNum, cryoModules, calibIdx=None,
                 refValvePos=None):
    # type: (dict, [], int, dict, int, float) -> (DataSession, float)

    calibFile = "calibrations/calibrationsCM{CM_SLAC}.csv"

    populateIdxMap(calibFile, cryModIdxMap, calIdxKeys, slacNum)

    if calibIdx:
        calibSess, _ = addDataSessionAdv(calibFile, cryModIdxMap[slacNum],
                                         slacNum, None, calibIdx)

    else:
        calibSess, refValvePos = addDataSession(calibFile,
                                                cryModIdxMap[slacNum], slacNum,
                                                None, refValvePos=refValvePos)

    cryoModules[slacNum] = calibSess.container
    calibSess.generateCSV()
    calibSess.processData()

    return calibSess, refValvePos


# Reads the header from a CSV and populates the idxMap dict passed in from
# parseInputFile.
def populateIdxMap(fileFormatter, idxMap, idxkeys, slacNum):
    sessionCSV = fileFormatter.format(CM_SLAC=slacNum)
    with open(sessionCSV) as csvFile:
        csvReader = reader(csvFile)
        header = csvReader.next()
        indices = {}
        for key, column in idxkeys:
            indices[key] = header.index(column)
        idxMap[slacNum] = indices


################################################################################
# Either adds a new DataSession to an existing Container object or creates a
# Cryomodule and adds a new DataSession object to it
# @param container: either a Cavity or Cryomodule object, or None (in which
#                   case it generates a Cryomodule object)
################################################################################
def addDataSessionAdv(fileFormatter, indices, slacNum, container, idx,
                      calibSession=None, refValvePos=None):
    # type: (str, dict, int, Container, int, DataSession, float) -> (DataSession, float)

    sessionCSV = fileFormatter.format(CM_SLAC=slacNum)
    row = open(sessionCSV).readlines()[idx - 1]
    selectedRow = reader([row]).next()

    # There is no contingency for creating a Cavity object, because they are
    # created inside of a Cryomodule's init function
    if not container:
        jlabIdx = indices["jlabNumIdx"]
        container = Cryomodule(cryModNumSLAC=slacNum,
                               cryModNumJLAB=int(selectedRow[jlabIdx]))

    return (container.addDataSessionFromRow(selectedRow, indices, calibSession),
            None)


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


def printOptions(options):
    print(("\n" + dumps(options, indent=4) + "\n")
          .replace('"', '').replace(',', ''))


def addToCryMod(cryModIdxMap, slacNum, cryoModule, calibIdx=None,
                refValvePos=None):
    # type: (dict, int, Cryomodule, int, float) -> (DataSession, float)

    calibFile = "calibrations/calibrationsCM{CM_SLAC}.csv"

    if calibIdx:
        calibSess = addDataSessionAdv(calibFile, cryModIdxMap[slacNum], slacNum,
                                      cryoModule, calibIdx)

    else:
        calibSess, refValvePos = addDataSession(calibFile,
                                                cryModIdxMap[slacNum], slacNum,
                                                cryoModule,
                                                refValvePos=refValvePos)

    calibSess.generateCSV()
    calibSess.processData()

    return calibSess, refValvePos


def genQ0Session(addDataSessionFunc, dataSessionFuncParam, cavIdxMap, slacNum,
                 cavity, calibSession, cavIdxKeys, refValvePos=None):
    # type: (callable, float, dict, int, Cavity, DataSession, [], float) -> float

    q0File = "q0Measurements/q0MeasurementsCM{CM_SLAC}.csv"

    if slacNum not in cavIdxMap:
        populateIdxMap(q0File, cavIdxMap, cavIdxKeys, slacNum)

    sessionQ0, refValvePos = addDataSessionFunc(q0File, cavIdxMap[slacNum],
                                                slacNum, cavity,
                                                dataSessionFuncParam,
                                                calibSession, refValvePos)

    sessionQ0.generateCSV()
    sessionQ0.processData()

    print("\n---------- {CM} {CAV} ----------\n"
          .format(CM=calibSession.container.name, CAV=cavity.name))
    sessionQ0.printReport()

    updateCalibCurve(calibSession.heaterCalibAxis, sessionQ0,
                     calibSession)

    return refValvePos


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


if __name__ == "__main__":
    if TEST_MODE:
        parseInputFile("testFiles/inputDemo.csv")
    else:
        parseInputFile("input.csv")
