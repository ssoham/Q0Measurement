class Cryomodule:

    class Cavity:

        def __init__(self, _parent, cavNumber):
            self.parent = _parent
            self.number = cavNumber
            
            self.heaterPV = ("CHTR:CM0" + str(_parent.jlabNum) + ":1"
                             + str(cavNumber) + "55:HV:POWER")
            
            self.q0MeasTime = []
            self.q0MeasUnixTime = []
            self.q0MeasValvePos = []
            self.q0MeasHeatLoad = []
            self.q0MeasDownstreamLevel = []
            self.q0MeasUpstreamLevel = []
    
    def __init__(self, slacNumber, jlabNumber):
        self.slacNum = slacNumber
        self.jlabNum = jlabNumber
        self.cavities = [self.Cavity(self, i) for i in xrange(1, 9)]

        jlabNumStr = str(self.jlabNum)
        self.valvePV = "CPV:CM0" + jlabNumStr + ":3001:JT:POS_RBV"
        self.massFlowPV = "CFICM0312"
        self.dsLevelPV = "CLL:CM0" + jlabNumStr + ":2301:DS:LVL"
        self.usLevelPV = "CLL:CM0" + jlabNumStr + ":2601:US:LVL"

        self.calTime = []
        self.calUnixTime = []
        self.calValvePos = []
        self.calFlowRate = []
        self.calHeatLoad = []
        self.calDownstreamLevel = []
        self.calUpstreamLevel = []
        

def main():
    derp = Cryomodule(12, 2)
    for cav in derp.cavities:
        print cav.heaterPV


if __name__ == '__main__':
    main()
