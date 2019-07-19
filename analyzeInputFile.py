################################################################################
# A utility used to calculate Q0 for a set of cavities in a given cryomodules
# using the change in 2K helium liquid level (LL) per unit time
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import division, print_function

from abc import ABCMeta, abstractproperty, abstractmethod
from collections import OrderedDict
from csv import reader, writer
from matplotlib import pyplot as plt
from container import (Cryomodule, Cavity,
                       Q0DataSession, CalibDataSession)
from utils import (getNumInputFromLst, isYes, TEST_MODE, printOptions,
                   addOption, getSelection, drawAndShow, ValveParams)
#from typing import Optional, List, Tuple
from os.path import isfile
from numpy import mean
from decimal import Decimal


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
        # getRefValveParams (in Container) so that we don't have to rerun that
        # function every time we get new data (each call takes 2 hours)
        self.valveParams = None  # type: Optional[ValveParams]

    @abstractmethod
    def parse(self):
        raise NotImplementedError

    def saveFigs(self, cryoModule):
        lastFigNum = len(plt.get_fignums()) + 1
        for i in range(self.figStartIdx, lastFigNum):
            plt.figure(i)
            plt.savefig("figures/cm{NUM}/{CM}_{FIG}.png".format(NUM=cryoModule.cryModNumSLAC,
                                                                CM=cryoModule.name,
                                                                FIG=i))
        self.figStartIdx = lastFigNum


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

                calibSess = self.cryModManager.addNewCryModAdv(slacNum=slacNum,
                                                               calibIdx=calibIdx)

                self.dataSessions[slacNum] = {calibIdx: calibSess}

            else:
                # Add a DataSession object to the cryomodule if one doesn't
                # already exist for the specified calibration
                if calibIdx not in self.dataSessions[slacNum]:
                    cryMod = self.cryoModules[slacNum]
                    calibSess = self.cryModManager.addToCryMod(slacNum=slacNum,
                                                               calibIdx=calibIdx,
                                                               cryMod=cryMod)

                    self.dataSessions[slacNum][calibIdx] = calibSess

                calibSess = self.dataSessions[slacNum][calibIdx]

            cryoModule = self.cryoModules[slacNum]

            calibSess.printSessionReport()

            for _, cavity in cryoModule.cavities.items():
                cavIdx = self.header.index("Cavity {NUM} Index"
                                           .format(NUM=cavity.cavNum))
                try:
                    cavIdx = int(row[cavIdx])

                    self.cavManager.genQ0SessionAdv(idx=cavIdx, slacNum=slacNum,
                                                    cavity=cavity,
                                                    calibSession=calibSess)

                except ValueError:
                    pass

            self.saveFigs(cryoModule)

        drawAndShow()


class BasicInputFileParser(InputFileParser):

    def __init__(self, inputFile):
        super(BasicInputFileParser, self).__init__(inputFile)

    def genQ0Session(self, refGradVal, slacNum, cavity, calibSession):
        # type: (float, int, Cavity, CalibDataSession) -> Q0DataSession
        return self.cavManager.genQ0Session(refGradVal=refGradVal,
                                            slacNum=slacNum, cavity=cavity,
                                            calibSession=calibSession)

    def parse(self):
        for row in self.csvReader:
            slacNum = int(row[self.slacNumIdx])

            if slacNum not in self.cryoModules:
                calibSess = self.cryModManager.addNewCryMod(slacNum=slacNum)

            else:
                options = {}
                idx = 1
                idx2session = {}

                # Multiple rows in the input file may have the same SLAC
                # cryomodule number. However, the user might want to use
                # different calibrations. This is where we give the user the
                # option to reuse a calibration we've already loaded up and
                # processed.
                dataSessions = self.cryoModules[slacNum].dataSessions
                for _, dataSession in dataSessions.items():
                    options[idx] = str(dataSession)
                    idx2session[idx] = dataSession
                    idx += 1

                options[idx] = "Use a different calibration"
                printOptions(options)

                prompt = ("Please select a calibration option"
                          " (hit enter for option 1): ")
                selection = getNumInputFromLst(prompt, options.keys(), int,
                                               True)

                reuseCalibration = (selection != max(options))

                if not reuseCalibration:
                    cryMod = self.cryoModules[slacNum]
                    calibSess = self.cryModManager.addToCryMod(slacNum=slacNum,
                                                               cryMod=cryMod)

                else:
                    calibSess = idx2session[selection]

            cryoModule = self.cryoModules[slacNum]

            calibSess.printSessionReport()
            
            calibCutoffs = [str(run.diagnostics["Cutoff"]) for run in calibSess.dataRuns]
            
            fname = "results/cm{CM}.csv".format(CM=slacNum)
                
            if not isfile(fname):
                with open(fname, "w+") as f:
                    csvWriter = writer(f, delimiter=',')
                    csvWriter.writerow(["Cavity", "Gradient", "Q0",
                                        "Calibration", "Q0 Measurement",
                                        "Calibration Cutoffs",
                                        "Q0 Cutoffs"])

            for _, cavity in cryoModule.cavities.items():
                cavGradIdx = self.header.index("Cavity {NUM} Gradient"
                                               .format(NUM=cavity.cavNum))

                try:
                    gradDes = float(row[cavGradIdx])
                except ValueError:
                    continue

                print("\n---- Cavity {CAV} @ {GRAD} MV/m ----"
                      .format(CM=slacNum, CAV=cavity.cavNum,
                              GRAD=gradDes))

                q0Sess = self.genQ0Session(refGradVal=gradDes, slacNum=slacNum,
                                           cavity=cavity,
                                           calibSession=calibSess)
                                           
                q0s = [q0Sess.dataRuns[runIdx].q0 for runIdx in q0Sess.rfRunIdxs]
                q0Cutoffs = [str(run.diagnostics["Cutoff"]) for run in q0Sess.dataRuns]
                
                with open(fname, "a") as f:
                    csvWriter = writer(f, delimiter=',')
                    csvWriter.writerow([cavity.cavNum, gradDes,
                                        '{:.2e}'.format(Decimal(mean(q0s))),
                                        str(calibSess), str(q0Sess),
                                        " | ".join(calibCutoffs),
                                        " | ".join(q0Cutoffs)])
            
            self.saveFigs(cryoModule)

        drawAndShow()


class DataManager(object):

    __metaclass__ = ABCMeta

    def __init__(self, parent):
        # type: (InputFileParser) -> None

        self.parent = parent

        # Dicts of dicts where the format is:
        #   {[SLAC cryomodule number]:
        #       {[column name shorthand]: [index in the relevant CSV header]}}
        self.idxMap = {}

        # Used to populate cryModIdxMap and cavIdxMap. Each tuple in the list is
        # of the form: ([column name shorthand], [column title in the CSV])
        self.baseIdxKeys = [("startIdx", "Start"), ("endIdx", "End"),
                            ("refHeatIdx", "Reference Heat Load (Des)"),
                            ("refHeatActIdx", "Reference Heat Load (Act)"),
                            ("jtIdx", "JT Valve Position"),
                            ("timeIntIdx", "MySampler Time Interval")]

        self._idxKeys = None

    @abstractproperty
    def header(self):
        raise NotImplementedError
    
    @abstractproperty
    def idxKeys(self):
        raise NotImplementedError

    @abstractproperty
    def fileFormatter(self):
        raise NotImplementedError

    ############################################################################
    # This is the DataSession creation method called when we are using basic
    # user input.
    # @param container: a Container object (Cryomodule or Cavity), or None. If
    #                   None, create a new Cryomodule
    ############################################################################
    @abstractmethod
    def addDataSession(self, slacNum, container, refGradVal=None,
                       calibSession=None):
        raise NotImplementedError

    ############################################################################
    # Either adds a new DataSession to an existing Container object or creates a
    # Cryomodule and adds a new DataSession object to it
    # @param container: either a Cavity or Cryomodule object, or None (in which
    #                   case it generates a Cryomodule object)
    # @param refValvePos: not used in this function but it has to be here to
    #                     make the call signature match addDataSession's.
    ############################################################################
    @abstractmethod
    def addDataSessionAdv(self, slacNum, container, idx, calibSession=None,
                          refValvePos=None):
        raise NotImplementedError

    @property
    def valveParams(self):
        return self.parent.valveParams

    def getRowAndHeatLoad(self, slacNum, idx):
        # type: (int, int) -> Tuple[List[str], float, float]

        sessionCSV = self.fileFormatter.format(CM_SLAC=slacNum)
        row = open(sessionCSV).readlines()[idx - 1]

        selectedRow = reader([row]).next()
        refHeatLoad = float(selectedRow[self.idxMap[slacNum]["refHeatIdx"]])
        refHeatLoadAct = float(selectedRow[self.idxMap[slacNum]["refHeatActIdx"]])

        return selectedRow, refHeatLoad, refHeatLoadAct

    # Reads the header from a CSV and populates the idxMap dict passed in from
    # parseInputFile.
    def populateIdxMap(self, slacNum):
        # type: (int) -> None
        def populate(fileObj, header=None):
            if not header:
                csvReader = reader(fileObj)
                header = csvReader.next()
            indices = {}
            for key, column in self.idxKeys:
                indices[key] = header.index(column)
            self.idxMap[slacNum] = indices

        if slacNum not in self.idxMap:
            sessionCSV = self.fileFormatter.format(CM_SLAC=slacNum)
            if not isfile(sessionCSV):
                with open(sessionCSV, "w+") as f:
                    headerWriter = writer(f)
                    headerWriter.writerow(self.header)
                    populate(f, self.header)
                
            with open(sessionCSV) as csvFile:
                populate(csvFile)

    def getRowsAndFileReader(self, slacNum):
        sessionCSV = self.fileFormatter.format(CM_SLAC=slacNum)
        rows = open(sessionCSV).readlines()
        # Reversing to get in chronological order (the program appends the most
        # recent sessions to the end of the file)
        rows.reverse()
        reader([rows.pop()]).next()
        fileReader = reader(rows)
        return fileReader, rows


class CavityDataManager(DataManager):

    def __init__(self, parent):
        # type: (InputFileParser) -> None

        super(CavityDataManager, self).__init__(parent)

    @property
    def header(self):
        return ["Cavity","Gradient", "JT Valve Position","Start","End",
                "Reference Heat Load (Des)", "Reference Heat Load (Act)",
                "MySampler Time Interval"]

    @property
    def fileFormatter(self):
        return "q0Measurements/q0MeasurementsCM{CM_SLAC}.csv"

    @property
    def idxKeys(self):
        if not self._idxKeys:
            self._idxKeys = self.baseIdxKeys + [("cavNumIdx", "Cavity"),
                                                ("gradIdx", "Gradient")]
        return self._idxKeys

    def addDataSessionAdv(self, slacNum, container, idx,
                          calibSession=None, refValvePos=None):
        # type: (int, Cavity, int, CalibDataSession, float) -> Q0DataSession

        row, refHeatLoad, refHeatLoadAct = self.getRowAndHeatLoad(slacNum=slacNum, idx=idx)

        refGradVal = float(row[self.idxMap[slacNum]["gradIdx"]])

        return container.addDataSessionFromRow(row, self.idxMap[slacNum],
                                               refHeatLoad, refHeatLoadAct, calibSession,
                                               refGradVal)

    # @param addDataSessionFunc: either addDataSession or addDataSessionAdv,
    #                            depending on the choice of user input file
    def genQ0SessionAdv(self, idx, slacNum, cavity, calibSession):
        # type: (int, int, Cavity, CalibDataSession) -> None

        self.populateIdxMap(slacNum=slacNum)

        q0Session = self.addDataSessionAdv(slacNum=slacNum, container=cavity,
                                           idx=idx, calibSession=calibSession)

        q0Session.updateOutput()

    def genQ0Session(self, refGradVal, slacNum, cavity, calibSession):
        # type: (float, int, Cavity, CalibDataSession) -> Q0DataSession

        self.populateIdxMap(slacNum=slacNum)

        q0Session = self.addDataSession(slacNum=slacNum, container=cavity,
                                        refGradVal=refGradVal,
                                        calibSession=calibSession)

        q0Session.updateOutput()
        return q0Session

    def addDataSession(self, slacNum, container, refGradVal=None,
                       calibSession=None):
        # type: (int, Cavity, float, CalibDataSession) -> Q0DataSession

        indices = self.idxMap[slacNum]

        fileReader, rows = self.getRowsAndFileReader(slacNum)

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

            addOption(csvRow=row, lineNum=fileReader.line_num, indices=indices,
                      options=options)

        selection = getSelection(duration=2, suffix="Q0 Measurement",
                                 options=options)

        # If using an existing data session
        if selection != max(options):
            selectedRow = reader([rows[selection - 1]]).next()
            refHeatLoad = float(selectedRow[indices["refHeatIdx"]])
            refHeatLoadAct = float(selectedRow[indices["refHeatActIdx"]])
            return container.addDataSessionFromRow(selectedRow, indices,
                                                   refHeatLoad, refHeatLoadAct,
                                                   calibSession, refGradVal)

        else:
            (Q0Sess,
             self.parent.valveParams) = container.runQ0Meas(refGradVal,
                                                            calibSession,
                                                            self.valveParams)
            return Q0Sess


class CryModDataManager(DataManager):

    @property
    def header(self):
        return ["JLAB Number", "Reference Heat Load (Des)",
                "Reference Heat Load (Act)", "JT Valve Position",
                "Start", "End", "MySampler Time Interval"]

    @property
    def fileFormatter(self):
        return "calibrations/calibrationsCM{CM_SLAC}.csv"

    def addDataSessionAdv(self, slacNum, container, idx,
                          calibSession=None, refValvePos=None):
        # type: (int, Optional[Cryomodule], int, CalibDataSession, float) -> CalibDataSession

        row, refHeatLoad, refHeatLoadAct = self.getRowAndHeatLoad(slacNum=slacNum, idx=idx)

        # There is no contingency for creating a lone Cavity object because they
        # are always created inside of a Cryomodule's init function
        if not container:
            jlabIdx = self.idxMap[slacNum]["jlabNumIdx"]
            container = Cryomodule(cryModNumSLAC=slacNum,
                                   cryModNumJLAB=int(row[jlabIdx]))

        return container.addDataSessionFromRow(row, self.idxMap[slacNum],
                                               refHeatLoad, refHeatLoadAct,
                                               calibSession)

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
    def addNewCryMod(self, slacNum):
        # type: (int) -> CalibDataSession

        self.populateIdxMap(slacNum=slacNum)

        calibSession = self.addDataSession(slacNum=slacNum, container=None)

        self.parent.cryoModules[slacNum] = calibSession.container

        return calibSession

    def addNewCryModAdv(self, slacNum, calibIdx):
        # type: (int, int) -> CalibDataSession

        self.populateIdxMap(slacNum=slacNum)

        calibSession = self.addDataSessionAdv(slacNum=slacNum,
                                              container=None, idx=calibIdx)

        self.parent.cryoModules[slacNum] = calibSession.container

        return calibSession

    ############################################################################
    # addToCryMod takes an existing Cryomodule object and adds a data session to
    # it
    # @param calibIdx: The row number in the target cryomodule's record of
    #                  previous calibrations. If it's None that means we're
    #                  using basic user input.
    ############################################################################
    def addToCryMod(self, slacNum, cryMod, calibIdx=None):
        # type: (int, Cryomodule, int) -> CalibDataSession

        if calibIdx:
            calibSession = self.addDataSessionAdv(slacNum=slacNum,
                                                  container=cryMod,
                                                  idx=calibIdx)

        else:
            calibSession = self.addDataSession(slacNum=slacNum,
                                               container=cryMod)

        return calibSession

    def addDataSession(self, slacNum, container, refGradVal=None,
                       calibSession=None):
        # type: (int, Cryomodule, float, CalibDataSession) -> CalibDataSession

        indices = self.idxMap[slacNum]

        fileReader, rows = self.getRowsAndFileReader(slacNum)

        # Unclear if this is actually necessary, but the idea is to have the
        # output of json.dumps be ordered by index number
        options = OrderedDict()

        for row in fileReader:

            if (len(options) + 1) % 10 == 0:
                printOptions(options)
                showMore = isYes("Search for more options? ")
                if not showMore:
                    break

            addOption(csvRow=row, lineNum=fileReader.line_num, indices=indices,
                      options=options)

        selection = getSelection(duration=5, suffix="calibration",
                                 options=options)

        if selection != max(options):

            calibRow = reader([rows[selection - 1]]).next()

            if not container:
                container = Cryomodule(slacNum,
                                       calibRow[indices["jlabNumIdx"]])

            refHeatLoad = float(calibRow[indices["refHeatIdx"]])
            refHeatLoadAct = float(calibRow[indices["refHeatActIdx"]])

            return container.addDataSessionFromRow(calibRow, indices,
                                                   refHeatLoad, refHeatLoadAct)

        else:
            if not container:
                container = Cryomodule(slacNum,
                                       getNumInputFromLst("JLab cryomodule"
                                                          " number: ", [2, 3],
                                                          int))

            (calibSession,
             self.parent.valveParams) = container.runCalibration(self.parent.valveParams)

            return calibSession


if __name__ == "__main__":
    try:
        if TEST_MODE:
            AdvInputFileParser("testFiles/inputAdv.csv").parse()
        else:
            BasicInputFileParser("input.csv").parse()
    except(KeyboardInterrupt):
        print("\n\n:(\n")
