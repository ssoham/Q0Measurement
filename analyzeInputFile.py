################################################################################
# A utility used to calculate Q0 for a set of cavities in a given cryomodules
# using the change in 2K helium liquid level (LL) per unit time
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import print_function, division

from abc import ABCMeta, abstractmethod
from collections import OrderedDict
from csv import reader, writer
from matplotlib import pyplot as plt
from container import (Cryomodule, Cavity,
                       Q0DataSession, CalibDataSession, DataSession)
from utils import (getNumInputFromLst, isYes, TEST_MODE, printOptions,
                   addOption, getSelection, drawAndShow, ValveParams,
                   compatibleNext, compatibleMkdirs)
from typing import Optional, List, Tuple, TextIO, Dict
from os.path import isfile
from numpy import mean
from decimal import Decimal


class InputFileParser(object):
    __metaclass__ = ABCMeta

    def __init__(self, inputFile):
        # type: (str) -> None

        self.inputFile = inputFile

        self.csvReader = reader(open(inputFile))
        self.header = compatibleNext(self.csvReader)
        self.slacNumIdx = self.header.index("SLAC Cryomodule Number")

        self.cryModManager = CryModDataManager(self)
        self.cavManager = CavityDataManager(self)

        self.figStartIdx = 1

        # A dict of dicts where the format is:
        #   {[SLAC cryomodule number]:
        #       {[line number from record-keeping CSV]: [DataSession object]}}
        self.dataSessions = {}

        # A dict of the form {[SLAC cryomodule number]: [Cryomodule Object]}
        self.cryoModules = {}  # type: Dict[int, Cryomodule]

        # We store the JT Valve position from the first time that we run
        # getRefValveParams (in Container) so that we don't have to rerun that
        # function every time we get new data (each call takes 2 hours)
        self.valveParams = None  # type: Optional[ValveParams]

    @abstractmethod
    def parse(self):
        raise NotImplementedError

    def saveFigs(self, cryoModule):
        # plt.tight_layout()
        lastFigNum = len(plt.get_fignums()) + 1
        for i in range(self.figStartIdx, lastFigNum):
            plt.figure(i)
            figFile = ("figures/cm{NUM}/{CM}_{FIG}.png"
                       .format(NUM=cryoModule.cryModNumSLAC, CM=cryoModule.name,
                               FIG=i))
            compatibleMkdirs(figFile)
            plt.savefig(figFile, bbox_inches='tight', dpi=300)
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

    def genQ0Session(self, desiredGradient, slacNum, cavity, calibSession):
        # type: (float, int, Cavity, CalibDataSession) -> Q0DataSession
        return self.cavManager.genQ0Session(refGradVal=desiredGradient,
                                            slacNum=slacNum, cavity=cavity,
                                            calibSession=calibSession)

    def genMultiQ0Session(self, desiredGradients, slacNum, cryomodule, calibSession):
        # type: (Dict[int], int, Cryomodule, CalibDataSession) -> Q0DataSession
        return self.cryModManager.genQ0Session(desiredGradients=desiredGradients,
                                               slacNum=slacNum, cryomodule=cryomodule,
                                               calibSession=calibSession)

    def parse(self):
        for row in self.csvReader:
            slacNum = int(row[self.slacNumIdx])

            calibSess, desiredGradients = self.getCalibAndDesGrads(row, slacNum)

            cryoModule = self.cryoModules[slacNum]

            calibSess.printSessionReport()

            calibCutoffs = [str(run.diagnostics["Cutoff"]) for run in calibSess.dataRuns]

            if desiredGradients:

                options = {1: "multi-cavity Q0 measurement",
                           2: "single-cavity Q0 measurements"}

                prompt = "Please select a type of measurement for row: {ROW}".format(ROW=row)
                printOptions(options)

                selectedType = getNumInputFromLst(prompt, options.keys(), int, True)

                if selectedType == 2:
                    for cavNum, gradDes in desiredGradients.items():
                        if not gradDes:
                            continue

                        fname = ("results/cm{CM}/cav{CAV}/resultsCM{CM}CAV{CAV}.csv"
                                 .format(CM=slacNum, CAV=cavNum))

                        if not isfile(fname):
                            compatibleMkdirs(fname)
                            with open(fname, "w+") as f:
                                csvWriter = writer(f, delimiter=',')
                                csvWriter.writerow(["Cavity", "Gradient", "Q0",
                                                    "Calibration", "Q0 Measurement",
                                                    "Calibration Cutoffs",
                                                    "Q0 Cutoffs"])

                        print("\n---- Cavity {CAV} @ {GRAD} MV/m ----"
                              .format(CM=slacNum, CAV=cavNum, GRAD=gradDes))

                        cavity = self.cryoModules[slacNum].cavities[cavNum]

                        q0Sess = self.genQ0Session(desiredGradient=gradDes, slacNum=slacNum,
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
                else:
                    # desiredGradients = {}
                    # for _, cavity in cryoModule.cavities.items():
                    #     cavGradIdx = self.header.index("Cavity {NUM} Gradient"
                    #                                    .format(NUM=cavity.cavNum))
                    #     try:
                    #         gradDes = float(row[cavGradIdx])
                    #     except ValueError:
                    #         continue
                    #
                    #     desiredGradients[cavity.cavNum] = gradDes

                    fname = "results/cm{CM}/resultsCM{CM}.csv".format(CM=slacNum)

                    if not isfile(fname):
                        compatibleMkdirs(fname)
                        with open(fname, "w+") as f:
                            csvWriter = writer(f, delimiter=',')
                            csvWriter.writerow(["Cavity 1 Gradient",
                                                "Cavity 2 Gradient",
                                                "Cavity 3 Gradient",
                                                "Cavity 4 Gradient",
                                                "Cavity 5 Gradient",
                                                "Cavity 6 Gradient",
                                                "Cavity 7 Gradient",
                                                "Cavity 8 Gradient",
                                                "Cumulative Gradient", "Q0",
                                                "Calibration", "Q0 Measurement",
                                                "Calibration Cutoffs",
                                                "Q0 Cutoffs"])

                    q0Sess = self.genMultiQ0Session(desiredGradients=desiredGradients,
                                                    slacNum=slacNum,
                                                    cryomodule=cryoModule,
                                                    calibSession=calibSess)

                    q0s = [q0Sess.dataRuns[runIdx].q0 for runIdx in q0Sess.rfRunIdxs]
                    q0Cutoffs = [str(run.diagnostics["Cutoff"]) for run in q0Sess.dataRuns]

                    with open(fname, "a") as f:
                        csvWriter = writer(f, delimiter=',')

                        # TODO fix this...
                        csvWriter.writerow([slacNum, desiredGradients,
                                            '{:.2e}'.format(Decimal(mean(q0s))),
                                            str(calibSess), str(q0Sess),
                                            " | ".join(calibCutoffs),
                                            " | ".join(q0Cutoffs)])
            self.saveFigs(cryoModule)

        drawAndShow()

    def getCalibAndDesGrads(self, row, slacNum):
        desiredGradients = {}
        options = {1: "Full module calibration",
                   2: "Single cavity calibration (per cavity in row)"}
        printOptions(options)
        prompt = "Please select a calibration option (hit enter for option 1): "
        selection = getNumInputFromLst(prompt, options.keys(), int, True)
        if selection == 1:
            if ((slacNum not in self.cryoModules)
                    or (not self.cryoModules[slacNum].calibDataSessions)):
                calibSess = self.cryModManager.addNewCalibration(slacNum=slacNum)

            else:
                options = {}
                idx = 1
                idx2session = {}

                # Multiple rows in the input file may have the same SLAC
                # cryomodule number. However, the user might want to use
                # different calibrations. This is where we give the user the
                # option to reuse a calibration we've already loaded up and
                # processed.
                calibDataSessions = self.cryoModules[slacNum].calibDataSessions
                for _, calibDataSession in calibDataSessions.items():
                    options[idx] = str(calibDataSession)
                    idx2session[idx] = calibDataSession
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

        else:
            if slacNum not in self.cryoModules:
                prompt = "LERF cryomodule number for CM{CM}: ".format(CM=slacNum)
                cryoModule = Cryomodule(slacNum,
                                        getNumInputFromLst(prompt, [2, 3], int))
                self.cryoModules[slacNum] = cryoModule

        for _, cavity in self.cryoModules[slacNum].cavities.items():
            cavGradIdx = self.header.index("Cavity {NUM} Gradient"
                                           .format(NUM=cavity.cavNum))
            try:
                gradDes = float(row[cavGradIdx])
            except ValueError:
                continue

            desiredGradients[cavity.cavNum] = gradDes

        if selection == 2:
            for cavNum in desiredGradients.keys():
                cavity = self.cryoModules[slacNum].cavities[cavNum]
                if not cavity.calibDataSessions:
                    calibSess = self.cavManager.addNewCalibration(slacNum=slacNum,
                                                                  cavity=cavity)

                else:
                    options = {}
                    idx = 1
                    idx2session = {}

                    # Multiple rows in the input file may have the same SLAC
                    # cryomodule number. However, the user might want to use
                    # different calibrations. This is where we give the user the
                    # option to reuse a calibration we've already loaded up and
                    # processed.
                    calibDataSessions = cavity.calibDataSessions
                    for _, calibDataSession in calibDataSessions.items():
                        options[idx] = str(calibDataSession)
                        idx2session[idx] = calibDataSession
                        idx += 1

                    options[idx] = "Use a different calibration"
                    printOptions(options)

                    prompt = ("Please select a calibration option"
                              " (hit enter for option 1): ")
                    selection = getNumInputFromLst(prompt, options.keys(), int,
                                                   True)

                    reuseCalibration = (selection != max(options))

                    if not reuseCalibration:
                        calibSess = self.cavManager.addDataSession(slacNum=slacNum,
                                                                   container=cavity,
                                                                   kind="calib")

                    else:
                        calibSess = idx2session[selection]
        return calibSess, desiredGradients


class DataManager(object):
    __metaclass__ = ABCMeta

    def __init__(self, parent):
        # type: (InputFileParser) -> None

        self.parent = parent

        # Dicts of dicts where the format is:
        #   {[SLAC cryomodule number]:
        #       {[column name shorthand]: [index in the relevant CSV header]}}
        self.idxMap = {}
        self.q0IdxMap = {}

        # Used to populate cryModIdxMap and cavIdxMap. Each tuple in the list is
        # of the form: ([column name shorthand], [column title in the CSV])
        self.baseIdxKeys = [("startIdx", "Start"), ("endIdx", "End"),
                            ("refHeatIdx", "Reference Heat Load (Des)"),
                            ("refHeatActIdx", "Reference Heat Load (Act)"),
                            ("jtIdx", "JT Valve Position"),
                            ("timeIntIdx", "MySampler Time Interval")]

        self._idxKeys = None
        self._q0IdxKeys = None

    @property
    @abstractmethod
    def q0Header(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def calibHeader(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def idxKeys(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def q0IdxKeys(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def calibFileFormatter(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def q0FileFormatter(self):
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

        selectedRow = compatibleNext(reader([row]))
        refHeatLoad = float(selectedRow[self.idxMap[slacNum]["refHeatIdx"]])
        refHeatLoadAct = float(selectedRow[self.idxMap[slacNum]["refHeatActIdx"]])

        return selectedRow, refHeatLoad, refHeatLoadAct

    def populate(self, fileObj, slacNum, cavNum=None, header=None, kind=None):
        # type: (TextIO, int, int, List, str) -> None
        if not header:
            csvReader = reader(fileObj)
            header = compatibleNext(csvReader)
        indices = {}
        for key, column in (self.idxKeys if kind != "q0" else self.q0IdxKeys):
            indices[key] = header.index(column)
        if kind == "calib":
            self.idxMap[slacNum] = indices
        else:
            self.q0IdxMap[slacNum] = indices

    @abstractmethod
    def genSessionFile(self, slacNum, kind=None, cavNum=None):
        # type: (int, str, int) -> str
        raise NotImplementedError

    # Reads the header from a CSV and populates the idxMap dict passed in from
    # parseInputFile.
    def populateIdxMap(self, slacNum, kind=None, cavNum=None):
        # type: (int, str, int) -> None

        if kind == "q0":
            if slacNum not in self.q0IdxMap:
                sessionCSV = self.genSessionFile(slacNum, kind, cavNum)
                if not isfile(sessionCSV):
                    compatibleMkdirs(sessionCSV)
                    with open(sessionCSV, "w+") as f:
                        headerWriter = writer(f)
                        header = self.q0Header
                        headerWriter.writerow(header)
                        self.populate(f, slacNum, header=header, kind=kind)

                with open(sessionCSV) as csvFile:
                    self.populate(csvFile, slacNum, kind=kind)
        else:
            if slacNum not in self.idxMap:
                sessionCSV = self.genSessionFile(slacNum, kind, cavNum)
                if not isfile(sessionCSV):
                    compatibleMkdirs(sessionCSV)
                    with open(sessionCSV, "w+") as f:
                        headerWriter = writer(f)
                        header = self.calibHeader
                        headerWriter.writerow(header)
                        self.populate(f, slacNum, header=header, kind=kind)

                with open(sessionCSV) as csvFile:
                    self.populate(csvFile, slacNum, kind=kind)

    def getRowsAndFileReader(self, slacNum, kind=None, cavNum=None):
        # type: (int, str, int) -> Tuple

        sessionCSV = self.genSessionFile(slacNum, kind, cavNum)
        rows = open(compatibleMkdirs(sessionCSV)).readlines()
        # Reversing to get in chronological order (the program appends the most
        # recent sessions to the end of the file)
        rows.reverse()
        compatibleNext(reader([rows.pop()]))
        fileReader = reader(rows)
        return fileReader, rows


class CavityDataManager(DataManager):

    def __init__(self, parent):
        # type: (InputFileParser) -> None

        super(CavityDataManager, self).__init__(parent)

        # Dicts of dicts of dicts where the format is:
        #   {[SLAC cryomodule number]:
        #       {[cavity number]:
        #           {[column name shorthand]: [index in the relevant CSV header]}}
        self.idxMap = {}
        self.q0IdxMap = {}

    @property
    def q0Header(self):
        return ["Cavity", "Gradient", "JT Valve Position", "Start", "End",
                "Reference Heat Load (Des)", "Reference Heat Load (Act)",
                "MySampler Time Interval"]

    @property
    def calibHeader(self):
        return ["Cavity", "JT Valve Position", "Start", "End",
                "Reference Heat Load (Des)", "Reference Heat Load (Act)",
                "MySampler Time Interval"]

    @property
    def calibFileFormatter(self):
        return "calibrations/cm{CM_SLAC}/cav{CAV}/calibrationsCM{CM_SLAC}CAV{CAV}.csv"

    @property
    def q0FileFormatter(self):
        return "q0Measurements/cm{CM_SLAC}/cav{CAV}/q0MeasurementsCM{CM_SLAC}CAV{CAV}.csv"

    @property
    def idxKeys(self):
        if not self._idxKeys:
            self._idxKeys = self.baseIdxKeys + [("cavNumIdx", "Cavity")]
        return self._idxKeys

    @property
    def q0IdxKeys(self):
        if not self._q0IdxKeys:
            self._q0IdxKeys = self.baseIdxKeys + [("cavNumIdx", "Cavity"),
                                                  ("gradIdx", "Gradient")]
        return self._q0IdxKeys

    def addNewCalibration(self, slacNum, cavity):
        # type: (int, Cavity) -> CalibDataSession

        self.populateIdxMap(slacNum=slacNum, kind="calib", cavNum=cavity.cavNum)

        calibSession = self.addDataSession(slacNum=slacNum, container=cavity, kind="calib")

        # self.parent.cryoModules[slacNum] = calibSession.container

        return calibSession

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

        self.populateIdxMap(slacNum=slacNum, kind="q0", cavNum=cavity.cavNum)

        q0Session = self.addDataSessionAdv(slacNum=slacNum, container=cavity,
                                           idx=idx, calibSession=calibSession)

        q0Session.updateOutput()

    def genQ0Session(self, refGradVal, slacNum, cavity, calibSession):
        # type: (float, int, Cavity, CalibDataSession) -> Q0DataSession

        self.populateIdxMap(slacNum=slacNum, kind="q0", cavNum=cavity.cavNum)

        q0Session = self.addDataSession(slacNum=slacNum, container=cavity,
                                        refGradVal=refGradVal,
                                        calibSession=calibSession)

        q0Session.updateOutput()
        return q0Session

    def genSessionFile(self, slacNum, kind=None, cavNum=None):
        # type: (int, str, int) -> str

        if kind == "calib":
            return self.calibFileFormatter.format(CM_SLAC=slacNum, CAV=cavNum)
        else:
            return self.q0FileFormatter.format(CM_SLAC=slacNum, CAV=cavNum)

    # Reads the header from a CSV and populates the idxMap dict passed in from
    # parseInputFile.
    def populateIdxMap(self, slacNum, kind=None, cavNum=None):
        # type: (int, str, int) -> None

        if kind == "q0":
            missing = slacNum not in self.q0IdxMap or cavNum not in self.q0IdxMap[slacNum]
        else:
            missing = slacNum not in self.idxMap or cavNum not in self.idxMap[slacNum]

        if missing:
            sessionCSV = self.genSessionFile(slacNum, kind, cavNum)
            if not isfile(sessionCSV):
                compatibleMkdirs(sessionCSV)
                with open(sessionCSV, "w+") as f:
                    headerWriter = writer(f)
                    header = self.calibHeader if kind == "calib" else self.q0Header
                    headerWriter.writerow(header)
                    self.populate(f, slacNum, cavNum, header, kind)

            with open(sessionCSV) as csvFile:
                self.populate(csvFile, slacNum, cavNum=cavNum, kind=kind)

    def populate(self, fileObj, slacNum, cavNum=None, header=None, kind=None):
        # type: (TextIO, int, int, List, str) -> None
        if not header:
            csvReader = reader(fileObj)
            header = compatibleNext(csvReader)
        indices = {}
        for key, column in (self.idxKeys if kind != "q0" else self.q0IdxKeys):
            indices[key] = header.index(column)
        if kind == "calib":
            self.idxMap[slacNum] = {cavNum: indices}
        else:
            self.q0IdxMap[slacNum] = {cavNum: indices}

    def addDataSession(self, slacNum, container, refGradVal=None,
                       calibSession=None, kind="q0"):
        # type: (int, Cavity, float, CalibDataSession, str) -> DataSession

        indices = (self.idxMap[slacNum][container.cavNum]
                   if kind == "calib"
                   else self.q0IdxMap[slacNum][container.cavNum])

        fileReader, rows = self.getRowsAndFileReader(slacNum,
                                                     cavNum=container.cavNum,
                                                     kind=kind)

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

            grad = float(row[indices["gradIdx"]]) if kind == "q0" else None

            cavNum = int(row[indices["cavNumIdx"]])

            # The files are per cryomodule, so there's a lot of different
            # cavities in the file. We check to make sure that we're only
            # presenting the options for the requested cavity at the requested
            # gradient (by just skipping the irrelevant ones)
            if (grad and (grad != refGradVal)) or (cavNum != container.cavNum):
                continue

            addOption(csvRow=row, lineNum=fileReader.line_num, indices=indices,
                      options=options)

        if kind == "q0":
            selection = getSelection(duration=2, suffix="Q0 Measurement",
                                     options=options)
        else:
            selection = getSelection(duration=5, suffix="Calibration",
                                     options=options, name=container.name)

        # If using an existing data session
        if selection != max(options):
            selectedRow = compatibleNext(reader([rows[selection - 1]]))
            refHeatLoad = float(selectedRow[indices["refHeatIdx"]])
            refHeatLoadAct = float(selectedRow[indices["refHeatActIdx"]])
            return container.addDataSessionFromRow(selectedRow, indices,
                                                   refHeatLoad, refHeatLoadAct,
                                                   calibSession, refGradVal,
                                                   kind=kind)

        else:
            if kind == "q0":
                (Q0Sess,
                 self.parent.valveParams) = container.runQ0Meas(refGradVal,
                                                                calibSession,
                                                                self.valveParams)
                return Q0Sess
            else:
                (calibSess,
                 self.parent.valveParams) = container.runCalibration(self.valveParams)
                return calibSess


class CryModDataManager(DataManager):

    def __init__(self, parent):
        # type: (InputFileParser) -> None
        super(CryModDataManager, self).__init__(parent)

    @property
    def q0Header(self):
        return ["JLAB Number", "Reference Heat Load (Des)",
                "Reference Heat Load (Act)", "JT Valve Position",
                "Cavity 1 Gradient", "Cavity 2 Gradient", "Cavity 3 Gradient",
                "Cavity 4 Gradient", "Cavity 5 Gradient", "Cavity 6 Gradient",
                "Cavity 7 Gradient", "Cavity 8 Gradient", "Cumulative Gradient",
                "Start", "End", "MySampler Time Interval"]

    @property
    def calibHeader(self):
        return ["JLAB Number", "Reference Heat Load (Des)",
                "Reference Heat Load (Act)", "JT Valve Position",
                "Start", "End", "MySampler Time Interval"]

    @property
    def calibFileFormatter(self):
        return "calibrations/cm{CM_SLAC}/calibrationsCM{CM_SLAC}.csv"

    @property
    def q0FileFormatter(self):
        return "q0Measurements/cm{CM_SLAC}/q0MeasurementsCM{CM_SLAC}.csv"

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

    @property
    def idxKeys(self):
        if not self._idxKeys:
            self._idxKeys = self.baseIdxKeys + [("jlabNumIdx", "JLAB Number")]
        return self._idxKeys

    @property
    def q0IdxKeys(self):
        if not self._q0IdxKeys:
            self._q0IdxKeys = self.baseIdxKeys + [("jlabNumIdx", "JLAB Number"),
                                                  ("cav1GradIdx", "Cavity 1 Gradient"),
                                                  ("cav2GradIdx", "Cavity 2 Gradient"),
                                                  ("cav3GradIdx", "Cavity 3 Gradient"),
                                                  ("cav4GradIdx", "Cavity 4 Gradient"),
                                                  ("cav5GradIdx", "Cavity 5 Gradient"),
                                                  ("cav6GradIdx", "Cavity 6 Gradient"),
                                                  ("cav7GradIdx", "Cavity 7 Gradient"),
                                                  ("cav8GradIdx", "Cavity 8 Gradient"),
                                                  ("totGradIdx", "Cumulative Gradient")]
        return self._q0IdxKeys

    ############################################################################
    # addNewCryMod creates a new Cryomodule object and adds a data session to it
    # @param calibIdx: The row number in the target cryomodule's record of
    #                  previous calibrations. If it's None that means we're
    #                  using basic user input.
    # noinspection PyTypeChecker
    ############################################################################
    def addNewCalibration(self, slacNum, cavNum=None):
        # type: (int, int) -> CalibDataSession

        self.populateIdxMap(slacNum=slacNum, kind="calib", cavNum=cavNum)

        calibSession = self.addDataSession(slacNum=slacNum, container=None)

        self.parent.cryoModules[slacNum] = calibSession.container

        return calibSession

    def addNewCryModAdv(self, slacNum, calibIdx):
        # type: (int, int) -> CalibDataSession

        self.populateIdxMap(slacNum=slacNum, kind="calib")

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

    def addQ0DataSession(self, slacNum, container, desiredGradients=None,
                         calibSession=None):
        # type: (int, Cryomodule, Dict[int], CalibDataSession) -> Q0DataSession

        indices = self.q0IdxMap[slacNum]

        fileReader, rows = self.getRowsAndFileReader(slacNum, kind="q0")

        # Unclear if this is actually necessary, but the idea is to have the
        # output of json.dumps be ordered by index number
        options = OrderedDict()

        desiredGradient = 0

        for grad in desiredGradients.values():
            desiredGradient += grad ** 2

        for row in fileReader:

            # We could theoretically have hundreds of results, and that seems
            # like a seriously unnecessary number of options to show. This
            # asks the user if they want to keep searching for more every 10
            # hits
            # if (len(options) + 1) % 10 == 0:
            #     printOptions(options)
            #     showMore = isYes("Search for more options? ")
            #     if not showMore:
            #         break

            # The files are per cryomodule, so there's a lot of different
            # cavities in the file. We check to make sure that we're only
            # presenting the options for the requested cavity at the requested
            # gradient (by just skipping the irrelevant ones)
            if float(row[indices["totGradIdx"]]) != desiredGradient:
                continue

            addOption(csvRow=row, lineNum=fileReader.line_num, indices=indices,
                      options=options)

        selection = getSelection(duration=2, suffix="Q0 Measurement",
                                 options=options)

        # If using an existing data session
        if selection != max(options):
            selectedRow = compatibleNext(reader([rows[selection - 1]]))
            refHeatLoad = float(selectedRow[indices["refHeatIdx"]])
            refHeatLoadAct = float(selectedRow[indices["refHeatActIdx"]])
            return container.addDataSessionFromRow(selectedRow, indices,
                                                   refHeatLoad, refHeatLoadAct,
                                                   calibSession,
                                                   desiredGradients, kind="q0")

        else:
            (Q0Sess,
             self.parent.valveParams) = container.runQ0Meas(desiredGradients,
                                                            calibSession,
                                                            self.valveParams)
            return Q0Sess

    def genQ0Session(self, desiredGradients, slacNum, cryomodule, calibSession):
        # type: (Dict[int], int, Cryomodule, CalibDataSession) -> Q0DataSession

        self.populateIdxMap(slacNum=slacNum, kind="q0")

        q0Session = self.addQ0DataSession(slacNum=slacNum, container=cryomodule,
                                          desiredGradients=desiredGradients,
                                          calibSession=calibSession)

        q0Session.updateOutput()
        return q0Session

    def addDataSession(self, slacNum, container, refGradVal=None,
                       calibSession=None):
        # type: (int, Cryomodule, float, CalibDataSession) -> CalibDataSession

        indices = self.idxMap[slacNum]

        fileReader, rows = self.getRowsAndFileReader(slacNum, kind="calib")

        # Unclear if this is actually necessary, but the idea is to have the
        # output of json.dumps be ordered by index number
        options = OrderedDict()

        for row in fileReader:
            # if (len(options) + 1) % 10 == 0:
            #     printOptions(options)
            #     showMore = isYes("Search for more options? ")
            #     if not showMore:
            #         break

            addOption(csvRow=row, lineNum=fileReader.line_num, indices=indices,
                      options=options)

        selection = getSelection(duration=5, suffix="calibration",
                                 options=options)

        if selection != max(options):

            calibRow = compatibleNext(reader([rows[selection - 1]]))

            if not container:
                container = Cryomodule(slacNum,
                                       calibRow[indices["jlabNumIdx"]])

            refHeatLoad = float(calibRow[indices["refHeatIdx"]])
            refHeatLoadAct = float(calibRow[indices["refHeatActIdx"]])

            return container.addDataSessionFromRow(calibRow, indices,
                                                   refHeatLoad, refHeatLoadAct,
                                                   kind="calib")

        else:
            if not container:
                container = Cryomodule(slacNum,
                                       getNumInputFromLst("JLab cryomodule"
                                                          " number: ", [2, 3],
                                                          int))

            (calibSession,
             self.parent.valveParams) = container.runCalibration(self.parent.valveParams)

            return calibSession

    def genSessionFile(self, slacNum, kind=None, cavNum=None):
        # type: (int, str, int) -> str

        if kind == "calib":
            return self.calibFileFormatter.format(CM_SLAC=slacNum)
        else:
            return self.q0FileFormatter.format(CM_SLAC=slacNum)


if __name__ == "__main__":
    try:
        if TEST_MODE:
            AdvInputFileParser("testFiles/inputAdv.csv").parse()
        else:
            BasicInputFileParser("input.csv").parse()
    except KeyboardInterrupt:
        print("\n\n:(\n")
