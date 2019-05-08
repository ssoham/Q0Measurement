################################################################################
# A script that runs a Q0 measurement for a given Cavity object. This consists
# of some initial RF setup followed by a 40 minute RF run and a 40 minute
# heater calibration run. The script takes 2-4 hours to run depending on the
# initial conditions.
# Author: Lisa Zacarias
################################################################################

from __future__ import print_function
from __future__ import division
from subprocess import CalledProcessError
from time import sleep
from numpy import mean
from cryomodule import (MYSAMPLER_TIME_INTERVAL, TEST_MODE, Cryomodule, Cavity,
                        Container, DataSession, Q0DataSession, MAX_DS_LL)
from sys import stderr, stdout
from matplotlib import pyplot as plt
from datetime import datetime, timedelta
from epicsShell import cagetPV, caputPV
from scipy.stats import linregress
from math import log10
from csv import writer
# from calculateQ0 import getStrLim, get_float_lim


ERROR_MESSAGE = "Please provide valid input"


if hasattr(__builtins__, 'raw_input'):
    input = raw_input


def get_float_lim(prompt, low_lim, high_lim):
    return getNumericalInput(prompt, low_lim, high_lim, float)


def getNumericalInput(prompt, lowLim, highLim, inputType):
    response = get_input(prompt, inputType)

    while response < lowLim or response > highLim:
        stderr.write(ERROR_MESSAGE + "\n")
        sleep(0.01)
        response = get_input(prompt, inputType)

    return response


def get_input(prompt, desired_type):
    response = input(prompt)

    try:
        response = desired_type(response)
    except ValueError:
        stderr.write(str(desired_type) + " required\n")
        sleep(0.01)
        return get_input(prompt, desired_type)

    return response


def getStrLim(prompt, acceptable_strings):
    response = get_input(prompt, str)

    while response not in acceptable_strings:
        stderr.write(ERROR_MESSAGE + "\n")
        sleep(0.01)
        response = get_input(prompt, str)

    return response



def runQ0Meas(cavity, desiredGradient, calibSession=None, refValvePos=None):
    # type: (Cavity, float, DataSession, float) -> (Q0DataSession, float)
    try:
        # cavity.refGradVal = desiredGradient
        if not refValvePos:
            refValvePos = checkCryo(cavity, 2)

        checkAcqControl(cavity)
        setPowerSSA(cavity, True)
        characterize(cavity)

        if not TEST_MODE:
            caputPV(cavity.genAcclPV("SEL_ASET"), "15")

        # Start with pulsed mode
        setModeRF(cavity, "4")

        setStateRF(cavity, True)
        pushGoButton(cavity)

        checkAndSetOnTime(cavity)
        checkDrive(cavity)

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
        # powerDown(cavity)


# noinspection PyTupleAssignmentBalance,PyTypeChecker
def checkCryo(container, numHours, checkForFlatness=True):
    # type: (Container, float, bool) -> float

    getNewPos = (getStrLim("Determine new JT Valve Position?"
                           " (May take 2 hours)", ["y", "n", "Y", "N"])
                 in ["y", "Y"])

    if not getNewPos:
        desPos = get_float_lim("Desired JT Valve Position: ", 0, 100)
        waitForLL(container)
        waitForJT(container, desPos)
        print("\nDesired JT Valve position is {POS}".format(POS=desPos))
        return desPos

    print("\nDetermining Required JT Valve Position...")
    csvReader = DataSession.parseRawData(datetime.now()
                                         - timedelta(hours=numHours),
                                         int((60 / MYSAMPLER_TIME_INTERVAL)
                                             * (numHours * 60)),
                                         [container.dsLevelPV,
                                          container.valvePV])

    csvReader.next()
    valveVals = []
    llVals = []

    for row in csvReader:
        try:
            valveVals.append(float(row.pop()))
            llVals.append(float(row.pop()))
        except ValueError:
            pass

    m, b, _, _, _ = linregress(range(len(valveVals)), valveVals)

    if not checkForFlatness or (checkForFlatness and log10(abs(m)) < 5):
        desPos = round(mean(valveVals), 1)
        waitForLL(container)
        waitForJT(container, desPos)
        print("\nDesired JT Valve position is {POS}".format(POS=desPos))
        return desPos

    else:
        print("Need to figure out new JT valve position")

        waitForLL(container)

        writeAndWait("\nWaiting 1 hour 45 minutes for LL to stabilize...")

        start = datetime.now()
        while (datetime.now() - start).total_seconds() < 6300:
            writeAndWait(".", 5)
            
        return checkCryo(container, 0.25, False)


def waitForLL(container):
    # type: (Container) -> None
    writeAndWait("\nWaiting for downstream liquid level to be {LL}%..."
                 .format(LL=MAX_DS_LL))

    while abs(MAX_DS_LL - float(cagetPV(container.dsLevelPV))) > 1:
        writeAndWait(".", 5)

    print("\ndownstream liquid level at required value")


def writeAndWait(message, timeToWait=0):
    stdout.write(message)
    stdout.flush()
    sleep(timeToWait)


def waitForJT(container, desPosJT):
    # type: (Container, float) -> None

    writeAndWait("\nWaiting for JT Valve to be locked at {POS}..."
                 .format(POS=desPosJT))

    mode = cagetPV(container.jtModePV)

    if mode == "0":
        while float(cagetPV(container.jtPosSetpointPV)) != desPosJT:
            writeAndWait(".", 5)

    else:

        while float(cagetPV(container.cvMinPV)) != desPosJT:
            writeAndWait(".", 5)

        while float(cagetPV(container.cvMaxPV)) != desPosJT:
            writeAndWait(".", 5)

    print("\nJT Valve locked")


def checkAcqControl(cavity):
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


def setPowerSSA(cavity, turnOn):
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
            setPowerSSA(cavity, turnOn)

    print("SSA power set\n")


def characterize(cavity):

    def checkAndPush(basePV, pushPV, param, newPV=None):
        oldVal = float(cagetPV(cavity.genAcclPV(basePV)))

        newVal = (float(cagetPV(cavity.genAcclPV(newPV)))
                  if newPV
                  else float(cagetPV(cavity.genAcclPV(basePV + "_NEW"))))

        if abs(newVal - oldVal) < 0.15:
            caputPV(cavity.genAcclPV(pushPV), "1")

        else:
            # print("Old and new {PARAM} differ by more than 0.15 - please "
            #       "inspect and push manually".format(PARAM=param))
            raise AssertionError("Old and new {PARAM} differ by more than 0.15"
                                 " - please inspect and push manually"
                                 .format(PARAM=param))

    caputPV(cavity.genAcclPV("SSACALSTRT"), "1")

    checkAndPush("SLOPE", "PUSH_SSASLOPE.PROC", "slopes")

    caputPV(cavity.genAcclPV("PROBECALSTRT"), "1")

    checkAndPush("QLOADED", "PUSH_QLOADED.PROC", "Loaded Qs")

    checkAndPush("CAV:SCALER_SEL.B", "PUSH_CAV_SCALE.PROC", "Cavity Scales",
                 "CAV:CAL_SCALEB_NEW")


def setModeRF(cavity, modeDesired):
    # type: (Cavity, str) -> None

    rfModePV = cavity.genAcclPV("RFMODECTRL")

    if cagetPV(rfModePV) is not modeDesired:
        caputPV(rfModePV, modeDesired)
        assert cagetPV(rfModePV) == modeDesired, "Unable to set RF mode"


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


def pushGoButton(cavity):
    # type: (Cavity) -> None
    rfStatePV = cavity.genAcclPV("PULSEONSTRT")
    caputPV(rfStatePV, "1")
    sleep(2)
    if cagetPV(rfStatePV) != "1":
        raise AssertionError("Unable to set RF state")


def checkAndSetOnTime(cavity):
    print("Checking RF Pulse On Time...")
    onTimePV = cavity.genAcclPV("PULSE_ONTIME")
    onTime = cagetPV(onTimePV)
    if onTime != "70":
        print("Setting RF Pulse On Time to 70ms")
        caputPV(onTimePV, "70")


def checkDrive(cavity):
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


def phaseCavity(cavity):
    # type: (Cavity) -> None

    waveformFormatStr = cavity.genAcclPV("{INFIX}:AWF")

    def getWaveformPV(infix):
        return waveformFormatStr.format(INFIX=infix)

    # get rid of trailing zeros - might be more elegant way of doing this
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


def writeAndFlushStdErr(message):
    stderr.write("\n{MSSG}\n".format(MSSG=message))
    stderr.flush()


def lowerAmplitude(cavity):
    print("Lowering amplitude")
    caputPV(cavity.genAcclPV("ADES"), "2")


def walkToGradient(cavity, desiredGradient):
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


def holdGradient(cavity, desiredGradient):
    # type: (Cavity, float) -> datetime

    amplitudePV = cavity.genAcclPV("ADES")

    startTime = datetime.now()

    step = 0.01
    prevDiff = float(cagetPV(cavity.gradPV)) - desiredGradient

    print("\nStart time: {START}".format(START=startTime))

    stdout.write("\nHolding gradient for 40 minutes...")
    stdout.flush()

    hitTarget = False

    while (datetime.now() - startTime).total_seconds() < 2400:
        stdout.write(".")

        gradient = float(cagetPV(cavity.gradPV))
        
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

        sleep(15)
        stdout.flush()

    print("\nEnd Time: {END}".format(END=datetime.now()))
    duration = (datetime.now() - startTime).total_seconds() / 3600
    print("Duration in hours: {DUR}".format(DUR=duration))
    return startTime


def powerDown(cavity):
    try:
        print("\nPowering down...")
        setStateRF(cavity, False)
        setPowerSSA(cavity, False)
        caputPV(cavity.genAcclPV("SEL_ASET"), "15")
        lowerAmplitude(cavity)

    except(CalledProcessError, IndexError, OSError,
           ValueError, AssertionError) as e:
        writeAndFlushStdErr("Powering down failed with error:\n{E}\n"
                            .format(E=e))


def launchHeaterRun(cavity, desPos):
    # type: (Cavity, float) -> datetime

    proceed = (getStrLim("Proceed to the heater calibration without refilling?",
                         ["y", "n", "Y", "N"]) in ["y", "Y"])

    if not proceed:
        print("**** REMINDER: refills aren't automated - please contact the"
              " cryo group ****")
        waitForLL(cavity)
        waitForJT(cavity, desPos)

    for heaterPV in cavity.parent.heaterDesPVs:
        curVal = float(cagetPV(heaterPV))
        caputPV(heaterPV, str(curVal + 1))

    startTime = datetime.now()

    print("\nStart time: {START}".format(START=startTime))

    stdout.write("\nRunning heaters for 40 minutes...")

    while (datetime.now() - startTime).total_seconds() < 2400:
        stdout.write(".")
        stdout.flush()

        # Abort the run if the downstream liquid helium level dips below 90
        if float(cagetPV(cavity.dsLevelPV)) < 90:
            break
        sleep(5)

    endTime = datetime.now()
    print("\nEnd Time: {END}".format(END=endTime))
    duration = (endTime - startTime).total_seconds() / 3600
    print("Duration in hours: {DUR}".format(DUR=duration))

    for heaterPV in cavity.parent.heaterDesPVs:
        curVal = float(cagetPV(heaterPV))
        caputPV(heaterPV, str(curVal - 1))

    return endTime


if __name__ == "__main__":
    # noinspection PyUnboundLocalVariable
    runQ0Meas(Cryomodule(int(input("SLAC CM: ")), int(input("JLAB CM: ")))
              .cavities[int(input("Cavity: "))],
              float(input("Desired Gradient: ")))
