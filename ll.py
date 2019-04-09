from __future__ import division
from csv import reader
from datetime import datetime
from matplotlib import pyplot as plt
from math import ceil, log10
from scipy.stats import linregress


def genAxis(title, xlabel, ylabel):
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    return ax


with open("ll.csv") as csvFile:
    csvReader = reader(csvFile)
    time = []
    data = []

    timeZero = datetime.utcfromtimestamp(0)
    datetimeFormatStr = "%Y-%m-%d-%H:%M:%S"

    for row in csvReader:
        dt = datetime.strptime(row[0], datetimeFormatStr)
        time.append((dt - timeZero).total_seconds())
        data.append(float(row[1]))

    ax = genAxis("", "", "")
    ax.plot(time, data)

    pointsPerMin = 4

    timeChunk = pointsPerMin * 120
    chunks = int(ceil(len(data) / timeChunk))


    for i in range(chunks):
        start = i * timeChunk
        run = data[start:start + timeChunk]
        timeRun = time[start:start + timeChunk]
        m, b, r, p, e = linregress(timeRun, run)
        ax.plot(timeRun, [m*x + b for x in timeRun],
                label="{M}".format(M=log10(abs(m))))

    ax.legend(loc='best')
    plt.draw()
    plt.show()

