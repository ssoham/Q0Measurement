from __future__ import division
from csv import reader
from datetime import datetime
from matplotlib import pyplot as plt
from math import ceil, log10
from scipy.stats import linregress
from numpy import mean
from scipy.signal import medfilt


def genAxis(title, xlabel, ylabel):
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    return ax


with open("ll.csv") as csvFile:
    csvReader = reader(csvFile)
    jtReader = reader(open("jt.csv"))
    time = []
    data = []
    jtVals = []

    timeZero = datetime.utcfromtimestamp(0)
    datetimeFormatStr = "%Y-%m-%d-%H:%M:%S"

    for row in csvReader:
        dt = datetime.strptime(row[0], datetimeFormatStr)
        time.append((dt - timeZero).total_seconds())
        data.append(float(row[1]))

    for row in jtReader:
        # dt = datetime.strptime(row[0], datetimeFormatStr)
        # time.append((dt - timeZero).total_seconds())
        jtVals.append(float(row[1]))

    data = medfilt(data)
    jtVals = medfilt(jtVals)

    ax = genAxis("LL", "", "")
    ax.plot(time, data)

    ax1 = genAxis("JT", "", "")
    ax1.plot(time, jtVals)

    pointsPerMin = 4

    timeChunk = pointsPerMin * 60
    chunks = int(ceil(len(data) / timeChunk))


    for i in range(chunks):
        start = i * timeChunk
        run = data[start:start + timeChunk]
        timeRun = time[start:start + timeChunk]
        m, b, r, p, e = linregress(timeRun, run)
        ax.plot(timeRun, [m*x + b for x in timeRun],
                label="{M}".format(M=log10(abs(m))))
        ave = mean(jtVals[start : start + timeChunk])
        ax1.plot(timeRun, [ave for t in timeRun], label=str(ave))

    ax.legend(loc='best')
    ax1.legend(loc='best')
    plt.draw()
    plt.show()

