################################################################################
# Utility classes to hold calibration data for a cryomodule, and Q0 measurement
# data for each of that cryomodule's cavities
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

from decimal import Decimal


# TODO add a run class that stores the floats associated with each run.

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
        self.unixTimeBuffer = []
        self.timeBuffer = []
        self.valvePosBuffer = []
        self.heaterBuffer = []
        self.dsLevelBuffer = []
        self.usLevelBuffer = []

        # Maps this cryomodule's PVs to its corresponding data buffers
        self.pvBufferMap = {self.valvePV: self.valvePosBuffer,
                            self.dsLevelPV: self.dsLevelBuffer,
                            self.usLevelPV: self.usLevelBuffer}

        # Give each cryomodule 8 cavities
        self.cavities = {i: self.Cavity(parent=self, cavNumber=i)
                         for i in range(1, 9)}

        # TODO store runs as a list of run objects instead
        # self.runs = []

        # This buffer stores lists of pairs of indices. The first marks the
        # start of a heater calibration data run and the second marks the end.
        self.runIndices = []

        # This buffer stores the dLL/dt value for each run
        self.runSlopes = []

        # This buffer stores the electric heat load over baseline for each
        # run (defined as the calibration cavity heater setting - the ref heater
        # value)
        self.runElecHeatLoads = []

        # These characterize the cryomodule's overall heater calibration curve
        self.calibSlope = None
        self.calibIntercept = None

    # Returns a list of the PVs used for its data acquisition, including
    # the PV of the cavity heater used for calibration
    def getPVs(self):
        return [self.valvePV, self.dsLevelPV, self.usLevelPV,
                self.cavities[self.calCavNum].heaterPV]

    class Cavity:
        def __init__(self, parent, cavNumber):
            self.parent = parent

            self.name = "Cavity {cavNum}".format(cavNum=cavNumber)
            self.cavNum = cavNumber
            self.dataFileName = None

            self.refGradientVal = None
            self.refValvePos = None

            heaterPVStr = "CHTR:CM0{cryModNum}:1{cavNum}55:HV:POWER"
            self.heaterPV = heaterPVStr.format(cryModNum=parent.cryModNumJLAB,
                                               cavNum=cavNumber)

            gradientPVStr = "ACCL:L1B:0{cryModNum}{cavNum}0:GACT"
            self.gradientPV = gradientPVStr.format(
                cryModNum=parent.cryModNumJLAB,
                cavNum=cavNumber)

            # These buffers store Q0 measurement data read from the CSV
            # <dataFileName>
            self.unixTimeBuffer = []
            self.timeBuffer = []
            self.valvePosBuffer = []
            self.heaterBuffer = []
            self.dsLevelBuffer = []
            self.usLevelBuffer = []
            self.gradientBuffer = []
            self.dsPressureBuffer = []

            # Maps this cavity's PVs to its corresponding data buffers
            # (including a couple of PVs from its parent cryomodule)
            self.pvBufferMap = {self.parent.valvePV: self.valvePosBuffer,
                                self.parent.dsLevelPV:
                                    self.dsLevelBuffer,
                                self.parent.usLevelPV: self.usLevelBuffer,
                                self.heaterPV: self.heaterBuffer,
                                self.gradientPV: self.gradientBuffer,
                                self.parent.dsPressurePV:
                                    self.dsPressureBuffer}

            # TODO store runs as a list of run objects instead
            # self.runs = []

            # This buffer stores lists defining the Q0 measurement runs, each of
            # which has the form [run start index, run end index].
            self.runIndices = []

            # This buffer stores the dLL/dt value for each run
            self.runSlopes = []

            # This buffer stores the total heat load over baseline for each run
            # (calculated from dLL/dt and the calibration curve)
            self.runHeatLoads = []

            # This buffer stores the electric heat load for each run (defined as
            # the cavity heater setting - the ref heater val)
            self.runElecHeatLoads = []

            # This buffer stores the RF heat load over baseline for each
            # run (defined as the total heat load over baseline - the run's
            # electric heat load)
            self.runRFHeatLoads = []

            # This buffer stores the weighted gradient value for each run (Q0
            # scales with the gradient squared so this number is scaled to
            # reflect that)
            self.runGradients = []

            # This buffer stores the average pressure value for the incoming
            # 2 K helium for each run.
            self.runPressures = []

            # This buffer stores the calculated Q0 value for each run
            self.runQ0s = []

        def __str__(self):
            # TODO clean this print function up

            report = ""

            for idx, heatLoad in enumerate(self.runHeatLoads):
                line1 = "\n{cavName} run {runNum} total heat load: {heat} W\n"
                report += (line1.format(cavName=self.name, runNum=idx + 1,
                                        heat=round(heatLoad, 2)))

                line2 = "            Electric heat load: {heat} W\n"
                report += (line2.format(heat=round(self.runElecHeatLoads[idx],
                                                   2)))

                line3 = "                  RF heat load: {heat} W\n"
                report += (line3.format(heat=round(self.runRFHeatLoads[idx],
                                                   2)))

                line4 = "                 Calculated Q0: {q0Val}\n"
                q0 = '{:.2e}'.format(Decimal(self.runQ0s[idx]))
                report += (line4.format(q0Val=q0))

            return report

        # Similar to the Cryomodule function, it just has the gradient PV
        # instead of the heater PV
        def getPVs(self):
            return [self.parent.valvePV, self.parent.dsLevelPV,
                    self.parent.usLevelPV, self.gradientPV, self.heaterPV,
                    self.parent.dsPressurePV]

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


def main():
    cryomodule = Cryomodule(cryModNumSLAC=12, cryModNumJLAB=2, calFileName="",
                            refValvePos=0, refHeaterVal=0)
    for idx, cav in cryomodule.cavities.iteritems():
        print cav.gradientPV
        print cav.heaterPV


if __name__ == '__main__':
    main()
