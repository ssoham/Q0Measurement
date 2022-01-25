from scLinac import Cavity, Cryomodule, Rack, Linac, LINACS
from typing import Type, Dict
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


class Q0Cryomodule(Cryomodule, object):
    def __init__(self, cryoName, linacObject, cavityClass=Q0Cavity):
        # type: (str, Linac, Cavity) -> None

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

        for q0Cavity in self.cavities:
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


Q0_LINAC_OBJECTS = []
for name, cryomoduleList in LINACS:
    Q0_LINAC_OBJECTS.append(Linac(name, cryomoduleList, cavityClass=Q0Cavity,
                                  cryomoduleClass=Q0Cryomodule))
