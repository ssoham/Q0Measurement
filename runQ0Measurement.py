################################################################################
# A script that runs a Q0 measurement for a given Cavity object. This consists
# of some initial RF setup followed by a 40 minute RF run and a 40 minute
# heater calibration run. The script takes 2-4 hours to run depending on the
# initial conditions.
# Author: Lisa Zacarias
################################################################################

from __future__ import print_function, division
from subprocess import CalledProcessError
from time import sleep
from container import Cryomodule, Cavity, Q0DataSession, CalibDataSession
from matplotlib import pyplot as plt
from datetime import datetime
from csv import writer
from utils import (writeAndFlushStdErr, cagetPV, caputPV,
                   MYSAMPLER_TIME_INTERVAL, TEST_MODE, MIN_DS_LL, writeAndWait)


def runQ0Meas(cavity, desiredGradient, calibSession=None, refValvePos=None):
    # type: (Cavity, float, CalibDataSession, float) -> (Q0DataSession, float)
    try:
        if not refValvePos:
            refValvePos = cavity.getRefValvePos(2)

        cavity.waitForCryo(refValvePos)

        checkAcqControl(cavity)
        setPowerStateSSA(cavity, True)
        characterize(cavity)

        # Setting the RF low and ramping up is time consuming so we skip it
        # during testing
        if not TEST_MODE:
            caputPV(cavity.genAcclPV("SEL_ASET"), "15")

        # Start with pulsed mode
        setModeRF(cavity, "4")

        setStateRF(cavity, True)
        pushGoButton(cavity)

        checkAndSetOnTime(cavity)
        checkAndSetDrive(cavity)

        phaseCavity(cavity)

        if not TEST_MODE:
            lowerAmplitude(cavity)

        # go to CW
        setModeRF(cavity, "2")

        walkToGradient(cavity, desiredGradient)

        startTime = holdGradient(cavity, desiredGradient)

        powerDown(cavity)

        endTime = launchHeaterRun(cavity, refValvePos)

        session = cavity.addDataSession(startTime, endTime,
                                        MYSAMPLER_TIME_INTERVAL, refValvePos,
                                        refGradVal=desiredGradient,
                                        calibSession=calibSession)

        with open(cavity.idxFile, 'a') as f:
            csvWriter = writer(f)
            csvWriter.writerow([cavity.cavNum, desiredGradient, refValvePos,
                                startTime.strftime("%m/%d/%y %H:%M"),
                                endTime.strftime("%m/%d/%y %H:%M"),
                                session.refHeatLoad, MYSAMPLER_TIME_INTERVAL])

        return session, refValvePos

    except(CalledProcessError, IndexError, OSError, ValueError,
           AssertionError) as e:
        writeAndFlushStdErr("Procedure failed with error:\n{E}\n".format(E=e))
        powerDown(cavity)


# Checks that the parameters associated with acquisition of the cavity RF
# waveforms are configured properly
def checkAcqControl(cavity):
    # type: (Cavity) -> None
    print("Checking Waveform Data Acquisition Control...")
    for infix in ["CAV", "FWD", "REV", "DRV", "DAC"]:
        enablePV = cavity.genAcclPV(infix + ":ENABLE")
        if float(cagetPV(enablePV)) != 1:
            print("Enabling {INFIX}".format(INFIX=infix))
            caputPV(enablePV, "1")

    suffixValPairs = [("MODE", 1), ("HLDOFF", 0.1), ("STAT_START", 0.065),
                      ("STAT_WIDTH", 0.004), ("DECIM", 255)]

    for suffix, val in suffixValPairs:
        pv = cavity.genAcclPV("ACQ_" + suffix)
        if float(cagetPV(enablePV)) != val:
            print("Setting {SUFFIX}".format(SUFFIX=suffix))
            caputPV(pv, str(val))


def setPowerStateSSA(cavity, turnOn):
    # type: (Cavity, bool) -> None

    # Using double curly braces to trick it into a partial formatting
    ssaFormatPV = cavity.genAcclPV("SSA:{SUFFIX}")

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
            assert cagetPV(ssaStatusPV) == stateMap["desired"], \
                "Could not set SSA Power"
        else:
            print("\nResetting SSA...")
            caputPV(genPV("FaultReset"), "1")
            assert cagetPV(ssaStatusPV) in ["2", "3"], \
                "Could not reset SSA"
            setPowerStateSSA(cavity, turnOn)

    print("SSA power set\n")


################################################################################
# Characterize various cavity parameters.
# * Runs the SSA through its range and constructs a polynomial describing the
#   relationship between requested SSA output and actual output
# * Calibrates the cavity's RF probe so that the gradient readback will be
#   accurate.
################################################################################
def characterize(cavity):
    # type: (Cavity) -> None

    def pushAndWait(suffix):
        caputPV(cavity.genAcclPV(suffix), "1")
        sleep(10)

    def checkAndPush(basePV, pushPV, param, newPV=None):
        oldVal = float(cagetPV(cavity.genAcclPV(basePV)))

        newVal = (float(cagetPV(cavity.genAcclPV(newPV)))
                  if newPV
                  else float(cagetPV(cavity.genAcclPV(basePV + "_NEW"))))

        if abs(newVal - oldVal) < 0.15:
            pushAndWait(pushPV)

        else:
            raise AssertionError("Old and new {PARAM} differ by more than 0.15"
                                 " - please inspect and push manually"
                                 .format(PARAM=param))

    pushAndWait("SSACALSTRT")

    checkAndPush("SLOPE", "PUSH_SSASLOPE.PROC", "slopes")

    pushAndWait("PROBECALSTRT")

    checkAndPush("QLOADED", "PUSH_QLOADED.PROC", "Loaded Qs")

    checkAndPush("CAV:SCALER_SEL.B", "PUSH_CAV_SCALE.PROC", "Cavity Scales",
                 "CAV:CAL_SCALEB_NEW")


# Switches the cavity to a given operational mode (pulsed, CW, etc.)
def setModeRF(cavity, modeDesired):
    # type: (Cavity, str) -> None

    rfModePV = cavity.genAcclPV("RFMODECTRL")

    if cagetPV(rfModePV) is not modeDesired:
        caputPV(rfModePV, modeDesired)
        assert cagetPV(rfModePV) == modeDesired, "Unable to set RF mode"


# Turn the cavity on or off
def setStateRF(cavity, turnOn):
    # type: (Cavity, bool) -> None

    rfStatePV = cavity.genAcclPV("RFSTATE")
    rfControlPV = cavity.genAcclPV("RFCTRL")

    rfState = cagetPV(rfStatePV)

    desiredState = ("1" if turnOn else "0")

    if rfState != desiredState:
        print("\nSetting RF State...")
        caputPV(rfControlPV, desiredState)

    print("RF state set\n")


# Many of the changes made to a cavity don't actually take effect until the
# go button is pressed
def pushGoButton(cavity):
    # type: (Cavity) -> None
    rfStatePV = cavity.genAcclPV("PULSEONSTRT")
    caputPV(rfStatePV, "1")
    sleep(2)
    if cagetPV(rfStatePV) != "1":
        raise AssertionError("Unable to set RF state")


# In pulsed mode the cavity has a duty cycle determined by the on time and off
# time. We want the on time to be 70 ms or else the various cavity parameters
# calculated from the waveform (e.g. the RF gradient) won't be accurate.
def checkAndSetOnTime(cavity):
    # type: (Cavity) -> None
    print("Checking RF Pulse On Time...")
    onTimePV = cavity.genAcclPV("PULSE_ONTIME")
    onTime = cagetPV(onTimePV)
    if onTime != "70":
        print("Setting RF Pulse On Time to 70 ms")
        caputPV(onTimePV, "70")


# Ramps the cavity's RF drive (only relevant in pulsed mode) up until the RF
# gradient is high enough for phasing
def checkAndSetDrive(cavity):
    # type: (Cavity) -> None

    print("Checking drive...")

    drivePV = cavity.genAcclPV("SEL_ASET")
    currDrive = float(cagetPV(drivePV))

    while (float(cagetPV(cavity.gradPV)) < 1) or (currDrive < 15):
        
        print("Increasing drive...")
        driveDes = str(currDrive + 1)

        caputPV(drivePV, driveDes)
        pushGoButton(cavity)

        currDrive = float(cagetPV(drivePV))

    print("Drive set")


# Corrects the cavity phasing in pulsed mode based on analysis of the RF
# waveform. Doesn't currently work if the phase is very far off and the
# waveform is distorted.
def phaseCavity(cavity):
    # type: (Cavity) -> None

    waveformFormatStr = cavity.genAcclPV("{INFIX}:AWF")

    def getWaveformPV(infix):
        return waveformFormatStr.format(INFIX=infix)

    # Get rid of trailing zeros - might be more elegant way of doing this
    def trimWaveform(inputWaveform):
        try:
            maxValIdx = inputWaveform.index(max(inputWaveform))
            del inputWaveform[maxValIdx:]

            first = inputWaveform.pop(0)
            while inputWaveform[0] >= first:
                first = inputWaveform.pop(0)
        except IndexError:
            pass

    def getAndTrimWaveforms():
        res = []

        for suffix in ["REV", "FWD", "CAV"]:
            res.append(cagetPV(getWaveformPV(suffix), startIdx=2))
            res[-1] = list(map(lambda x: float(x), res[-1]))
            trimWaveform(res[-1])

        return res

    def getLine(inputWaveform, lbl):
        return ax.plot(range(len(inputWaveform)), inputWaveform, label=lbl)

    # The waveforms have trailing and leading tails down to zero that would mess
    # with our analysis - have to trim those off.
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

    phasePV = cavity.genAcclPV("SEL_POFF")

    # When the cavity is properly phased the reverse waveform should dip down
    # very close to zero. Phasing the cavity consists of minimizing that minimum
    # value as we vary the RF phase.
    minVal = min(revWaveform)

    print("Minimum reverse waveform value: {MIN}".format(MIN=minVal))
    step = 1

    while abs(minVal) > 0.5:
        val = float(cagetPV(phasePV))

        print("step: {STEP}".format(STEP=step))
        newVal = val + step
        caputPV(phasePV, str(newVal))

        if float(cagetPV(phasePV)) != newVal:
            writeAndFlushStdErr("Mismatch between desired and actual phase")

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
        if (prevMin <= 0 < minVal) or (prevMin >= 0 > minVal):
            step *= -0.5

        elif abs(minVal) > abs(prevMin) + 0.01:
            step *= -1


# Lowers the requested CW amplitude to a safe level where cavities have a very
# low chance of quenching at turnon
def lowerAmplitude(cavity):
    # type: (Cavity) -> None
    print("Lowering amplitude")
    caputPV(cavity.genAcclPV("ADES"), "2")


# Walks the cavity to a given gradient in CW mode with exponential backoff
# in the step size (steps get smaller each time you cross over the desired
# gradient until the error is very low)
def walkToGradient(cavity, desiredGradient):
    # type: (Cavity, float) -> None
    amplitudePV = cavity.genAcclPV("ADES")
    step = 0.5
    gradient = float(cagetPV(cavity.gradPV))
    prevGradient = gradient
    diff = gradient - desiredGradient
    prevDiff = diff
    print("Walking gradient...")

    while abs(diff) > 0.05:
        gradient = float(cagetPV(cavity.gradPV))

        if gradient < (prevGradient * 0.9):
            raise AssertionError("Detected a quench - aborting")

        currAmp = float(cagetPV(amplitudePV))
        diff = gradient - desiredGradient
        mult = 1 if (diff <= 0) else -1

        if (prevDiff >= 0 > diff) or (prevDiff <= 0 < diff):
            step *= (0.5 if step > 0.01 else 1.5)

        caputPV(amplitudePV, str(currAmp + mult * step))

        prevDiff = diff
        sleep(2.5)

    print("Gradient at desired value")


# When cavities are turned on in CW mode they slowly heat up, which causes the
# gradient to drop over time. This function holds the gradient at the requested
# value during the Q0 run.
def holdGradient(cavity, desiredGradient):
    # type: (Cavity, float) -> datetime

    amplitudePV = cavity.genAcclPV("ADES")

    startTime = datetime.now()

    step = 0.01
    prevDiff = float(cagetPV(cavity.gradPV)) - desiredGradient

    print("\nStart time: {START}".format(START=startTime))

    writeAndWait("\nWaiting either 40 minutes or for the LL to drop below"
                 " {NUM}...".format(NUM=MIN_DS_LL))

    hitTarget = False

    while ((datetime.now() - startTime).total_seconds() < 2400
            and float(cagetPV(cavity.dsLevelPV)) > MIN_DS_LL):

        gradient = float(cagetPV(cavity.gradPV))

        # If the gradient suddenly drops by a noticeable amount, that probably
        # indicates a quench in progress and we should abort
        if hitTarget and gradient <= (desiredGradient * 0.9):
            raise AssertionError("Detected a quench - aborting")

        currAmp = float(cagetPV(amplitudePV))

        diff = gradient - desiredGradient

        if abs(diff) <= 0.01:
            hitTarget = True

        mult = 1 if (diff <= 0) else -1

        if (prevDiff >= 0 > diff) or (prevDiff <= 0 < diff):
            step *= (0.5 if step > 0.01 else 1.5)

        caputPV(amplitudePV, str(currAmp + mult * step))

        prevDiff = diff

        writeAndWait(".", 15)

    print("\nEnd Time: {END}".format(END=datetime.now()))
    duration = (datetime.now() - startTime).total_seconds() / 3600
    print("Duration in hours: {DUR}".format(DUR=duration))
    return startTime


def powerDown(cavity):
    # type: (Cavity) -> None
    try:
        print("\nPowering down...")
        setStateRF(cavity, False)
        setPowerStateSSA(cavity, False)
        caputPV(cavity.genAcclPV("SEL_ASET"), "15")
        lowerAmplitude(cavity)

    except(CalledProcessError, IndexError, OSError,
           ValueError, AssertionError) as e:
        writeAndFlushStdErr("Powering down failed with error:\n{E}\n"
                            .format(E=e))


# After doing a data run with the cavity's RF on we also do a run with the
# electric heaters turned up by a known amount. This is used to reduce the error
# in our calculated RF heat load due to the JT valve not being at exactly the
# correct position to keep the liquid level steady over time, which would show
# up as an extra term in the heat load.
def launchHeaterRun(cavity, desPos):
    # type: (Cavity, float) -> datetime

    print("**** REMINDER: refills aren't automated - please contact the"
          " cryo group ****")
    cavity.waitForCryo(desPos)

    cavity.walkHeaters(3)

    startTime = datetime.now()

    print("\nStart time: {START}".format(START=startTime))

    writeAndWait("\nWaiting either 40 minutes or for the LL to drop below"
                 " {NUM}...".format(NUM=MIN_DS_LL))

    while ((datetime.now() - startTime).total_seconds() < 2400
           and float(cagetPV(cavity.dsLevelPV)) > MIN_DS_LL):
        writeAndWait(".", 15)

    endTime = datetime.now()

    print("\nEnd Time: {END}".format(END=endTime))
    duration = (endTime - startTime).total_seconds() / 3600
    print("Duration in hours: {DUR}".format(DUR=duration))

    cavity.walkHeaters(-3)

    return endTime


if __name__ == "__main__":
    # noinspection PyUnboundLocalVariable
    runQ0Meas(Cryomodule(int(input("SLAC CM: ")), int(input("JLAB CM: ")))
              .cavities[int(input("Cavity: "))],
              float(input("Desired Gradient: ")))
