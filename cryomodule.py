################################################################################
# Utility classes to hold calibration data for a cryomodule and Q0 measurement
# data for each of that cryomodule's cavities
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

class Cryomodule:
    def __init__(self, _cryModNumSLAC, _cryModNumJLAB, _calFileName,
                 _refValvePos, _refHeaterVal, _calCavNum=1):

        self.cryModNumSLAC = _cryModNumSLAC
        self.cryModNumJLAB = _cryModNumJLAB
        self.dataFileName = _calFileName
        self.refValvePos = _refValvePos
        self.refHeaterVal = _refHeaterVal
        self.calCavNum = _calCavNum

        jlabNumStr = str(self.cryModNumJLAB)
        self.valvePV = "CPV:CM0" + jlabNumStr + ":3001:JT:POS_RBV"
        self.dsLevelPV = "CLL:CM0" + jlabNumStr + ":2301:DS:LVL"
        self.usLevelPV = "CLL:CM0" + jlabNumStr + ":2601:US:LVL"

        # These buffers store calibration data read from a CSV
        self.unixTimeBuffer = []
        self.timeBuffer = []
        self.valvePosBuffer = []
        self.heatLoadBuffer = []
        self.downstreamLevelBuffer = []
        self.upstreamLevelBuffer = []

        # Maps this cryomodule's PV's to its corresponding data buffers
        self.pvBufferMap = {self.valvePV: self.valvePosBuffer,
                            self.dsLevelPV: self.downstreamLevelBuffer,
                            self.usLevelPV: self.upstreamLevelBuffer}

        self.cavities = {i: self.Cavity(self, i) for i in xrange(1, 9)}

    def getPVs(self):
        return [self.valvePV, self.dsLevelPV, self.usLevelPV,
                self.cavities[self.calCavNum - 1].heaterPV]

    class Cavity:
        def __init__(self, _parent, _cavNumber, _q0MeasFileName=""):
            self.parent = _parent

            self.cavityNumber = _cavNumber
            self.dataFileName = _q0MeasFileName

            self.heaterPV = ("CHTR:CM0" + str(_parent.cryModNumJLAB) + ":1"
                             + str(_cavNumber) + "55:HV:POWER")

            self.gradientPV = ("ACCL:L1B:0" + str(_parent.cryModNumJLAB)
                               + str(_cavNumber) + "0:GACT")

            # These buffers store Q0 measurement data read from a CSV
            self.unixTimeBuffer = []
            self.timeBuffer = []
            self.valvePosBuffer = []
            self.heatLoadBuffer = []
            self.downstreamLevelBuffer = []
            self.upstreamLevelBuffer = []
            self.gradientBuffer = []

            # Maps this cavity's PVs to its corresponding data buffers
            # (including a couple of PVs from its parent cryomodule)
            self.pvBufferMap = {self.parent.valvePV: self.valvePosBuffer,
                                self.parent.dsLevelPV:
                                    self.downstreamLevelBuffer,
                                self.parent.usLevelPV: self.upstreamLevelBuffer,
                                self.heaterPV: self.heatLoadBuffer,
                                self.gradientPV: self.gradientBuffer}

        def getPVs(self):
            return [self.parent.valvePV, self.parent.dsLevelPV,
                    self.parent.usLevelPV, self.gradientPV]

        @property
        def refValvePos(self):
            return self.parent.refValvePos

        @property
        def refHeaterVal(self):
            return self.parent.refHeaterVal

        @property
        def cryModNumSLAC(self):
            return self.parent.cryModNumSLAC


def main():
    cryomodule = Cryomodule(12, 2, "", 0, 0)
    for idx, cav in cryomodule.cavities.iteritems():
        print cav.heaterPV


if __name__ == '__main__':
    main()
