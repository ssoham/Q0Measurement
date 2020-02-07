from datetime import datetime, timedelta
from container import Cryomodule
from utils import getAndParseRawData
from numpy import mean

def getTotalHeatActFromArchive():
    
    year = 2019
    month = 7
    day = 8
    hour = 1
    minute = 0
    startTime = datetime(year, month, day, hour, minute)
    
    numPoints = 120
    
    module = Cryomodule(16, 2)
    
    r = getAndParseRawData(startTime, numPoints, module.heaterActPVs,
                           verbose=False)
    
    header = r.next()
    
    heatActSums = []
    
    for row in r:
        heatActSum = 0
        for i in range(8):
            heatActSum += float(row.pop())
        heatActSums.append(heatActSum)
    
    heatActMean = mean(heatActSums)
    print("refHeatLoadAct = {NUM}".format(NUM=heatActMean))
    
    
def main():
    getTotalHeatActFromArchive()


if __name__ == '__main__':
    main()
