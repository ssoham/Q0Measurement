################################################################################
# A utility used to calculate Q0 for a set of cavities in a given cryomodules
# using the change in 2K helium liquid level (LL) per unit time
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import division, print_function

from abc import ABCMeta, abstractproperty, abstractmethod
from collections import OrderedDict
from csv import reader
from matplotlib import pyplot as plt
from container import (Cryomodule, Cavity,
                       Q0DataSession, CalibDataSession)
from utils import (getNumInputFromLst, isYes, TEST_MODE, makeTimeFromStr,
                   printOptions)
from typing import Optional


class InputFileParser(object):

    __metaclass__ = ABCMeta

    def __init__(self, inputFile):
        # type: (str) -> None

        self.inputFile = inputFile

        self.csvReader = reader(open(inputFile))
        self.header = self.csvReader.next()
        self.slacNumIdx = self.header.index("SLAC Cryomodule Number")

        self.cryModManager = CryModDataManager(self)
        self.cavManager = CavityDataManager(self)

        self.figStartIdx = 1

        # A dict of dicts where the format is:
        #   {[SLAC cryomodule number]:
        #       {[line number from record-keeping CSV]: [DataSession object]}}
        self.dataSessions = {}

        # A dict of the form {[SLAC cryomodule number]: [Cryomodule Object]}
        self.cryoModules = {}

        # We store the JT Valve position from the first time that we run
        # getRefValvePos (in Container) so that we don't have to rerun that
        # function every time we get new data (each call takes 2 hours)
        self.refValvePos = None

    @abstractmethod
    def parse(self):
        raise NotImplementedError

    def addToCryMod(self, slacNum, calibIdx=None):
        cryMod = self.cryoModules[slacNum]
        return self.cryModManager.addToCryMod(slacNum=slacNum,
                                              calibIdx=calibIdx, cryMod=cryMod)

    def addNewCryMod(self, slacNum, calibIdx=None):
        return self.cryModManager.addNewCryMod(slacNum=slacNum,
                                               calibIdx=calibIdx)

    def genQ0Session(self, refGradVal, slacNum, cavity, calibSession):
        return self.cavManager.genQ0Session(refGradVal=refGradVal,
                                            slacNum=slacNum, cavity=cavity,
                                            calibSession=calibSession)

    def genQ0SessionAdv(self, idx, slacNum, cavity, calibSession):
        return self.cavManager.genQ0SessionAdv(idx=idx, slacNum=slacNum,
                                               cavity=cavity,
                                               calibSession=calibSession)


class AdvInputFileParser(InputFileParser):

    def __init__(self, inputFile):
        super(AdvInputFileParser, self).__init__(inputFile)

    def parse(self):

        cryModFileIdx = self.header.index("Calibration Index")

        for row in self.csvReader:
            slacNum = int(row[self.slacNumIdx])
            calibIdx = int(row[cryModFileIdx])

            # Create a cryomodule object if one doesn't already exist and add
            # a calibration DataSession to it
            if slacNum not in self.dataSessions:

                calibSess, _ = self.addNewCryMod(slacNum=slacNum,
                                                 calibIdx=calibIdx)

                self.dataSessions[slacNum] = {calibIdx: calibSess}

            else:
                # Add a DataSession object to the cryomodule if one doesn't
                # already exist for the specified calibration
                if calibIdx not in self.dataSessions[slacNum]:
                    calibSess, _ = self.addToCryMod(slacNum=slacNum,
                                                    calibIdx=calibIdx)

                    self.dataSessions[slacNum][calibIdx] = calibSess

                calibSess = self.dataSessions[slacNum][calibIdx]

            cryoModule = self.cryoModules[slacNum]

            calibSess.printSessionReport()

            for _, cavity in cryoModule.cavities.items():
                cavIdx = self.header.index("Cavity {NUM} Index"
                                           .format(NUM=cavity.cavNum))
                try:
                    cavIdx = int(row[cavIdx])

                    self.genQ0SessionAdv(idx=cavIdx, slacNum=slacNum,
                                         cavity=cavity, calibSession=calibSess)

                except ValueError:
                    pass

            lastFigNum = len(plt.get_fignums()) + 1
            for i in range(self.figStartIdx, lastFigNum):
                plt.figure(i)
                plt.savefig("figures/cm{CM}/{CM}_{FIG}.png"
                            .format(CM=cryoModule.cryModNumSLAC, FIG=i))
            self.figStartIdx = lastFigNum

        plt.draw()
        plt.show()


class BasicInputFileParser(InputFileParser):

    def __init__(self, inputFile):
        super(BasicInputFileParser, self).__init__(inputFile)

    def parse(self):
        for row in self.csvReader:
            slacNum = int(row[self.slacNumIdx])

            if slacNum not in self.cryoModules:
                calibSess, refValvePos = self.addNewCryMod(slacNum=slacNum)

            else:
                options = {}
                idx = 1
                idx2session = {}

                # Multiple rows in the input file may have the same SLAC
                # cryomodule number. However, they might want to use
                # different calibrations. This is where we give the user the
                # option to reuse a calibration we've already loaded up and
                # processed.
                for _, dataSession in self.cryoModules[slacNum].dataSessions.items():
                    options[idx] = str(dataSession)
                    idx2session[idx] = dataSession
                    idx += 1

                options[idx] = "Use a different calibration"
                printOptions(options)

                selection = getNumInputFromLst(
                    ("Please select a calibration"
                     " option: "), options.keys(),
                    int)

                reuseCalibration = (selection != max(options))

                if not reuseCalibration:
                    (calibSess,
                     refValvePos) = self.addToCryMod(slacNum=slacNum)

                else:
                    calibSess = idx2session[selection]

            cryoModule = self.cryoModules[slacNum]

            calibSess.printSessionReport()

            for _, cavity in cryoModule.cavities.items():
                cavGradIdx = self.header.index("Cavity {NUM} Gradient"
                                               .format(NUM=cavity.cavNum))

                try:
                    gradDes = float(row[cavGradIdx])

                    print("\n---- Cavity {CAV} @ {GRAD} MV/m ----"
                          .format(CM=slacNum, CAV=cavity.cavNum,
                                  GRAD=gradDes))

                    self.genQ0Session(refGradVal=gradDes, slacNum=slacNum,
                                      cavity=cavity, calibSession=calibSess)

                # Don't do anything if this cell in the CSV is blank
                except ValueError:
                    pass

            lastFigNum = len(plt.get_fignums()) + 1
            for i in range(self.figStartIdx, lastFigNum):
                plt.figure(i)
                plt.savefig(
                    "figures/{CM}_{FIG}.png".format(CM=cryoModule.name,
                                                    FIG=i))
            self.figStartIdx = lastFigNum

        plt.draw()
        plt.show()


class DataManager(object):

    __metaclass__ = ABCMeta

    def __init__(self, parent):
        # type: (InputFileParser) -> None

        self.parent = parent

        # Dicts of dicts where the format is:
        #   {[SLAC cryomodule number]:
        #       {[column name shorthand]: [index in the relevant CSV header]}}
        self.idxMap = {}

        # Used to populate cryModIdxMap and cavIdxMap. Each tuple in the list is of
        # the form: ([column name shorthand], [column title in the CSV])
        self.baseIdxKeys = [("startIdx", "Start"), ("endIdx", "End"),
                            ("refHeatIdx", "Reference Heat Load"),
                            ("jtIdx", "JT Valve Position"),
                            ("timeIntIdx", "MySampler Time Interval")]

        self._idxKeys = None

    @abstractproperty
    def idxKeys(self):
        raise NotImplementedError

    # This is the DataSession creation method called when we are using basic user
    # input.
    # @param container: a Container object (Cryomodule or Cavity), or None. If None,
    #                   create a new Cryomodule
    @abstractmethod
    def addDataSession(self, fileFormatter, slacNum, container,
                       refGradVal=None, calibSession=None):
        raise NotImplementedError

    @abstractmethod
    ############################################################################
    # Either adds a new DataSession to an existing Container object or creates a
    # Cryomodule and adds a new DataSession object to it
    # @param container: either a Cavity or Cryomodule object, or None (in which
    #                   case it generates a Cryomodule object)
    # @param refValvePos: not used in this function but it has to be here to
    #                     make the call signature match addDataSession's.
    ############################################################################
    def addDataSessionAdv(self, fileFormatter, slacNum, container, idx,
                          calibSession=None, refValvePos=None):
        raise NotImplementedError

    @property
    def refValvePos(self):
        return self.parent.refValvePos

    # Reads the header from a CSV and populates the idxMap dict passed in from
    # parseInputFile.
    def populateIdxMap(self, fileFormatter, slacNum):
        # type: (str, int) -> None
        sessionCSV = fileFormatter.format(CM_SLAC=slacNum)
        with open(sessionCSV) as csvFile:
            csvReader = reader(csvFile)
            header = csvReader.next()
            indices = {}
            for key, column in self.idxKeys:
                indices[key] = header.index(column)
            self.idxMap[slacNum] = indices


class CavityDataManager(DataManager):

    def addDataSessionAdv(self, fileFormatter, slacNum, container, idx,
                          calibSession=None, refValvePos=None):
        # type: (str, int, Cavity, int, CalibDataSession, float) -> Q0DataSession
        indices = self.idxMap[slacNum]

        sessionCSV = fileFormatter.format(CM_SLAC=slacNum)
        row = open(sessionCSV).readlines()[idx - 1]
        selectedRow = reader([row]).next()

        refGradVal = float(selectedRow[indices["gradIdx"]])
        refHeatLoad = float(selectedRow[indices["refHeatIdx"]])

        return container.addDataSessionFromRow(selectedRow, indices,
                                               refHeatLoad, calibSession,
                                               refGradVal)

    def __init__(self, parent):
        # type: (InputFileParser) -> None

        super(CavityDataManager, self).__init__(parent)

    @property
    def idxKeys(self):
        if not self._idxKeys:
            self._idxKeys = self.baseIdxKeys + [("cavNumIdx", "Cavity"),
                                                ("gradIdx", "Gradient")]
        return self._idxKeys

    # @param addDataSessionFunc: either addDataSession or addDataSessionAdv,
    #                            depending on the choice of user input file
    def genQ0SessionAdv(self, idx, slacNum, cavity, calibSession):
        # type: (int, int, Cavity, CalibDataSession) -> None

        q0File = "q0Measurements/q0MeasurementsCM{CM_SLAC}.csv"

        if slacNum not in self.idxMap:
            self.populateIdxMap(fileFormatter=q0File, slacNum=slacNum)

        q0Session = self.addDataSessionAdv(fileFormatter=q0File,
                                           slacNum=slacNum, container=cavity,
                                           idx=idx, calibSession=calibSession)

        q0Session.printSessionReport()
        q0Session.updateCalibCurve()

    def genQ0Session(self, refGradVal, slacNum, cavity, calibSession):
        # type: (float, int, Cavity, CalibDataSession) -> None
        q0File = "q0Measurements/q0MeasurementsCM{CM_SLAC}.csv"

        if slacNum not in self.idxMap:
            self.populateIdxMap(fileFormatter=q0File, slacNum=slacNum)

        q0Session = self.addDataSession(fileFormatter=q0File, slacNum=slacNum,
                                        container=cavity, refGradVal=refGradVal,
                                        calibSession=calibSession)

        q0Session.printSessionReport()
        q0Session.updateCalibCurve()


    def addDataSession(self, fileFormatter, slacNum, container, refGradVal=None,
                       calibSession=None):
        # type: (str, int, Cavity, float, CalibDataSession) -> Q0DataSession

        indices = self.idxMap[slacNum]

        def addOption(csvRow, lineNum):
            startTime = makeTimeFromStr(csvRow, indices["startIdx"])
            endTime = makeTimeFromStr(csvRow, indices["endIdx"])
            rate = csvRow[indices["timeIntIdx"]]
            options[lineNum] = ("{START} to {END} ({RATE}s sample interval)"
                                .format(START=startTime, END=endTime,
                                        RATE=rate))

        def getSelection(duration, suffix):
            # type: (float, str) -> int
            # Running a new Q0 measurement or heater calibration is always
            # presented as the last option in the list
            options[max(options) + 1] = ("Launch new {TYPE} ({DUR} hours)"
                                         .format(TYPE=suffix, DUR=duration))
            printOptions(options)
            return getNumInputFromLst(("Please select a {TYPE} option: "
                                       .format(TYPE=suffix)),
                                      options.keys(),
                                      int)

        sessionCSV = fileFormatter.format(CM_SLAC=slacNum)
        rows = open(sessionCSV).readlines()

        # Reversing to get in chronological order (the program appends the most
        # recent sessions to the end of the file)
        rows.reverse()
        reader([rows.pop()]).next()

        fileReader = reader(rows)

        # Unclear if this is actually necessary, but the idea is to have the
        # output of json.dumps be ordered by index number
        options = OrderedDict()

        for row in fileReader:

            # We could theoretically have hundreds of results, and that seems
            # like a seriously unnecessary number of options to show. This
            # asks the user if they want to keep searching for more every 10
            # hits
            if (len(options) + 1) % 10 == 0:
                printOptions(options)
                showMore = isYes("Search for more options? ")
                if not showMore:
                    break

            grad = float(row[indices["gradIdx"]])
            cavNum = int(row[indices["cavNumIdx"]])

            # The files are per cryomodule, so there's a lot of different
            # cavities in the file. We check to make sure that we're only
            # presenting the options for the requested cavity at the requested
            # gradient (by just skipping the irrelevant ones)
            if (grad != refGradVal) or (cavNum != container.cavNum):
                continue

            addOption(row, fileReader.line_num)

        selection = getSelection(2, "Q0 Measurement")

        # If using an existing data session
        if selection != max(options):
            selectedRow = reader([rows[selection - 1]]).next()
            refHeatLoad = float(selectedRow[indices["refHeatIdx"]])
            return container.addDataSessionFromRow(selectedRow, indices,
                                                   refHeatLoad, calibSession,
                                                   refGradVal)

        else:
            (Q0Sess,
             self.parent.refValvePos) = container.runQ0Meas(refGradVal,
                                                            calibSession,
                                                            self.refValvePos)
            return Q0Sess


class CryModDataManager(DataManager):

    def addDataSessionAdv(self, fileFormatter, slacNum, container, idx,
                          calibSession=None, refValvePos=None):
        # type: (str, int, Optional[Cryomodule], int, CalibDataSession, float) -> CalibDataSession

        indices = self.idxMap[slacNum]

        sessionCSV = fileFormatter.format(CM_SLAC=slacNum)
        row = open(sessionCSV).readlines()[idx - 1]
        selectedRow = reader([row]).next()

        # There is no contingency for creating a lone Cavity object because they
        # are always created inside of a Cryomodule's init function
        if not container:
            jlabIdx = indices["jlabNumIdx"]
            container = Cryomodule(cryModNumSLAC=slacNum,
                                   cryModNumJLAB=int(selectedRow[jlabIdx]))

        refHeatLoad = float(selectedRow[indices["refHeatIdx"]])

        return container.addDataSessionFromRow(selectedRow, indices,
                                               refHeatLoad, calibSession)

    def __init__(self, parent):
        # type: (InputFileParser) -> None
        super(CryModDataManager, self).__init__(parent)

    @property
    def idxKeys(self):
        if not self._idxKeys:
            self._idxKeys = self.baseIdxKeys + [("jlabNumIdx", "JLAB Number")]
        return self._idxKeys

    ############################################################################
    # addNewCryMod creates a new Cryomodule object and adds a data session to it
    # @param calibIdx: The row number in the target cryomodule's record of
    #                  previous calibrations. If it's None that means we're
    #                  using basic user input.
    # noinspection PyTypeChecker
    ############################################################################
    def addNewCryMod(self, slacNum, calibIdx=None):
        # type: (int, int) -> (CalibDataSession, float)

        calibFile = "calibrations/calibrationsCM{CM_SLAC}.csv"

        self.populateIdxMap(fileFormatter=calibFile, slacNum=slacNum)

        refPosJT = None

        if calibIdx:
            calibSession = self.addDataSessionAdv(fileFormatter=calibFile,
                                                  slacNum=slacNum,
                                                  container=None, idx=calibIdx)

        else:
            calibSession, refPosJT = self.addDataSession(fileFormatter=calibFile,
                                                         slacNum=slacNum,
                                                         container=None)

        self.parent.cryoModules[slacNum] = calibSession.container

        return calibSession, refPosJT if refPosJT else self.parent.refValvePos

    ############################################################################
    # addToCryMod takes an existing Cryomodule object and adds a data session to
    # it
    # @param calibIdx: The row number in the target cryomodule's record of
    #                  previous calibrations. If it's None that means we're
    #                  using basic user input.
    ############################################################################
    def addToCryMod(self, slacNum, cryMod, calibIdx=None):
        # type: (int, Cryomodule, int) -> (CalibDataSession, float)

        calibFile = "calibrations/calibrationsCM{CM_SLAC}.csv"

        refPosJT = None
        if calibIdx:
            calibSession = self.addDataSessionAdv(fileFormatter=calibFile,
                                                  slacNum=slacNum,
                                                  container=cryMod,
                                                  idx=calibIdx)

        else:
            calibSession, refPosJT = self.addDataSession(fileFormatter=calibFile,
                                                         slacNum=slacNum,
                                                         container=cryMod)

        return calibSession, refPosJT if refPosJT else self.parent.refValvePos

    def addDataSession(self, fileFormatter, slacNum, container, refGradVal=None,
                       calibSession=None):

        indices = self.idxMap[slacNum]

        def addOption(csvRow, lineNum):
            startTime = makeTimeFromStr(csvRow, indices["startIdx"])
            endTime = makeTimeFromStr(csvRow, indices["endIdx"])
            rate = csvRow[indices["timeIntIdx"]]
            options[lineNum] = ("{START} to {END} ({RATE}s sample interval)"
                                .format(START=startTime, END=endTime,
                                        RATE=rate))

        def getSelection(duration, suffix):
            # type: (float, str) -> int
            # Running a new Q0 measurement or heater calibration is always presented
            # as the last option in the list
            options[max(options) + 1] = ("Launch new {TYPE} ({DUR} hours)"
                                         .format(TYPE=suffix, DUR=duration))
            printOptions(options)
            return getNumInputFromLst(("Please select a {TYPE} option: "
                                       .format(TYPE=suffix)),
                                      options.keys(),
                                      int)

        sessionCSV = fileFormatter.format(CM_SLAC=slacNum)
        rows = open(sessionCSV).readlines()

        # Reversing to get in chronological order (the program appends the most
        # recent sessions to the end of the file)
        rows.reverse()
        reader([rows.pop()]).next()

        fileReader = reader(rows)

        # Unclear if this is actually necessary, but the idea is to have the
        # output of json.dumps be ordered by index number
        options = OrderedDict()

        for row in fileReader:

            if (len(options) + 1) % 10 == 0:
                printOptions(options)
                showMore = isYes("Search for more options? ")
                if not showMore:
                    break

            addOption(row, fileReader.line_num)

        selection = getSelection(5, "calibration")

        if selection != max(options):

            calibRow = reader([rows[selection - 1]]).next()

            if not container:
                container = Cryomodule(slacNum,
                                       calibRow[indices["jlabNumIdx"]])

            refHeatLoad = float(calibRow[indices["refHeatIdx"]])

            return container.addDataSessionFromRow(calibRow, indices,
                                                   refHeatLoad), None

        else:
            if not container:
                container = Cryomodule(slacNum,
                                       getNumInputFromLst("JLab cryomodule"
                                                          " number: ", [2, 3],
                                                          int))

            return container.runCalibration(self.parent.refValvePos)


if __name__ == "__main__":
    if TEST_MODE:
        AdvInputFileParser("testFiles/inputAdv.csv").parse()
    else:
        BasicInputFileParser("input.csv").parse()
