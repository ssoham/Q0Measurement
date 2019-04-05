################################################################################
# Utility classes to hold calibration data for a cryomodule, and Q0 measurement
# data for each of that cryomodule's cavities
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import print_function
from decimal import Decimal
from collections import OrderedDict

class Cryomodule:
    # We're assuming that the convention is going to be to run the calibration
    # on cavity 1, but we're leaving some wiggle room in case
    def __init__(self, cryModNumSLAC, cryModNumJLAB, calFileName,
                 refValvePos, refHeatLoad):

        self.name = "CM{cryModNum}".format(cryModNum=cryModNumSLAC)
        self.cryModNumSLAC = cryModNumSLAC
        self.cryModNumJLAB = cryModNumJLAB
        self.dataFileName = calFileName
        self.refValvePos = refValvePos
        self.refHeatLoad = refHeatLoad

        jlabNumStr = str(self.cryModNumJLAB)
        self.dsPressurePV = "CPT:CM0" + jlabNumStr + ":2302:DS:PRESS"
        self.jtModePV = "CPV:CM0" + jlabNumStr + ":3001:JT:MODE"
        self.cvFormatter = "CPID:CM0" + jlabNumStr + ":3001:JT:CV_{SUFFIX}"
        self.valvePV = "CPID:CM0{CM}:3001:JT:CV_VALUE".format(CM=cryModNumJLAB)
        self.dsLevelPV = "CLL:CM0{CM}:2301:DS:LVL".format(CM=cryModNumJLAB)
        self.usLevelPV = "CLL:CM0{CM}:2601:US:LVL".format(CM=cryModNumJLAB)

        # These buffers store calibration data read from the CSV <dataFileName>
        self.unixTimeBuff = []
        self.timeBuff = []
        self.valvePosBuff = []
        # self.heaterBuff = []
        self.dsLevelBuff = []
        self.usLevelBuff = []

        self.elecHeatBuff = []

        self.elecHeatActBuff = []

        # This buffer stores the heater calibration data runs as DataRun objects
        self.runs = []

        # Maps this cryomodule's PVs to its corresponding data buffers
        self.pvBuffMap = {self.valvePV: self.valvePosBuff,
                          self.dsLevelPV: self.dsLevelBuff,
                          self.usLevelPV: self.usLevelBuff}

        # Give each cryomodule 8 cavities
        cavities = {i: self.Cavity(parent=self, cavNumber=i)
                    for i in range(1, 9)}

        self.cavities = OrderedDict(sorted(cavities.items()))

        # These characterize the cryomodule's overall heater calibration curve
        self.calibSlope = None
        self.calibIntercept = None

        self.cvMaxPV = self.cvFormatter.format(SUFFIX="MAX")
        self.cvMinPV = self.cvFormatter.format(SUFFIX="MIN")
        
        heaterFormatStr = "CHTR:CM0{CM}:1{CAV}55:HV:POWER_SETPT"

        self.heaterPVs = [heaterFormatStr.format(CM=cryModNumJLAB, CAV=cav)
                          for cav in self.cavities]

        heaterActFormatStr = "CHTR:CM0{CM}:1{CAV}55:HV:POWER"

        self.heaterActPVs = [heaterActFormatStr.format(CM=cryModNumJLAB, CAV=cav)
                          for cav in self.cavities]

    # def genHeaterPV(self, suffix):
    #     return self.genPV("CHTR:CM0{CM}:1{CAV}55:HV:{SUFFIX}", suffix)

    # Returns a list of the PVs used for its data acquisition, including
    # the PV of the cavity heater used for calibration
    def getPVs(self):
        return ([self.valvePV, self.dsLevelPV, self.usLevelPV]
                + self.heaterPVs + self.heaterActPVs)

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

            # These buffers store Q0 measurement data read from the CSV
            # <dataFileName>
            self.unixTimeBuff = []
            self.timeBuff = []
            self.valvePosBuff = []
            #self.heaterBuff = []
            self.dsLevelBuff = []
            self.usLevelBuff = []
            self.gradBuff = []
            self.dsPressBuff = []
            self.elecHeatBuff = []

            self.elecHeatActBuff = []

            # This buffer stores the heater calibration data runs as Q0DataRun
            # objects
            self.runs = []

            # Maps this cavity's PVs to its corresponding data buffers
            # (including a couple of PVs from its parent cryomodule)
            self.pvBuffMap = {self.parent.valvePV: self.valvePosBuff,
                              self.parent.dsLevelPV:
                                  self.dsLevelBuff,
                              self.parent.usLevelPV: self.usLevelBuff,
                              self.gradPV: self.gradBuff,
                              self.parent.dsPressurePV:
                                  self.dsPressBuff}

        def genPV(self, formatStr, suffix):
            return formatStr.format(CM=self.cryModNumJLAB,
                                    CAV=self.cavNum,
                                    SUFFIX=suffix)

        def genAcclPV(self, suffix):
            return self.genPV("ACCL:L1B:0{CM}{CAV}0:{SUFFIX}", suffix)

        # Similar to the Cryomodule function, it just has the gradient PV
        # instead of the heater PV
        def getPVs(self):
            return ([self.parent.valvePV, self.parent.dsLevelPV,
                    self.parent.usLevelPV, self.gradPV,
                    self.parent.dsPressurePV] + self.parent.heaterPVs
                    + self.parent.heaterActPVs)

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
        def refHeatLoad(self):
            return self.parent.refHeatLoad

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

        @property
        def cryModNumJLAB(self):
            return self.parent.cryModNumJLAB

        @property
        def gradPV(self):
            return self.genAcclPV("GACT")

        #@property
        #def heaterPV(self):
            #return self.genPV("CHTR:CM0{CM}:1{CAV}55:HV:{SUFFIX}", "POWER_SETPT")

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
