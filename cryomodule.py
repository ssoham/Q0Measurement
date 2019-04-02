################################################################################
# Utility classes to hold calibration data for a cryomodule, and Q0 measurement
# data for each of that cryomodule's cavities
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

from decimal import Decimal


class Cryomodule:
    # We're assuming that the convention is going to be to run the calibration
    # on cavity 1, but we're leaving some wiggle room in case
    def __init__(self, cryModNumSLAC, cryModNumJLAB, calFileName,
                 refValvePos, refHeaterVal, calCavNum=1):
        self.name = "CM{cryModNum}".format(cryModNum=cryModNumSLAC)
        self.cryModNumSLAC = cryModNumSLAC
        self.cryModNumJLAB = cryModNumJLAB
        self.dataFileName = calFileName
        self.refValvePos = refValvePos
        self.refHeaterVal = refHeaterVal
        self.calCavNum = calCavNum

        jlabNumStr = str(self.cryModNumJLAB)
        self.valvePV = "CPV:CM0" + jlabNumStr + ":3001:JT:POS_RBV"
        self.dsLevelPV = "CLL:CM0" + jlabNumStr + ":2301:DS:LVL"
        self.usLevelPV = "CLL:CM0" + jlabNumStr + ":2601:US:LVL"
        self.dsPressurePV = "CPT:CM0" + jlabNumStr + ":2302:DS:PRESS"

        # These buffers store calibration data read from the CSV <dataFileName>
        self.unixTimeBuff = []
        self.timeBuff = []
        self.valvePosBuff = []
        self.heaterBuff = []
        self.dsLevelBuff = []
        self.usLevelBuff = []

        # This buffer stores the heater calibration data runs as DataRun objects
        self.runs = []

        # Maps this cryomodule's PVs to its corresponding data buffers
        self.pvBuffMap = {self.valvePV: self.valvePosBuff,
                          self.dsLevelPV: self.dsLevelBuff,
                          self.usLevelPV: self.usLevelBuff}

        # Give each cryomodule 8 cavities
        self.cavities = {i: self.Cavity(parent=self, cavNumber=i)
                         for i in range(1, 9)}

        # These characterize the cryomodule's overall heater calibration curve
        self.calibSlope = None
        self.calibIntercept = None

    # Returns a list of the PVs used for its data acquisition, including
    # the PV of the cavity heater used for calibration
    def getPVs(self):
        return [self.valvePV, self.dsLevelPV, self.usLevelPV,
                self.cavities[self.calCavNum].heaterPV]

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

            heaterPVStr = "CHTR:CM0{cryModNum}:1{cavNum}55:HV:POWER"
            self.heaterPV = heaterPVStr.format(cryModNum=parent.cryModNumJLAB,
                                               cavNum=cavNumber)

            gradPVStr = "ACCL:L1B:0{cryModNum}{cavNum}0:GACT"
            self.gradPV = gradPVStr.format(
                cryModNum=parent.cryModNumJLAB,
                cavNum=cavNumber)

            # These buffers store Q0 measurement data read from the CSV
            # <dataFileName>
            self.unixTimeBuff = []
            self.timeBuff = []
            self.valvePosBuff = []
            self.heaterBuff = []
            self.dsLevelBuff = []
            self.usLevelBuff = []
            self.gradBuff = []
            self.dsPressureBuff = []

            # This buffer stores the heater calibration data runs as Q0DataRun
            # objects
            self.runs = []

            # Maps this cavity's PVs to its corresponding data buffers
            # (including a couple of PVs from its parent cryomodule)
            self.pvBuffMap = {self.parent.valvePV: self.valvePosBuff,
                              self.parent.dsLevelPV:
                                  self.dsLevelBuff,
                              self.parent.usLevelPV: self.usLevelBuff,
                              self.heaterPV: self.heaterBuff,
                              self.gradPV: self.gradBuff,
                              self.parent.dsPressurePV:
                                  self.dsPressureBuff}

        # Similar to the Cryomodule function, it just has the gradient PV
        # instead of the heater PV
        def getPVs(self):
            return [self.parent.valvePV, self.parent.dsLevelPV,
                    self.parent.usLevelPV, self.gradPV, self.heaterPV,
                    self.parent.dsPressurePV]

        def printReport(self):
            # TODO handle white space more elegantly

            report = ""

            for i, run in enumerate(self.runs):
                line1 = "\n{cavName} run {runNum} total heat load: {heat} W\n"
                report += (line1.format(cavName=self.name, runNum=(i + 1),
                                        heat=round(run.totalHeatLoad, 2)))

                line2 = "            Electric heat load: {heat} W\n"
                report += (line2.format(heat=round(run.elecHeatLoad, 2)))

                line3 = "                  RF heat load: {heat} W\n"
                report += (line3.format(heat=round(run.rfHeatLoad, 2)))

                line4 = "                 Calculated Q0: {Q0Val}\n"
                Q0 = '{:.2e}'.format(Decimal(run.q0))
                report += (line4.format(Q0Val=Q0))

            print(report)

        # The @property annotation is effectively a shortcut for defining a
        # class variable and giving it a custom getter function (so now
        # whenever someone calls Cavity.refValvePos, it'll return the parent
        # value)
        @property
        def refHeaterVal(self):
            return self.parent.refHeaterVal

        @property
        def cryModNumSLAC(self):
            return self.parent.cryModNumSLAC

        @property
        def runElecHeatLoads(self):
            return [run.elecHeatLoad for run in self.runs]

        @property
        def runHeatLoads(self):
            return [run.totalHeatLoad for run in self.runs]

        @property
        def runSlopes(self):
            return [run.slope for run in self.runs]


# There are two types of data runs that we need to store - cryomodule heater
# calibration runs and cavity Q0 measurement runs. The DataRun class stores
# information that is common to both data run types.
class DataRun:

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


# Q0DataRun stores all the information about cavity Q0 measurement runs that
# isn't included in the parent class DataRun
class Q0DataRun(DataRun):

    def __init__(self, runStartIdx=None, runEndIdx=None):

        DataRun.__init__(self, runStartIdx, runEndIdx)

        # Q0 measurement runs have a total heat load value which we calculate
        # by projecting the run's dLL/dt on the cryomodule's heater calibration
        # curve
        self.totalHeatLoad = None

        # The RF heat load is equal to the total heat load minus the electric
        # heat load
        self.rfHeatLoad = None

        # avgGrad is the weighted average RF gradient for this run (Q0 scales
        # with the gradient squared so this number isn't just a simple average)
        self.avgGrad = None

        # avgPress is the average pressure value for the incoming 2 K helium for
        # this run
        self.avgPress = None

        # The calculated Q0 value for this run
        self.q0 = None


def main():
    cryomodule = Cryomodule(cryModNumSLAC=12, cryModNumJLAB=2, calFileName="",
                            refValvePos=0, refHeaterVal=0)
    for idx, cav in cryomodule.cavities.iteritems():
        print cav.gradPV
        print cav.heaterPV


if __name__ == '__main__':
    main()
