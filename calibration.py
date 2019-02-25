from csv import reader
from datetime import datetime
from matplotlib import pyplot as plt
from numpy import mean, std, polyfit
from scipy.stats import ttest_ind
from sys import maxint

CRYOMODULE = "2"
VALVE_LOCKED_POS = 17

time = []
unixTime = []
valvePos = []
flowRate = []
heaterPower = []
downstreamLevel = []
upstreamLevel = []

def checkAndAppend(dataBuffers, startIdx, endIdx):
    for (runBuffer, dataBuffer) in dataBuffers:
        if endIdx - startIdx > 0:
            runBuffer.append(dataBuffer[startIdx : endIdx])

def genString(prefix, suffix):
    return prefix + CRYOMODULE + suffix

def parseData(fileName):
    with open(fileName) as csvfile:
        csvReader = reader(csvfile)
        header = csvReader.next()
        
        for row in csvReader:
        
            def appendToBuffer(buff, columnName):
                buff.append(float(row[header.index(columnName)]))
                
            time.append(datetime.strptime(row[header.index("time")],
                                          "%Y-%m-%d %H:%M:%S"))
                                          
            for buff, col in [(unixTime, "Unix time"),
                              (valvePos, genString("CPV:CM0", ":3001:JT:POS_RBV")),
                              #(flowRate, "CFICM0312"),
                              (heaterPower, genString("CHTR:CM0", ":1155:HV:POWER")),
                              (downstreamLevel, genString("CLL:CM0", ":2301:DS:LVL")),
                              (upstreamLevel, genString("CLL:CM0", ":2601:US:LVL"))]:
                              
                appendToBuffer(buff, col)

def getLiquidLevelChange():
    parseData("LL_test_cropped.csv")
    
    runs, timeRuns, heaterVals = populateRuns(heaterPower, downstreamLevel, 66,
                                 1.2)
                                 
    print heaterVals
    
    ax1 = genAxis("Liquid Level as a Function of Time", "Unix Time (s)",
              "Downstream Liquid Level (%)")
              
    ax2 = genAxis("Rate of Change of Liquid Level as a Function of Heater Power",
                  "Heater Power (W)", "dLL/dt (%/s)")
    
    # Needed as a consequence of using scatterplot - should probably not hard
    # code these
    ax2.set_ylim([-0.002, 0])

    slopes = []

    for idx, run in enumerate(runs):
        m, b = polyfit(timeRuns[idx], run, 1)
        slopes.append(m)
        ax1.plot(timeRuns[idx], run, label=(str(round(m, 6)) + "%/s @ "
                                            + str(heaterVals[idx]) + " W"))
        ax1.plot(timeRuns[idx], [m*x + b for x in timeRuns[idx]])
        
    ax2.scatter(heaterVals, slopes, marker="o")
    m, b = polyfit(heaterVals, slopes, 1)
    ax2.plot(heaterVals, [m*x + b for x in heaterVals], label=(str(m)+" %/(s*W)"))

    ax1.legend(loc='lower right')
    ax2.legend(loc='upper right')
    
    plt.show()
    
def getAverage():
    parseData("data_new.csv")
    
    runs, timeRuns, heaterVals = populateRuns(heaterPower, flowRate, 0, maxint)
    print heaterVals
    
    for idx, run in enumerate(runs):
        print str(run[0:10]) + " TRUNCATED FROM " + str(len(run))
        ave = mean(run)
        print ave
        print std(run)
        plt.plot(timeRuns[idx], run)
        plt.plot(timeRuns[idx], [ave for _ in timeRuns[idx]])
        
    plt.show()

def populateRuns(inputBuffer, outputBuffer, levelLimit, valvePosLimit):
    currVal = inputBuffer[0]
    runs = []
    timeRuns = []
    currIdx = 0
    inputVals = []
    
    for idx, val in enumerate(inputBuffer):
            
        # A "break" condition defining the end of a run
        if (val != currVal or upstreamLevel[idx] < levelLimit
            or abs(valvePos[idx] - VALVE_LOCKED_POS) > valvePosLimit):

            # Discarding the first CUTOFF points where the value is settling
            if idx - currIdx > 1000:
                cutoff = int(abs(val - currVal) * 55)
                inputVals.append(currVal)
                print "cutoff: " + str(cutoff)
                checkAndAppend([(runs, outputBuffer), (timeRuns, unixTime)],
                               currIdx + cutoff, idx)
            
            currIdx = idx
            
        currVal = val
    
    if len(inputBuffer) - currIdx > 1000:
        inputVals.append(inputBuffer[len(inputBuffer) - 1])
        checkAndAppend([(runs, outputBuffer), (timeRuns, unixTime)], currIdx,
                       len(inputBuffer))
               
    return runs, timeRuns, inputVals

def genAxis(title, xlabel, ylabel):
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    return ax

getLiquidLevelChange()
#getAverage()
