class Cryomodule:
    def __init__(self, slacNumber, jlabNumber, _calFileName, _refValvePos,
                 _refHeaterVal, _calCavNum=1):
        self.slacNum = slacNumber
        self.jlabNum = jlabNumber
        self.calFileName = _calFileName
        self.refValvePos = _refValvePos
        self.refHeaterVal = _refHeaterVal
        self.calCavNum = _calCavNum

        jlabNumStr = str(self.jlabNum)
        self.valvePV = "CPV:CM0" + jlabNumStr + ":3001:JT:POS_RBV"
        self.dsLevelPV = "CLL:CM0" + jlabNumStr + ":2301:DS:LVL"
        self.usLevelPV = "CLL:CM0" + jlabNumStr + ":2601:US:LVL"

        self.calTime = []
        # self.calUnixTime = []
        self.calValvePos = []
        self.calHeatLoad = []
        self.calDownstreamLevel = []
        self.calUpstreamLevel = []

        self.pvBufferMap = {self.valvePV: self.calValvePos,
                            self.dsLevelPV: self.calDownstreamLevel,
                            self.usLevelPV: self.calUpstreamLevel}

        self.cavities = [self.Cavity(self, i) for i in xrange(1, 9)]

    class Cavity:
        def __init__(self, _parent, _cavNumber, _q0MeasFileName=""):
            self.parent = _parent
            self.number = _cavNumber
            self.q0MeasFileName = _q0MeasFileName

            self.heaterPV = ("CHTR:CM0" + str(_parent.jlabNum) + ":1"
                             + str(_cavNumber) + "55:HV:POWER")

            self.q0MeasTime = []
            # self.q0MeasUnixTime = []
            self.q0MeasValvePos = []
            self.q0MeasHeatLoad = []
            self.q0MeasDownstreamLevel = []
            self.q0MeasUpstreamLevel = []

            self.pvBufferMap = {self.parent.valvePV: self.q0MeasValvePos,
                                self.parent.dsLevelPV:
                                    self.q0MeasDownstreamLevel,
                                self.parent.usLevelPV: self.q0MeasUpstreamLevel,
                                self.heaterPV: self.q0MeasHeatLoad}
        

def main():
    derp = Cryomodule(12, 2)
    for cav in derp.cavities:
        print cav.heaterPV


if __name__ == '__main__':
    main()
