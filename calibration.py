from csv import reader
from datetime import datetime
from matplotlib import pyplot as plt
from numpy import mean, std
from scipy.stats import ttest_ind

CUTOFF = 500

time = []
unixTime = []
valvePos = []
flowRate = []
heaterPower = []

def checkAndAppend(dataBuffers, startIdx, endIdx):
    for (runBuffer, dataBuffer) in dataBuffers:
        if endIdx - startIdx > 0:
            runBuffer.append(dataBuffer[startIdx : endIdx])

with open("data.csv") as csvfile:
    csvReader = reader(csvfile)
    header = csvReader.next()
    for row in csvReader:
        time.append(datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
        unixTime.append(float(row[1]))
        valvePos.append(float(row[2]))
        flowRate.append(float(row[3]))
        heaterPower.append(float(row[4]))

currVal = heaterPower[0]
runs = []
timeRuns = []
currIdx = 0

for idx, val in enumerate(heaterPower):
    if val != currVal:
        # Discarding the first CUTOFF points where the value is settling
        startIdx = currIdx + CUTOFF
        checkAndAppend([(runs, flowRate), (timeRuns, unixTime)], startIdx, idx)
        currIdx = idx
    currVal = val

startIdx = currIdx + CUTOFF
checkAndAppend([(runs, flowRate), (timeRuns, unixTime)], startIdx,
               len(heaterPower) - 1)

for idx, run in enumerate(runs):
    print str(run[0:10]) + " TRUNCATED FROM " + str(len(run))
    ave = mean(run)
    print std(run)
    plt.plot(timeRuns[idx], run)
    plt.plot(timeRuns[idx], [ave for _ in timeRuns[idx]])

print ttest_ind(runs[-1][0:len(runs[-1])/2], runs[-1][len(runs[-1])/2:-1])

print ttest_ind(runs[0], runs[-1])

plt.show()
