from __future__ import print_function

# PyEpics doesn't work at LERF yet...
from subprocess import check_output
from matplotlib import pyplot as plt
from time import sleep


def cagetPV(pv, startIdx=1):
    # type: (str, int) -> Optional[List[str]]
    return check_output(["caget", pv, "-n"]).split()[startIdx:]


def plotWaveforms(cryModNum, cavNum, iterations=100):
    prefixStr = "ACCL:L1B:0{CM}{CAV}0:{SUFFIX}"
    waveformFormatStr = prefixStr.format(CM=cryModNum, CAV=cavNum,
                                         SUFFIX="{INFIX}:PWF")

    def getWaveformPV(infix):
        return waveformFormatStr.format(INFIX=infix)

    def trimWaveform(waveform):
        last = waveform.pop()
        while last == "0":
            last = waveform.pop()

    cavWaveformPV = getWaveformPV("CAV")
    forwardWaveformPV = getWaveformPV("FWD")
    reverseWaveformPV = getWaveformPV("REV")

    reverseWaveform = cagetPV(reverseWaveformPV, startIdx=2)
    cavWaveform = cagetPV(cavWaveformPV, startIdx=2)
    forwardWaveform = cagetPV(forwardWaveformPV, startIdx=2)

    trimWaveform(reverseWaveform)
    trimWaveform(cavWaveform)
    trimWaveform(forwardWaveform)

    plt.ion()
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.set_title("Waveforms")
    ax.set_xlabel("Amplitude")
    ax.set_ylabel("")

    ax.set_autoscale_on(True)
    ax.autoscale_view(True, True, True)

    # print(reverseWaveform)

    lineRev, = ax.plot(range(len(reverseWaveform)), reverseWaveform,
                       label="Reverse")
    lineCav, = ax.plot(range(len(cavWaveform)), cavWaveform, label="Cav")
    lineFwd, = ax.plot(range(len(forwardWaveform)), forwardWaveform,
                       label="Forward")

    fig.canvas.draw()
    fig.canvas.flush_events()

    for i in range(iterations):
        reverseWaveform = cagetPV(reverseWaveformPV, startIdx=2)
        cavWaveform = cagetPV(cavWaveformPV, startIdx=2)
        forwardWaveform = cagetPV(forwardWaveformPV, startIdx=2)

        trimWaveform(reverseWaveform)
        trimWaveform(cavWaveform)
        trimWaveform(forwardWaveform)

        lineRev.set_data(range(len(reverseWaveform)), reverseWaveform)
        lineCav.set_data(range(len(cavWaveform)), cavWaveform)
        lineFwd.set_data(range(len(forwardWaveform)), forwardWaveform)

        ax.set_autoscale_on(True)
        ax.autoscale_view(True, True, True)
        # lineCav.set_data(range(len(cavWaveform)), cavWaveform)
        # lineFwd.set_data(range(len(forwardWaveform)), forwardWaveform)

        fig.canvas.draw()
        fig.canvas.flush_events()
        # sleep(1)


if __name__ == "__main__":
    # plt.ion()
    plotWaveforms("2", "1")
    # plt.draw()
    # plt.show()