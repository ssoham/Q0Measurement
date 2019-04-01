from __future__ import print_function
from subprocess import check_output, CalledProcessError, check_call
from time import sleep
from cryomodule import Cryomodule
from sys import stderr, stdout
from matplotlib import pyplot as plt
from datetime import datetime
from os import devnull


FNULL = open(devnull, "w")


def runQ0Meas(cavity, desiredGradient):
    # type: (Cryomodule.Cavity) -> None
    try:
        # TODO coordinate with Cryo
        setPowerSSA(cavity, True)

        # Start with pulsed mode
        setModeRF(cavity, "4")

        setStateRF(cavity, True)
        pushGoButton(cavity)

        checkDrive(cavity)

        phaseCavity(cavity)

        # go to CW
        setModeRF(cavity, "2")

        holdGradient(cavity, desiredGradient)

        powerDown(cavity)

    except(CalledProcessError, IndexError, OSError, ValueError,
           AssertionError) as e:
        stderr.write("\nProcedure failed with error:\n{E}\n\n".format(E=e))
        sleep(0.01)
        powerDown(cavity)


def setPowerSSA(cavity, turnOn):
    # type: (Cryomodule.Cavity, bool) -> None

    # Using double curly braces to trick it into a partial formatting
    ssaFormatPV = cavity.genPV("SSA:{SUFFIX}")

    def genPV(suffix):
        return ssaFormatPV.format(SUFFIX=suffix)

    ssaStatusPV = genPV("StatusMsg")

    value = cagetPV(ssaStatusPV)

    if turnOn:
        stateMap = {"desired": "3", "opposite": "2", "pv": "PowerOn"}
    else:
        stateMap = {"desired": "2", "opposite": "3", "pv": "PowerOff"}

    if value != stateMap["desired"]:
        if value == stateMap["opposite"]:
            print("\nSetting SSA power...")
            caputPV(genPV(stateMap["pv"]), "1")

            # can't use parentheses with asserts, apparently
            assert cagetPV(ssaStatusPV) ==\
                   stateMap["desired"], "Could not set SSA Power"
        else:
            print("\nResetting SSA...")
            caputPV(genPV("FaultReset"), "1")
            assert cagetPV(ssaStatusPV) in ["2", "3"],\
                "Could not reset SSA"
            setPowerSSA(cavity, turnOn)

    print("SSA power set\n")


# PyEpics doesn't work at LERF yet...
def cagetPV(pv, startIdx=1, attempt=1):
    # type: (str, int) -> Optional[List[str]]
    
    if attempt < 4:
        try:
            out = check_output(["caget", pv, "-n"]).split()[startIdx:]
            if startIdx == 1:
                return out.pop()
            elif startIdx >= 2:
                return out
        except CalledProcessError, AttributeError:
            sleep(2)
            print("Retrying caget")
            return cagetPV(pv, startIdx, attempt + 1)

    else:
        raise CalledProcessError("caget failed too many times")
        


def caputPV(pv, val):
    # type: (str, str) -> Optional[int]

    out = check_call(["caput", pv, val], stdout=FNULL)
    sleep(2)
    return out


def setModeRF(cavity, modeDesired):
    # type: (Cryomodule.Cavity, str) -> None

    rfModePV = cavity.genPV("RFMODECTRL")

    if cagetPV(rfModePV) is not modeDesired:
        caputPV(rfModePV, modeDesired)
        assert cagetPV(rfModePV) == modeDesired, "Unable to set RF mode"


def setStateRF(cavity, turnOn):
    # type: (Cryomodule.Cavity, bool) -> None

    rfStatePV = cavity.genPV("RFSTATE")
    rfControlPV = cavity.genPV("RFCTRL")

    rfState = cagetPV(rfStatePV)

    desiredState = ("1" if turnOn else "0")

    if rfState != desiredState:
        print("\nSetting RF State...")
        caputPV(rfControlPV, desiredState)
        #assert cagetPV(rfStatePV) == desiredState,\
            #"Unable to set RF state"

    print("RF state set\n")


def pushGoButton(cavity):
    # type: (Cryomodule.Cavity) -> None
    rfStatePV = cavity.genPV("PULSEONSTRT")
    caputPV(rfStatePV, "1")
    sleep(2)
    assert cagetPV(rfStatePV) == "1",\
            "Unable to set RF state"


def checkDrive(cavity):
    # type: (Cryomodule.Cavity) -> None
    
    print("Checking drive...")

    drivePV = cavity.genPV("SEL_ASET")
    currDrive = float(cagetPV(drivePV))

    while (float(cagetPV(cavity.gradientPV)) < 1) or (currDrive < 15):
        
        print("Increasing drive...")
        driveDes = str(currDrive + 1)

        caputPV(drivePV, driveDes)
        currDrive = float(cagetPV(drivePV))
        
    print("Drive set")


def phaseCavity(cavity):
    # type: (Cryomodule.Cavity) -> None

    waveformFormatStr = cavity.genPV("{INFIX}:AWF")

    def getWaveformPV(infix):
        return waveformFormatStr.format(INFIX=infix)

    # get rid of trailing zeros - might be more elegant way of doing this
    def trimWaveform(waveform):
        try:
            maxValIdx = waveform.index(max(waveform))
            del waveform[maxValIdx:]

            first = waveform.pop(0)
            while waveform[0] >= first:
                first = waveform.pop(0)
        except IndexError:
            pass

    def getAndTrimWaveforms():
        res = []

        for suffix in ["REV", "FWD", "CAV"]:
            res.append(cagetPV(getWaveformPV(suffix), startIdx=2))
            res[-1] = list(map(lambda x: float(x), res[-1]))
            trimWaveform(res[-1])
            
        return res

    def getLine(waveform, lbl):
        return ax.plot(range(len(waveform)), waveform, label=lbl)

    revWaveform, fwdWaveform, cavWaveform = getAndTrimWaveforms()

    plt.ion()
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.set_title("Waveforms")
    ax.set_xlabel("Seconds")
    ax.set_ylabel("Amplitude")

    ax.set_autoscale_on(True)
    ax.autoscale_view(True, True, True)

    lineRev, = getLine(revWaveform, "Reverse")
    lineCav, = getLine(cavWaveform, "Cavity")
    lineFwd, = getLine(fwdWaveform, "Forward")

    fig.canvas.draw()
    fig.canvas.flush_events()

    phasePV = cavity.genPV("SEL_POFF")

    minVal = min(revWaveform)

    print("Minimum reverse waveform value: {MIN}".format(MIN=minVal))
    step = 1
   
    while abs(minVal) > 0.5:
        val = float(cagetPV(phasePV))

        print("step: {STEP}".format(STEP=step))
        newVal = val + step
        caputPV(phasePV, str(newVal))

        if float(cagetPV(phasePV)) != newVal:
            stderr.write("\nMismatch between desired and actual phase\n")
            sleep(0.01)

        revWaveform, fwdWaveform, cavWaveform = getAndTrimWaveforms()
        
        lineWaveformPairs = [(lineRev, revWaveform), (lineCav, cavWaveform),
                             (lineFwd, fwdWaveform)]

        for line, waveform in lineWaveformPairs:
            line.set_data(range(len(waveform)), waveform)

        ax.set_autoscale_on(True)
        ax.autoscale_view(True, True, True)

        fig.canvas.draw()
        fig.canvas.flush_events()

        prevMin = minVal
        minVal = min(revWaveform)
        print("Minimum reverse waveform value: {MIN}".format(MIN=minVal))

        # I think this accounts for inflection points? Hopefully the decrease
        # in step size addresses the potential for it to get stuck
        if (prevMin <= 0 and minVal > 0) or (prevMin >= 0 and minVal < 0):
            step *= -0.5

        elif abs(minVal) > abs(prevMin) + 0.01:
            step *= -1


def holdGradient(cavity, desiredGradient):
    # type: (Cryomodule.Cavity, float) -> None

    amplitudePV = cavity.genPV("ADES")

    startTime = datetime.now()

    step = 0.5
    prevDiff = float(cagetPV(cavity.gradientPV)) - desiredGradient
    
    print("\nStart time: {START}".format(START=startTime))

    stdout.write("\nHolding gradient for 40 minutes...")
    stdout.flush()
    
    hitTarget = False
    
    while (datetime.now() - startTime).total_seconds() < 2400:
        stdout.write(".")

        gradient = float(cagetPV(cavity.gradientPV))
        
        if hitTarget and gradient <= (desiredGradient / 4):
            raise AssertionError("Detected a quench - aborting")
        
        currAmp = float(cagetPV(amplitudePV))

        diff = gradient - desiredGradient
        
        if abs(diff) <= 0.01:
            hitTarget = True

        mult = 1 if (diff <= 0) else -1

        if (prevDiff >= 0 and diff < 0) or (prevDiff <= 0 and diff > 0):
            step *= (0.5 if step > 0.01 else 1.5)

        caputPV(amplitudePV, str(currAmp + mult*step))

        prevDiff = diff

        sleep(5)
        stdout.flush()
        
    print("\nEnd Time: {END}".format(END=datetime.now()))


def powerDown(cavity):
    try:
        print("\nPowering down...")
        setStateRF(cavity, False)
        setPowerSSA(cavity, False)

    except(CalledProcessError, IndexError, OSError,
           ValueError, AssertionError) as e:
        stderr.write("\nPowering down failed with error:\n{E}\n\n".format(E=e))
        sleep(0.01)


if __name__ == "__main__":
    runQ0Meas(Cryomodule(int(raw_input("SLAC CM: ")),
                         int(raw_input("JLAB CM: ")),
                         None, 0, 0).cavities[int(raw_input("Cavity: "))],
              float(raw_input("Desired Gradient: ")))
     
