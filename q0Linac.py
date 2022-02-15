from scLinac import Cavity, Cryomodule, Rack, Linac, LINACS
from typing import Type, Dict, List
from datetime import datetime
from utils import (ARCHIVER_TIME_INTERVAL, TimeParams, ValveParams, q0Hash,
                   CryomodulePVs)
import dataSession


class Q0Cavity(Cavity, object):
    def __init__(self, cavityNum, rackObject):
        # type: (int, Rack) -> None

        super(Q0Cavity, self).__init__(cavityNum, rackObject)

        self._fieldEmissionPVs = None

        self.heaterDesPV = "CHTR:CM{CM}:1{CAV}55:HV:{SUFF}".format(CM=self.cryomodule.name,
                                                                   SUFF="POWER_SETPT",
                                                                   CAV=cavityNum)
        self.heaterActPV = "CHTR:CM{CM}:1{CAV}55:HV:{SUFF}".format(SUFF="POWER",
                                                                   CM=self.cryomodule.name,
                                                                   CAV=cavityNum)

        self._idxFile = ("q0Measurements/cm{CM}/cav{CAV}/q0MeasurementsCM{CM}CAV{CAV}.csv"
                         .format(CM=self.cryomodule.name, CAV=cavityNum))

        self._calibIdxFile = ("calibrations/cm{CM}/cav{CAV}/calibrationsCM{CM}CAV{CAV}.csv"
                              .format(CM=self.cryomodule.name, CAV=cavityNum))

        self.amplitudeDesPV = self.pvPrefix + "ADES"
        self.amplitudeActPV = self.pvPrefix + "AACTMEAN"


class Q0Cryomodule(Cryomodule, object):
    def __init__(self, cryoName, linacObject, _):
        # type: (str, Linac, Q0Cavity) -> None

        super(Q0Cryomodule, self).__init__(cryoName, linacObject, Q0Cavity)
        self.dsPressurePV = "CPT:CM{CM}:2302:DS:PRESS".format(CM=cryoName)
        self.jtModePV = "CPV:CM{CM}:3001:JT:MODE".format(CM=cryoName)
        self.jtPosSetpointPV = "CPV:CM{CM}:3001:JT:POS_SETPT".format(CM=cryoName)

        lvlFormatStr = "CLL:CM{CM}:{{INFIX}}:{{LOC}}:LVL".format(CM=cryoName)
        self.dsLevelPV = lvlFormatStr.format(INFIX="2301", LOC="DS")
        self.usLevelPV = lvlFormatStr.format(INFIX="2601", LOC="US")

        valveLockFormatter = "CPID:CM{CM}:3001:JT:CV_{{SUFF}}".format(CM=cryoName)
        self.cvMaxPV = valveLockFormatter.format(SUFF="MAX")
        self.cvMinPV = valveLockFormatter.format(SUFF="MIN")
        self.valvePV = valveLockFormatter.format(SUFF="VALUE")

        self.q0DataSessions = {}
        self.calibDataSessions = {}

        self.heaterDesPVs = []
        self.heaterActPVs = []

        for q0Cavity in self.cavities.values():
            self.heaterActPVs.append(q0Cavity.heaterActPV)
            self.heaterDesPVs.append(q0Cavity.heaterDesPV)

    def addCalibDataSessionFromGUI(self, calibrationSelection):
        # type: (Dict[str]) -> dataSession.CalibDataSession

        startTime = datetime.strptime(calibrationSelection["Start"], "%m/%d/%y %H:%M:%S")
        endTime = datetime.strptime(calibrationSelection["End"], "%m/%d/%y %H:%M:%S")

        try:
            timeInterval = int(calibrationSelection["MySampler Time Interval"])
        except (IndexError, ValueError):
            timeInterval = ARCHIVER_TIME_INTERVAL

        timeParams = TimeParams(startTime=startTime, endTime=endTime,
                                timeInterval=timeInterval)

        valveParams = ValveParams(refValvePos=float(calibrationSelection["JT Valve Position"]),
                                  refHeatLoadDes=float(calibrationSelection["Reference Heat Load (Des)"]),
                                  refHeatLoadAct=float(calibrationSelection["Reference Heat Load (Act)"]))

        return self.addCalibDataSession(timeParams, valveParams)

    def addCalibDataSession(self, timeParams, valveParams):
        # type: (TimeParams, ValveParams) -> dataSession.CalibDataSession

        sessionHash = q0Hash([timeParams, valveParams])

        # Only create a new calibration data session if one doesn't already
        # exist with those exact parameters
        if sessionHash not in self.calibDataSessions:
            cryomodulePVs = CryomodulePVs(valvePV=self.valvePV,
                                          dsLevelPV=self.dsLevelPV,
                                          usLevelPV=self.usLevelPV,
                                          dsPressurePV=self.dsPressurePV,
                                          heaterDesPVs=self.heaterDesPVs,
                                          heaterActPVs=self.heaterActPVs)

            session = dataSession.CalibDataSession(timeParams=timeParams,
                                                   valveParams=valveParams,
                                                   cryomodulePVs=cryomodulePVs,
                                                   cryoModuleName=self.name)
            self.calibDataSessions[sessionHash] = session

        return self.calibDataSessions[sessionHash]

    # getRefValveParams searches over the last timeRange hours for a period
    # when the liquid level was stable and then fetches an averaged JT valve
    # position during that time as well as summed cavity heater DES and ACT
    # values. All three numbers get packaged and returned in a ValveParams
    # object.
    # noinspection PyTupleAssignmentBalance,PyTypeChecker
    # def getRefValveParams(self, timeRange=JT_SEARCH_TIME_RANGE):
    #     # type: (float) -> ValveParams
    #
    #     def halfHourRoundDown(timeToRound):
    #         # type: (datetime) -> datetime
    #         newMinute = 0 if timeToRound.minute < 30 else 30
    #         return datetime(timeToRound.year, timeToRound.month,
    #                         timeToRound.day, timeToRound.hour, newMinute, 0)
    #
    #     print("\nDetermining required JT Valve position...")
    #
    #     loopStart = datetime.now() - timedelta(hours=12)
    #     searchStart = loopStart - timedelta(hours=HOURS_NEEDED_FOR_FLATNESS)
    #     searchStart = halfHourRoundDown(searchStart)
    #
    #     numPoints = int((60 / ARCHIVER_TIME_INTERVAL)
    #                     * (HOURS_NEEDED_FOR_FLATNESS * 60))
    #
    #     while (loopStart - searchStart) <= timedelta(hours=timeRange):
    #
    #         formatter = "Checking {START} to {END} for liquid level stability."
    #         searchEnd = searchStart + timedelta(hours=HOURS_NEEDED_FOR_FLATNESS)
    #         startStr = searchStart.strftime("%m/%d/%y %H:%M:%S")
    #         endStr = searchEnd.strftime("%m/%d/%y %H:%M:%S")
    #         print(formatter.format(START=startStr, END=endStr))
    #
    #         csvReaderLL = getAndParseRawData(searchStart, numPoints,
    #                                          [self.dsLevelPV], verbose=False)
    #
    #         if not csvReaderLL:
    #             raise AssertionError("No Archiver data found")
    #
    #         compatibleNext(csvReaderLL)
    #         llVals = []
    #
    #         for row in csvReaderLL:
    #             try:
    #                 llVals.append(float(row.pop()))
    #             except ValueError:
    #                 pass
    #
    #         # Fit a line to the liquid level over the last [numHours] hours
    #         m, b, _, _, _ = linregress(range(len(llVals)), llVals)
    #
    #         # If the LL slope is small enough, this may be a good period from
    #         # which to get a reference valve position & heater params
    #         if log10(abs(m)) < -5:
    #
    #             signals = ([self.valvePV] + self.heaterDesPVs
    #                        + self.heaterActPVs)
    #
    #             (header, heaterActCols, heaterDesCols, _,
    #              csvReader, _) = getDataAndHeaterCols(searchStart, numPoints,
    #                                                   self.heaterDesPVs,
    #                                                   self.heaterActPVs, signals,
    #                                                   verbose=False)
    #
    #             valveVals = []
    #             heaterDesVals = []
    #             heaterActVals = []
    #             valveIdx = header.index(self.valvePV)
    #
    #             for row in csvReader:
    #                 valveVals.append(float(row[valveIdx]))
    #                 (heatLoadDes,
    #                  heatLoadAct) = collapseHeaterVals(row, heaterDesCols,
    #                                                    heaterActCols)
    #                 heaterDesVals.append(heatLoadDes)
    #                 heaterActVals.append(heatLoadAct)
    #
    #             desValSet = set(heaterDesVals)
    #
    #             # We only want to use time periods in which there were no
    #             # changes made to the heater settings
    #             if len(desValSet) == 1:
    #                 desPos = round(mean(valveVals), 1)
    #                 heaterDes = desValSet.pop()
    #                 heaterAct = mean(heaterActVals)
    #
    #                 print("Stable period found.")
    #                 formatter = "{THING} is {VAL}"
    #                 print(formatter.format(THING="Desired JT valve position",
    #                                        VAL=desPos))
    #                 print(formatter.format(THING="Total heater DES setting",
    #                                        VAL=heaterDes))
    #
    #                 return ValveParams(desPos, heaterDes, heaterAct)
    #
    #         searchStart -= timedelta(hours=JT_SEARCH_HOURS_PER_STEP)
    #
    #     # If we broke out of the while loop without returning anything, that
    #     # means that the LL hasn't been stable enough recently. Wait a while for
    #     # it to stabilize and then try again.
    #     complaint = ("Cryo conditions were not stable enough over the last"
    #                  " {NUM} hours - determining new JT valve position. Please"
    #                  " do not adjust the heaters. Allow the PID loop to "
    #                  "regulate the JT valve position.")
    #     print(complaint.format(NUM=timeRange))
    #
    #     writeAndWait("\nWaiting 30 minutes for LL to stabilize then "
    #                  "retrying...")
    #
    #     start = datetime.now()
    #     while (datetime.now() - start).total_seconds() < 1800:
    #         writeAndWait(".", 5)
    #
    #     # Try again but only search the recent past. We have to manipulate the
    #     # search range a little bit due to how the search start time is rounded
    #     # down to the nearest half hour.
    #     return self.getRefValveParams(HOURS_NEEDED_FOR_FLATNESS + 0.5)


Q0_LINAC_OBJECTS: List[Linac] = []
for name, cryomoduleList in LINACS:
    Q0_LINAC_OBJECTS.append(Linac(name, cryomoduleList, cavityClass=Q0Cavity,
                                  cryomoduleClass=Q0Cryomodule))
