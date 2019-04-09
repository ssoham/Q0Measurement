################################################################################
# Utility classes to hold calibration data for a cryomodule, and Q0 measurement
# data for each of that cryomodule's cavities
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import print_function
from decimal import Decimal
from collections import OrderedDict


class Cryomodule:

    def __init__(self, cryModNumSLAC, cryModNumJLAB, calFileName,
                 refValvePos, refHeatLoad):

        self.cryModNumSLAC = cryModNumSLAC
        self.cryModNumJLAB = cryModNumJLAB
        self.dataFileName = calFileName
        self.refValvePos = refValvePos
        self.refHeatLoad = refHeatLoad

        self.name = self.addNumToStr("CM{CM}")

        self.dsPressurePV = self.addNumToStr("CPT:CM0{CM}:2302:DS:PRESS")
        self.jtModePV = self.addNumToStr("CPV:CM0{CM}:3001:JT:MODE")

        lvlFormatStr = self.addNumToStr("CLL:CM0{CM}:{{INFIX}}:{{LOC}}:LVL")

        self.dsLevelPV = lvlFormatStr.format(INFIX="2301", LOC="DS")
        self.usLevelPV = lvlFormatStr.format(INFIX="2601", LOC="US")

        self.cvMaxPV = self.addNumToStr("CPID:CM0{CM}:3001:JT:CV_{SUFF}", "MAX")
        self.cvMinPV = self.addNumToStr("CPID:CM0{CM}:3001:JT:CV_{SUFF}", "MIN")
        self.valvePV = self.addNumToStr("CPID:CM0{CM}:3001:JT:CV_{SUFF}",
                                        "VALUE")

        # These buffers store calibration data read from the CSV <dataFileName>
        self.unixTimeBuff = []
        self.timeBuff = []
        self.valvePosBuff = []
        self.dsLevelBuff = []
        self.usLevelBuff = []
        self.elecHeatDesBuff = []
        self.elecHeatActBuff = []

        # This buffer stores the heater calibration data runs as DataRun objects
        self.runs = []

        # Maps this cryomodule's PVs to its corresponding data buffers
        self.pvBuffMap = {self.valvePV: self.valvePosBuff,
                          self.dsLevelPV: self.dsLevelBuff,
                          self.usLevelPV: self.usLevelBuff}

        # Give each cryomodule 8 cavities
        cavities = {}

        self.heaterDesPVs = []
        self.heaterActPVs = []

        heaterDesStr = self.addNumToStr("CHTR:CM0{CM}:1{{CAV}}55:HV:{SUFF}",
                                        "POWER_SETPT")
        heaterActStr = self.addNumToStr("CHTR:CM0{CM}:1{{CAV}}55:HV:{SUFF}",
                                        "POWER")

        for i in range(1, 9):
            cavities[i] = self.Cavity(parent=self, cavNumber=i)
            self.heaterDesPVs.append(heaterDesStr.format(CAV=i))
            self.heaterActPVs.append(heaterActStr.format(CAV=i))

        self.cavities = OrderedDict(sorted(cavities.items()))

        # These characterize the cryomodule's overall heater calibration curve
        self.calibSlope = None
        self.calibIntercept = None

    def addNumToStr(self, formatStr, suffix=None):
        if suffix:
            return formatStr.format(CM=self.cryModNumJLAB, SUFF=suffix)
        else:
            return formatStr.format(CM=self.cryModNumJLAB)

    # Returns a list of the PVs used for its data acquisition, including
    # the PV of the cavity heater used for calibration
    def getPVs(self):
        return ([self.valvePV, self.dsLevelPV, self.usLevelPV]
                + self.heaterDesPVs + self.heaterActPVs)

    @property
    def runElecHeatLoads(self):
        return [run.elecHeatLoad for run in self.runs]

    @property
    def runSlopes(self):
        return [run.slope for run in self.runs]

    class Cavity:
        def __init__(self, parent, cavNumber):
            self.parent = parent

            self.name = "Cavity {cavNum}".format(cavNum=cavNumber)
            self.cavNum = cavNumber
            self.dataFileName = None

            self.refGradVal = None
            self.refValvePos = None

            self.refHeatLoad = self.parent.refHeatLoad

            self.offset = 0

            # These buffers store Q0 measurement data read from the CSV
            # <dataFileName>
            self.unixTimeBuff = []
            self.timeBuff = []
            self.valvePosBuff = []
            self.dsLevelBuff = []
            self.usLevelBuff = []
            self.gradBuff = []
            self.dsPressBuff = []
            self.elecHeatDesBuff = []
            self.elecHeatActBuff = []

            # This buffer stores the heater calibration data runs as Q0DataRun
            # objects
            self.runs = []

            # Maps this cavity's PVs to its corresponding data buffers
            # (including a couple of PVs from its parent cryomodule)
            self.pvBuffMap = {self.parent.valvePV: self.valvePosBuff,
                              self.parent.dsLevelPV: self.dsLevelBuff,
                              self.parent.usLevelPV: self.usLevelBuff,
                              self.gradPV: self.gradBuff,
                              self.parent.dsPressurePV: self.dsPressBuff}

        def genPV(self, formatStr, suffix):
            return formatStr.format(CM=self.cryModNumJLAB, CAV=self.cavNum,
                                    SUFF=suffix)

        def genAcclPV(self, suffix):
            return self.genPV("ACCL:L1B:0{CM}{CAV}0:{SUFF}", suffix)

        # Similar to the Cryomodule function, it just has the gradient PV
        # instead of the heater PV
        def getPVs(self):
            return ([self.parent.valvePV, self.parent.dsLevelPV,
                     self.parent.usLevelPV, self.gradPV,
                     self.parent.dsPressurePV] + self.parent.heaterPVs
                    + self.parent.heaterActPVs)

        def printReport(self):
            # TODO handle white space more elegantly

            reportStr = ("\n{cavName} run {runNum} total heat load: {TOT} W\n"
                         "            Electric heat load: {ELEC} W\n"
                         "                  RF heat load: {RF} W\n"
                         "                 Calculated Q0: {{Q0Val}}\n")


            for i, run in enumerate(self.runs):
                report = reportStr.format(cavName=self.name, runNum=(i + 1),
                                          TOT=round(run.totalHeatLoad, 2),
                                          ELEC=round(run.elecHeatLoad, 2),
                                          RF=round(run.rfHeatLoad, 2))

                if run.q0:
                    Q0 = '{:.2e}'.format(Decimal(run.q0))
                    print(report.format(Q0Val=Q0))
                else:
                    print(report.format(Q0Val=None))

        # The @property annotation is effectively a shortcut for defining a
        # class variable and giving it a custom getter function (so now
        # whenever someone calls Cavity.cryModNumSLAC, it'll return the parent
        # value)
        @property
        def cryModNumSLAC(self):
            return self.parent.cryModNumSLAC

        @property
        def runElecHeatLoads(self):
            return [run.elecHeatLoad for run in self.runs]

        @property
        def runHeatLoads(self):
            return [run.totalHeatLoad for run in self.runs
                    if run.elecHeatLoadDes == 0]

        @property
        def adjustedRunSlopes(self):
            m = self.parent.calibSlope
            b = self.parent.calibIntercept
            return [(m * run.totalHeatLoad) + b for run in self.runs
                    if run.elecHeatLoadDes == 0]

        @property
        def cryModNumJLAB(self):
            return self.parent.cryModNumJLAB

        @property
        def gradPV(self):
            return self.genAcclPV("GACT")

        @property
        def valvePV(self):
            return self.parent.valvePV

        @property
        def dsLevelPV(self):
            return self.parent.dsLevelPV

        @property
        def jtModePV(self):
            return self.parent.jtModePV

        @property
        def cvMaxPV(self):
            return self.parent.cvMaxPV

        @property
        def cvMinPV(self):
            return self.parent.cvMinPV

        @property
        def heaterPVs(self):
            return self.parent.heaterPVs

        @property
        def heaterActPVs(self):
            return self.parent.heaterActPVs


# There are two types of data runs that we need to store - cryomodule heater
# calibration runs and cavity Q0 measurement runs. The DataRun class stores
# information that is common to both data run types.
class DataRun(object):

    def __init__(self, runStartIdx=None, runEndIdx=None):
        # startIdx and endIdx define the beginning and the end of this data run
        # within the cryomodule or cavity's data buffers
        self.startIdx = runStartIdx
        self.endIdx = runEndIdx

        # All data runs have liquid level information which gets fitted with a
        # line (giving us dLL/dt). The slope and intercept parametrize the line.
        self.slope = None
        self.intercept = None

        # elecHeatLoad is the electric heat load over baseline for this run
        self.elecHeatLoad = None

        self.elecHeatLoadDes = None


# Q0DataRun stores all the information about cavity Q0 measurement runs that
# isn't included in the parent class DataRun
class Q0DataRun(DataRun):

    def __init__(self, runStartIdx=None, runEndIdx=None):
        super(Q0DataRun, self).__init__(runStartIdx, runEndIdx)

        # Q0 measurement runs have a total heat load value which we calculate
        # by projecting the run's dLL/dt on the cryomodule's heater calibration
        # curve
        self.totalHeatLoad = None

        # The RF heat load is equal to the total heat load minus the electric
        # heat load
        self.rfHeatLoad = None

        # The average gradient
        self.grad = None

        # The calculated Q0 value for this run
        self.q0 = None


def main():
    cryomodule = Cryomodule(cryModNumSLAC=12, cryModNumJLAB=2, calFileName="",
                            refValvePos=0, refHeatLoad=0)
    for idx, cav in cryomodule.cavities.items():
        print(cav.gradientPV)
        print(cav.heaterPV)


if __name__ == '__main__':
    main()
