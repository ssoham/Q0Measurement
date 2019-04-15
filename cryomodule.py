################################################################################
# Utility classes to hold calibration data for a cryomodule, and Q0 measurement
# data for each of that cryomodule's cavities
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import print_function
from decimal import Decimal
from collections import OrderedDict

from numpy import mean, exp


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
        self.jtPosSetpointPV = self.addNumToStr("CPV:CM0{CM}:3001:JT:POS_SETPT")

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

        self.delta = 0

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

    def addRun(self, startIdx, endIdx):
        self.runs.append(DataRun(startIdx, endIdx, self))

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
            self.delta = 0

            self.refHeatLoad = self.parent.refHeatLoad

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
                     self.parent.dsPressurePV] + self.parent.heaterDesPVs
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

        def addRun(self, startIdx, endIdx):
            self.runs.append(Q0DataRun(startIdx, endIdx, self))

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
        def jtPosSetpointPV(self):
            return self.parent.jtPosSetpointPV

        @property
        def heaterPVs(self):
            return self.parent.heaterPVs

        @property
        def heaterActPVs(self):
            return self.parent.heaterActPVs

        @property
        def calibIntercept(self):
            return self.parent.calibIntercept

        @property
        def calibSlope(self):
            return self.parent.calibSlope

        @property
        def offset(self):
            offsets = []

            for run in self.runs:
                runOffset = run.offset
                if runOffset:
                    offsets.append(runOffset)

            return mean(offsets) if offsets else 0


# There are two types of data runs that we need to store - cryomodule heater
# calibration runs and cavity Q0 measurement runs. The DataRun class stores
# information that is common to both data run types.
class DataRun(object):

    def __init__(self, runStartIdx=None, runEndIdx=None, container=None):
        # startIdx and endIdx define the beginning and the end of this data run
        # within the cryomodule or cavity's data buffers
        self.startIdx = runStartIdx
        self.endIdx = runEndIdx

        # All data runs have liquid level information which gets fitted with a
        # line (giving us dLL/dt). The slope and intercept parametrize the line.
        self.slope = None
        self.intercept = None

        self.container = container

    @property
    def data(self):
        return self.container.dsLevelBuff[self.startIdx:self.endIdx]

    @property
    def times(self):
        return self.container.unixTimeBuff[self.startIdx:self.endIdx]

    # elecHeatLoad is the electric heat load over baseline for this run
    @property
    def elecHeatLoad(self):
        return (self.container.elecHeatActBuff[self.endIdx]
                - self.container.elecHeatActBuff[0]) + self.container.delta

    @property
    def elecHeatLoadDes(self):
        return (self.container.elecHeatDesBuff[self.endIdx]
                - self.container.refHeatLoad)

    @property
    def label(self):
        labelStr = "{slope} %/s @ {heatLoad} W Electric Load"

        return labelStr.format(slope='%.2E' % Decimal(self.slope),
                               heatLoad=round(self.elecHeatLoad, 2))


# Q0DataRun stores all the information about cavity Q0 measurement runs that
# isn't included in the parent class DataRun
class Q0DataRun(DataRun):

    def __init__(self, runStartIdx=None, runEndIdx=None, cavity=None):
        # type: (int, int, Cryomodule.Cavity) -> None
        super(Q0DataRun, self).__init__(runStartIdx, runEndIdx, cavity)

        # The average gradient
        self.grad = None

    # Q0 measurement runs have a total heat load value which we calculate
    # by projecting the run's dLL/dt on the cryomodule's heater calibration
    # curve
    @property
    def totalHeatLoad(self):
        if self.elecHeatLoadDes != 0:
            return self.elecHeatLoad
        else:
            return ((self.slope - self.container.calibIntercept)
                    / self.container.calibSlope) + self.container.offset

    # The RF heat load is equal to the total heat load minus the electric
    # heat load
    @property
    def rfHeatLoad(self):
        if self.elecHeatLoadDes != 0:
            return 0
        else:
            return self.totalHeatLoad - self.elecHeatLoad

    @property
    def offset(self):
        if self.elecHeatLoadDes != 0:
            calcHeatLoad = ((self.slope - self.container.calibIntercept)
                            / self.container.calibSlope)
            return (self.elecHeatLoad - calcHeatLoad)
        else:
            return None

    # The calculated Q0 value for this run
    # Magical formula from Mike Drury (drury@jlab.org) to calculate Q0 from the
    # measured heat load on a cavity, the RF gradient used during the test, and
    # the pressure of the incoming 2 K helium.
    @property
    def q0(self):
        if self.elecHeatLoadDes != 0:
            return None

        q0s = []

        if self.container.dsPressBuff:
            for idx in range(self.startIdx, self.endIdx):
                if self.container.gradBuff[idx]:
                    q0s.append(self.calcQ0(self.container.gradBuff[idx],
                                           self.rfHeatLoad,
                                           self.container.dsPressBuff[idx]))
                else:
                    q0s.append(self.calcQ0(self.container.refGradVal,
                                           self.rfHeatLoad,
                                           self.container.dsPressBuff[idx]))
        else:
            for idx in range(self.startIdx, self.endIdx):
                if self.container.gradBuff[idx]:
                    q0s.append(self.calcQ0(self.container.gradBuff[idx],
                                           self.rfHeatLoad))
                else:
                    q0s.append(self.calcQ0(self.container.refGradVal,
                                           self.rfHeatLoad))

        return mean(q0s)

    @property
    def label(self):
        # This is a heater run. It could be part of a cryomodule heater
        # calibration or it could be part of a cavity Q0 measurement.
        if self.elecHeatLoadDes != 0:
            return super(Q0DataRun, self).label

        # This is an RF run taken during a cavity Q0 measurement.
        else:

            labelStr = "{slope} %/s @ {grad} MV/m\nCalculated Q0: {Q0}"
            q0Str = '{:.2e}'.format(Decimal(self.q0))

            return labelStr.format(slope='%.2E' % Decimal(self.slope),
                                   grad=self.container.refGradVal, Q0=q0Str)

    @staticmethod
    def calcQ0(grad, rfHeatLoad, avgPressure=None):
        # The initial Q0 calculation doesn't account for the temperature variation
        # of the 2 K helium
        uncorrectedQ0 = ((grad * 1000000) ** 2) / (939.3 * rfHeatLoad)

        # We can correct Q0 for the helium temperature!
        if avgPressure:
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
                                - (C7 / tempFromPress) * exp(
                        C6 / tempFromPress))
            return correctedQ0

        else:
            return uncorrectedQ0


def main():
    cryomodule = Cryomodule(cryModNumSLAC=12, cryModNumJLAB=2, calFileName="",
                            refValvePos=0, refHeatLoad=0)
    for idx, cav in cryomodule.cavities.items():
        print(cav.gradientPV)
        print(cav.heaterPV)


if __name__ == '__main__':
    main()
