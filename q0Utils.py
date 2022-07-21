from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from errno import EEXIST
from json import dumps
from os import devnull, makedirs, path
from pathlib import Path
from re import compile, findall
from subprocess import CalledProcessError, check_call, check_output
from sys import stderr, stdout, version_info
from time import sleep
from typing import Any, Callable, Dict, KeysView, List, Optional, Tuple, Union

from epics import PV
from lcls_tools.common.data_analysis.archiver import Archiver, ArchiverData
from matplotlib import pyplot as plt
from matplotlib.axes import Axes
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from requests.exceptions import ConnectTimeout
from six import moves

# Set True to use a known data set for debugging and/or demoing
# Set False to prompt the user for real data
TEST_MODE = False

# The relationship between the LHE content of a cryomodule and the readback from
# the liquid level sensors isn't linear over the full range of the sensors. We
# have chosen to gather all our data with the downstream sensor above 90%. When
# refilling the cryomodule we refill to at least 95%.
MIN_DS_LL = 90
MAX_DS_LL = 95

MIN_US_LL = 66

# Used to reject data where the JT valve wasn't at the correct position
VALVE_POS_TOL = 2

# Used to reject data where the cavity heater wasn't at the correct value
HEATER_TOL = 1.2

# The minimum acceptable run length is ten minutes (600 seconds)
MIN_RUN_DURATION = 200

# We want the liquid level to drop by at least 2.5% during our runs. This isn't
# actually enforced however, unlike the run duration.
TARGET_LL_DIFF = 4

# Used to reject data where the cavity amplitude wasn't at the correct value
AMPLITUDE_TOL = 0.3

# We fetch data from the JLab archiver with a program called MySampler, which
# samples the chosen PVs at a user-specified time interval. Increase to improve
# statistics, decrease to lower the size of the CSV files and speed up
# MySampler data acquisition.
ARCHIVER_TIME_INTERVAL = 1

# Used in custom input functions
ERROR_MESSAGE = "Please provide valid input"

# This is used to suppress the output of the caput function.
FNULL = open(devnull, "w")

# The starting point for our calibration
INITIAL_CAL_HEAT_LOAD = 8

# The number of distinct heater settings we're using for cryomodule calibrations
NUM_CAL_STEPS = 7

NUM_LL_POINTS_TO_AVG = 25

CAV_HEATER_RUN_LOAD = 24
FULL_MODULE_CALIBRATION_LOAD = 80

CAL_HEATER_DELTA = 8

JT_SEARCH_TIME_RANGE: timedelta = timedelta(hours=24)
JT_SEARCH_OVERLAP_DELTA: timedelta = timedelta(minutes=30)
DELTA_NEEDED_FOR_FLATNESS: timedelta = timedelta(hours=2)

FULL_CALIBRATION_FILENAME_TEMPLATE = "calibrationsCM{CM}.json"
CAVITY_CALIBRATION_FILENAME_TEMPLATE = "cav{CAV}/calibrationsCM{CM}CAV{CAV}.json"

RUN_STATUS_MSSG = ("\nWaiting for the LL to drop {DIFF}% "
                   "or below {MIN}%...".format(MIN=MIN_DS_LL, DIFF=TARGET_LL_DIFF))

SSA_SLOPE_CHANGE_TOL = 0.15
LOADED_Q_CHANGE_TOL = 0.15e7
CAVITY_SCALE_CHANGE_TOL = 0.2

# Swapnil requests 1%/min
JT_STEP_SIZE_PER_SECOND = 1 / 60

JT_MANUAL_MODE_VALUE = 0
JT_AUTO_MODE_VALUE = 1

RF_MODE_SELAP = 0
RF_MODE_SELA = 1
RF_MODE_SEL = 2
RF_MODE_SEL_RAW = 3
RF_MODE_PULSE = 4
RF_MODE_CHIRP = 5

SAFE_PULSED_DRIVE_LEVEL = 15

ARCHIVER = Archiver("lcls")


class RFError(Exception):
    """
    Exception thrown during RF Execution for the GUI to catch
    """
    
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class QuenchError(RFError):
    def __init__(self):
        super(QuenchError, self).__init__("Quench Detected")


class CryoError(Exception):
    """
    Exception thrown during Cryo Execution for the GUI to catch
    """
    
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


def q0Hash(argList: List[Any]):
    """
    A hash is effectively a unique numerical identifier. The purpose of a
    hash function is to generate an ID for an object. This function
    takes all of the input parameters and XORs (the ^ symbol) them.

    What is an XOR? It's an operator that takes two bit strings and goes
    through them, bit by bit, returning True (1) only if one bit is 0 and the
    other is 1

    EX) consider the following two bit strings a, b, and c = a^b:
          a: 101010010010 (2706 in base 10)
          b: 100010101011 (2219)
          ---------------
          c: 001000111001 (569)

    What we're doing here is taking each input data object's built-in hash
    function (which returns an int) and XORing those ints together. It's not
    QUITE unique, but XOR is the accepted way to hash in Python because
    collisions are extremely rare.

    As to WHY we're doing this, it's to have an easy way to compare
    two data sessions so that we can avoid creating (and storing) duplicate
    data sessions.
    """
    
    if len(argList) == 1:
        return hash(argList.pop())
    
    for arg in argList:
        return hash(arg) ^ q0Hash(argList[1:])


@dataclass
class SSAStateMap:
    desired: int
    opposite: int
    pv: PV


@dataclass
class ValveParams:
    refValvePos: float
    refHeatLoadDes: float
    refHeatLoadAct: float


@dataclass
class TimeParams:
    startTime: datetime
    endTime: datetime
    timeInterval: int


@dataclass
class CryomodulePVs:
    heaterDesPV: str
    heaterActPV: str
    valvePV: str
    dsLevelPV: str
    usLevelPV: str
    dsPressurePV: str
    ampPVs: Optional[List[str]] = None
    
    def asList(self) -> List[str]:
        return ([self.valvePV, self.dsLevelPV, self.usLevelPV,
                 self.dsPressurePV] + self.heaterDesPV + self.heaterActPV +
                (self.ampPVs if self.ampPVs else []))


def isYes(prompt):
    return getStrLim(prompt + " (y/n) ", ["Y", "y", "N", "n"]) in ["y", "Y"]


def getStrLim(prompt, acceptable_strings):
    # type: (str, List[str]) -> str
    
    response = get_input(prompt, str)
    
    while response not in acceptable_strings:
        stderr.write(ERROR_MESSAGE + "\n")
        sleep(0.01)
        response = get_input(prompt, str)
    
    return response


def writeAndFlushStdErr(message):
    # type: (str) -> None
    stderr.write("\n{MSSG}\n".format(MSSG=message))
    stderr.flush()


def writeAndWait(message, timeToWait=0):
    # type: (str, float) -> None
    stdout.write(message)
    stdout.flush()
    sleep(timeToWait)


def get_float_lim(prompt, low_lim, high_lim):
    # type: (str, float, float) -> float
    return getNumericalInput(prompt, low_lim, high_lim, float)


def getNumInputFromLst(prompt, lst, inputType, allowNoResponse=False):
    # type: (str, Union[KeysView, List], Callable, bool) -> Union[float, int]
    response = get_input(prompt, inputType, allowNoResponse)
    while response not in lst:
        # If the user just hits enter, return the first number in the list
        if allowNoResponse and response == "":
            # return lst[0]
            # @pre First option should always be 1
            return 1
        else:
            stderr.write(ERROR_MESSAGE + "\n")
            # Need to pause briefly for some reason to make sure the error message
            # shows up before the next prompt
            sleep(0.01)
            response = get_input(prompt, inputType, allowNoResponse)
    
    return response


def getNumericalInput(prompt, lowLim, highLim, inputType):
    # type: (str, Union[int, float], Union[int, float], Callable) -> Union[int, float]
    response = get_input(prompt, inputType)
    
    while response < lowLim or response > highLim:
        stderr.write(ERROR_MESSAGE + "\n")
        sleep(0.01)
        response = get_input(prompt, inputType)
    
    return response


def get_input(prompt, desired_type, allowNoResponse=False):
    # type: (str, Callable, bool) -> Union[int, float, str]
    
    response = moves.input(prompt)
    
    # if allowNoResponse is True the user is permitted to just hit enter in
    # response to the prompt, giving us an empty string regardless of the
    # desired input type
    if allowNoResponse and response == "":
        return response
    
    try:
        response = desired_type(response)
    except ValueError:
        stderr.write(str(desired_type) + " required\n")
        sleep(0.01)
        return get_input(prompt, desired_type, allowNoResponse)
    
    return response


def get_int_lim(prompt, low_lim, high_lim):
    # type: (str, int, int) -> int
    return getNumericalInput(prompt, low_lim, high_lim, int)


# PyEpics doesn't work at LERF yet...
# noinspection PyArgumentList
def cagetPV(pv, startIdx=1, attempt=1):
    # type: (str, int, int) -> [str]
    
    if attempt < 4:
        try:
            out = check_output(["caget", pv, "-n"]).split()[startIdx:]
            if startIdx == 1:
                return out.pop()
            elif startIdx >= 2:
                return out
        except CalledProcessError as _:
            sleep(2)
            print("Retrying caget")
            return cagetPV(pv, startIdx, attempt + 1)
    
    else:
        raise CalledProcessError("caget failed too many times")


# noinspection PyArgumentList
def caputPV(pv, val, attempt=1):
    # type: (str, str, int) -> int
    
    if attempt < 4:
        try:
            out = check_call(["caput", pv, val], stdout=FNULL)
            sleep(2)
            return out
        except CalledProcessError:
            sleep(2)
            print("Retrying caput")
            return caputPV(pv, val, attempt + 1)
    else:
        raise CalledProcessError("caput failed too many timeStamps")


def makeTimeFromStr(row, idx):
    # type: (List[str], int) -> datetime
    return datetime.strptime(row[idx], "%m/%d/%y %H:%M:%S")


def getTimeParams(row, indices):
    # type: (List[str], Dict[str, int]) -> TimeParams
    startTime = makeTimeFromStr(row, indices["startIdx"])
    endTime = makeTimeFromStr(row, indices["endIdx"])
    
    timeIntervalStr = row[indices["timeIntIdx"]]
    timeInterval = (int(timeIntervalStr) if timeIntervalStr
                    else ARCHIVER_TIME_INTERVAL)
    
    timeParams = TimeParams(startTime, endTime, timeInterval)
    
    return timeParams


############################################################################
# getMySamplerData runs a shell command to get archive data. The syntax we're
# using is:
#
#     mySampler -b "%Y-%m-%d %H:%M:%S" -s 1s -n[numPoints] [pv1] ... [pvn]
#
# where the "-b" denotes the start time, "-s 1s" says that the desired time
# step between data points is 1 second, -n[numPoints] tells us how many
# points we want, and [pv1]...[pvn] are the PVs we want archived
#
# Ex:
#     mySampler -b "2019-03-28 14:16" -s 30s -n11 R121PMES R221PMES
#
# @param startTime: datetime object
# @param signals: list of PV strings
############################################################################
def getArchiverData(numPoints: int, signals: List[str],
                    timeInt: int = ARCHIVER_TIME_INTERVAL,
                    startTime: datetime = None, endTime: datetime = None) -> Optional[ArchiverData]:
    archiver = Archiver("lcls")
    try:
        if startTime:
            endTime = startTime + timedelta(seconds=(numPoints * timeInt))
        
        elif endTime:
            startTime = endTime - timedelta(seconds=(numPoints * timeInt))
        
        else:
            writeAndFlushStdErr("No time boundaries supplied")
            return None
        
        # timeInt is is seconds
        data = archiver.getDataWithTimeInterval(pvList=signals,
                                                startTime=startTime,
                                                endTime=endTime,
                                                timeDelta=timedelta(seconds=timeInt))
        return data
    
    except ConnectTimeout:
        writeAndFlushStdErr("Archiver timed out - are you VPNed in?")
        return None


# def getAndParseRawData(startTime, numPoints, signals,
#                        timeInt=MYSAMPLER_TIME_INTERVAL, verbose=True):
#     # type: (datetime, int, List[str], int, bool) -> Optional[_reader]
#     if verbose:
#         print("\nGetting data from the archive...\n")
#     rawData = getArchiverData(startTime, numPoints, signals, timeInt)
#
#     if not rawData:
#         return None
#
#     else:
#         rawDataSplit = rawData.splitlines()
#         rows = ["\t".join(rawDataSplit.pop(0).strip().split())]
#         rows.extend(list(map(lambda x: reformatDate(x), rawDataSplit)))
#         return reader(rows, delimiter='\t')


def reformatDate(row):
    # type: (str) -> unicode
    try:
        # This clusterfuck regex is pretty much just trying to find strings
        # that match %Y-%m-%d %H:%M:%S and making them %Y-%m-%d-%H:%M:%S
        # instead (otherwise the csv parser interprets it as two different
        # columns)
        regex = compile("[0-9]{4}-[0-9]{2}-[0-9]{2}"
                        + " [0-9]{2}:[0-9]{2}:[0-9]{2}")
        res = findall(regex, row)[0].replace(" ", "-")
        reformattedRow = regex.sub(res, row)
        return "\t".join(reformattedRow.strip().split())
    
    except IndexError:
        
        writeAndFlushStdErr("Could not reformat date for row: " + str(row)
                            + "\n")
        return "\t".join(row.strip().split())


def genAxis(title, xlabel, ylabel):
    # type: (str, str, str) -> Axes
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    return ax


def redrawAxis(canvas, title, xlabel, ylabel):
    # type: (FigureCanvasQTAgg, str, str, str) -> None
    canvas.axes.cla()
    canvas.draw_idle()
    canvas.axes.set_title(title)
    canvas.axes.set_xlabel(xlabel)
    canvas.axes.set_ylabel(ylabel)


# A surprisingly ugly way to pretty print a dictionary
def printOptions(options):
    # type: (Dict[int, str]) -> None
    print(("\n" + dumps(options, indent=4) + "\n")
          .replace('"', '').replace(',', ''))


def addOption(csvRow, lineNum, indices, options):
    # type: (List[str], int, Dict[str, int], Dict[int, str]) -> None
    startTime = makeTimeFromStr(csvRow, indices["startIdx"])
    endTime = makeTimeFromStr(csvRow, indices["endIdx"])
    rate = csvRow[indices["timeIntIdx"]]
    options[lineNum] = ("{START} to {END} ({RATE}s sample interval)"
                        .format(START=startTime, END=endTime,
                                RATE=rate))


def getSelection(duration, suffix, options, name=None):
    # type: (float, str, Dict[int, str], str) -> int
    # Running a new Q0 measurement or heater calibration is always
    # presented as the last option in the list
    
    options[max(options) + 1
    if options else 1] = ("Launch new {TYPE} ({DUR} hours)"
                          .format(TYPE=suffix, DUR=duration))
    
    # The keys in options are line numbers in a CSV file. We want to present
    # them to the user in a more friendly format, starting from 1 and counting
    # up.
    renumberedOptions = OrderedDict()
    optionMap = {}
    i = 1
    for key in options:
        renumberedOptions[i] = options[key]
        optionMap[i] = key
        i += 1
    
    printOptions(renumberedOptions)
    formatter = "Please select a {TYPE} option for {NAME} (hit enter for option 1): "
    selection = getNumInputFromLst(formatter.format(TYPE=suffix, NAME=name),
                                   renumberedOptions.keys(), int, True)
    
    return optionMap[selection]


def drawAndShow():
    # type: () -> None
    plt.draw()
    plt.show()


# # Gets raw data from MySampler and does some of the prep work necessary for
# # collapsing all of the heater DES and ACT values down into summed values
# # (rewrites the CSV header, stores the indices for the heater DES and ACT PVs)
# def getDataAndHeaterCols(startTime, numPoints, heaterDesPVs, heaterActPVs,
#                          allPVs, timeInt=MYSAMPLER_TIME_INTERVAL, verbose=True,
#                          gradPVs=None):
#     # type: (datetime, int, List[str], List[str], List[str], int, bool, List) -> Optional[List[str], List[int], List[int], List[int], _reader, List]
#
#     def populateHeaterCols(pvList, buff):
#         # type: (List[str], List[float]) -> None
#         for heaterPV in pvList:
#             buff.append(header.index(heaterPV))
#
#     data = getArchiverData(startTime, numPoints, allPVs, timeInt)
#     csvReader = getAndParseRawData(startTime, numPoints, allPVs, timeInt,
#                                    verbose)
#
#     if not csvReader:
#         return None
#
#     else:
#
#         header = csvReader.next()
#
#         heaterDesCols = []
#         populateHeaterCols(heaterDesPVs, heaterDesCols)
#
#         heaterActCols = []
#         populateHeaterCols(heaterActPVs, heaterActCols)
#
#         gradCols = []
#         if gradPVs:
#             populateHeaterCols(gradPVs, gradCols)
#
#         # So that we don't corrupt the indices while we're deleting them
#         colsToDelete = sorted(heaterDesCols + heaterActCols + gradCols, reverse=True)
#
#         for index in colsToDelete:
#             del header[index]
#
#         header.append("Electric Heat Load Setpoint")
#         header.append("Electric Heat Load Readback")
#         if gradPVs:
#             header.append("Effective Gradient")
#
#         return header, heaterActCols, heaterDesCols, colsToDelete, csvReader, gradCols


def collapseGradVals(row, gradCols):
    # type: (List[str], List[int]) -> Optional[float]
    
    grad = 0
    
    for col in gradCols:
        try:
            grad += float(row[col])
        except ValueError:
            grad = None
            break
    
    return grad


def collapseHeaterVals(row, heaterDesCols, heaterActCols):
    # type: (List[str], List[int], List[int]) -> Tuple[Optional[float], Optional[float]]
    
    heatLoadSetpoint = 0
    
    for col in heaterDesCols:
        try:
            heatLoadSetpoint += float(row[col])
        except ValueError:
            heatLoadSetpoint = None
            break
    
    heatLoadAct = 0
    
    for col in heaterActCols:
        try:
            heatLoadAct += float(row[col])
        except ValueError:
            heatLoadAct = None
            break
    
    return heatLoadSetpoint, heatLoadAct


def compatibleNext(csvReader):
    # type: (_reader) -> List
    if version_info[0] < 3:
        return csvReader.next()
    else:
        return next(csvReader)


def compatibleMkdirs(filePath: Path) -> Path:
    if version_info[0] < 3:
        if not path.exists(filePath):
            try:
                makedirs(filePath)
            # Guard against race condition per Stack Overflow
            except OSError as exc:
                if exc.errno != EEXIST:
                    raise
    else:
        makedirs(filePath, exist_ok=True)
    
    return filePath
