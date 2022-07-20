from os.path import isfile

import json
import numpy as np
import os
from datetime import datetime, timedelta
from epics import PV, caget, caput
from lcls_tools.superconducting.scLinac import (Cavity, CryoDict, Cryomodule,
                                                Linac, Magnet, Piezo, Rack, SSA,
                                                StepperTuner)
from numpy import log10, mean, nanmean
from operator import itemgetter
from scipy.stats import linregress
from subprocess import CalledProcessError
from time import sleep
from typing import Dict, List, Tuple

import dataSession
import q0Utils as utils


class Q0Cavity(Cavity, object):
    def __init__(self, cavityNum: int, rackObject: Rack, ssaClass=SSA,
                 stepperClass=StepperTuner, piezoClass=Piezo):
        super(Q0Cavity, self).__init__(cavityNum, rackObject)
        
        self._fieldEmissionPVs = None
        
        self._idxFile = ("q0Measurements/cm{CM}/cav{CAV}/q0MeasurementsCM{CM}CAV{CAV}.csv"
                         .format(CM=self.cryomodule.name, CAV=cavityNum))
        
        self._calibIdxFile = ("calibrations/cm{CM}/cav{CAV}/calibrationsCM{CM}CAV{CAV}.csv"
                              .format(CM=self.cryomodule.name, CAV=cavityNum))
        
        self.selAmplitudeActPV.add_callback(self.quenchCheckCallback)
        
        self.gradientActPV = PV(self.pvPrefix + "GACTMEAN")
        
        self.llrfDataAcqEnablePVs: List[PV] = [PV(self.pvPrefix
                                                  + "{infix}:ENABLE".format(infix=infix))
                                               for infix in ["CAV", "FWD", "REV"]]
        
        self.llrfPVValuePairs: List[Tuple[PV, float]] = [(PV(self.pvPrefix + "ACQ_MODE"), 1),
                                                         (PV(self.pvPrefix + "ACQ_HLDOFF"), 0.1),
                                                         (PV(self.pvPrefix + "STAT_START"), 0.065),
                                                         (PV(self.pvPrefix + "STAT_WIDTH"), 0.004),
                                                         (PV(self.pvPrefix + "DECIM"), 255)]
        
        self.quenchBypassPV: str = self.pvPrefix + "QUENCH_BYP_RBV"
    
    def checkAcqControl(self):
        """
        Checks that the parameters associated with acquisition of the cavity RF
        waveforms are configured properly
        :return:
        """
        print("Checking Waveform Data Acquisition Control...")
        for pv in self.llrfDataAcqEnablePVs:
            if pv.value != 1:
                print("Enabling {pv}".format(pv=pv.pvname))
                pv.put(1)
        
        for pv, expectedValue in self.llrfPVValuePairs:
            if pv.value != expectedValue:
                print("Setting {pv}".format(pv=pv.pvname))
                pv.put(expectedValue)
    
    def quenchCheckCallback(self, **kw):
        """
        This is a really unsophisticated way of checking for a quench if the
        interlock is bypassed (better to have something than nothing)
        :param kw:
        :return:
        """
        sleep(0.1)
        
        if self.selAmplitudeActPV.value < (self.selAmplitudeDesPV.value * 0.9):
            # If the EPICs quench detection is disabled and we see a quench,
            # shut the cavity down
            if caget(self.quenchBypassPV) == 1:
                raise utils.QuenchError
            # If the EPICs quench detection is enabled just print a warning
            # message
            else:
                print(str(utils.QuenchError))


class CalibrationRun:
    def __init__(self, heat_load: float):
        self.ll_buffer: List[float] = []
        self.heat_load: float = heat_load
        self._dll_dt = None
    
    @property
    def dll_dt(self):
        if not self._dll_dt:
            slope, intercept, r_val, p_val, std_err = linregress(
                    range(len(self.ll_buffer)), self.ll_buffer)
            self._dll_dt = slope
        return self._dll_dt


class Calibration:
    def __init__(self):
        self.data: List[CalibrationRun] = []
        self._slope = None
        self.adjustment = 0
    
    def clear(self):
        self.data: List[CalibrationRun] = []
        self._slope = None
        self.adjustment = 0
    
    @property
    def slope(self):
        if not self._slope:
            heat_loads = []
            dll_dts = []
            for run in self.data:
                heat_loads.append(run.heat_load)
                dll_dts.append(run.dll_dt)
            
            slope, intercept, r_val, p_val, std_err = linregress(
                    heat_loads, dll_dts)
            self.adjustment = intercept
            self._slope = slope
        return self._slope


class Q0Cryomodule(Cryomodule, object):
    def __init__(self, cryoName: str, linacObject: Linac, isHarmonicLinearizer,
                 cavityClass=Q0Cavity, magnetClass=Magnet,
                 stepperClass=StepperTuner, piezoClass=Piezo,
                 rackClass=Rack, ssaClass=SSA):
        
        super().__init__(cryoName, linacObject,
                         isHarmonicLinearizer=isHarmonicLinearizer,
                         cavityClass=Q0Cavity)
        self.cavities: Dict[int, Q0Cavity]
        self.dsPressurePV = "CPT:CM{CM}:2302:DS:PRESS".format(CM=cryoName)
        
        self.jtModePV: str = self.jtPrefix + "MODE"
        self.jtManualSelectPV: str = self.jtPrefix + "MANUAL"
        self.jtAutoSelectPV: str = self.jtPrefix + "AUTO"
        self.dsLiqLevSetpointPV: str = self.jtPrefix + "SP_RQST"
        
        self.jtManPosSetpointPV: str = self.jtPrefix + "MANPOS_RQST"
        
        self.jtValveReadbackPV: str = self.jtPrefix + "ORBV"
        
        self.q0DataSessions = {}
        self.calibDataSessions = {}
        
        self.heaterDesPVs: List[str] = [q0Cavity.heater.powerDesPV for q0Cavity in self.cavities.values()]
        self.heaterActPVs: List[str] = [q0Cavity.heater.powerActPV for q0Cavity in self.cavities.values()]
        
        self.valveParams: utils.ValveParams = None
        
        self._calibIdxFile = ("calibrations/cm{CM}/calibrationsCM{CM}.json"
                              .format(CM=self.name))
        self._q0IdxFile = ("q0Measurements/cm{CM}/q0MeasurementsCM{CM}.json"
                           .format(CM=self.name))
        
        self.cryomodulePVs = utils.CryomodulePVs(valvePV=self.jtValveReadbackPV,
                                                 dsLevelPV=self.dsLevelPV,
                                                 usLevelPV=self.usLevelPV,
                                                 dsPressurePV=self.dsPressurePV,
                                                 heaterDesPVs=self.heaterDesPVs,
                                                 heaterActPVs=self.heaterActPVs)
        
        self.ll_buffer: np.array = np.empty(utils.NUM_LL_POINTS_TO_AVG)
        self.ll_buffer[:] = np.nan
        self._ll_buffer_size = utils.NUM_LL_POINTS_TO_AVG
        self.ll_buffer_idx = 0
        
        self.measurement_buffer = []
        self.calibration = Calibration()
        self.current_calibration_run = None
    
    @property
    def ll_buffer_size(self):
        return self._ll_buffer_size
    
    @ll_buffer_size.setter
    def ll_buffer_size(self, value):
        self._ll_buffer_size = value
        self.clear_ll_buffer()
    
    def clear_ll_buffer(self):
        self.ll_buffer = np.empty(self.ll_buffer_size)
        self.ll_buffer[:] = np.nan
        self.ll_buffer_idx = 0
    
    def monitor_ll(self, value, **kwargs):
        self.ll_buffer[self.ll_buffer_idx] = value
        self.ll_buffer_idx = (self.ll_buffer_idx + 1) % self.ll_buffer_size
        if self.current_calibration_run:
            self.current_calibration_run.ll_buffer.append(value)
    
    def addCalibDataSession(self, timeParams: utils.TimeParams,
                            valveParams: utils.ValveParams) -> dataSession.CalibDataSession:
        
        sessionHash = utils.q0Hash([timeParams, valveParams])
        
        # Only create a new calibration data session if one doesn't already
        # exist with those exact parameters
        if sessionHash not in self.calibDataSessions:
            session = dataSession.CalibDataSession(timeParams=timeParams,
                                                   valveParams=valveParams,
                                                   cryomodulePVs=self.cryomodulePVs,
                                                   cryoModuleName=self.name)
            self.calibDataSessions[sessionHash] = session
        
        return self.calibDataSessions[sessionHash]
    
    def addCalibDataSessionFromGUI(self, calibrationSelection: Dict[str, str]) -> dataSession.CalibDataSession:
        
        startTime = datetime.strptime(calibrationSelection["Start"], "%m/%d/%y %H:%M:%S")
        endTime = datetime.strptime(calibrationSelection["End"], "%m/%d/%y %H:%M:%S")
        
        try:
            timeInterval = int(calibrationSelection["Archiver Time Interval"])
        except (IndexError, ValueError):
            timeInterval = utils.ARCHIVER_TIME_INTERVAL
        
        timeParams = utils.TimeParams(startTime=startTime, endTime=endTime,
                                      timeInterval=timeInterval)
        
        valveParams = utils.ValveParams(refValvePos=float(calibrationSelection["JT Valve Position"]),
                                        refHeatLoadDes=float(calibrationSelection["Reference Heat Load (Des)"]),
                                        refHeatLoadAct=float(calibrationSelection["Reference Heat Load (Act)"]))
        
        return self.addCalibDataSession(timeParams=timeParams, valveParams=valveParams)
    
    def addQ0DataSession(self, timeParams: utils.TimeParams,
                         valveParams: utils.ValveParams, refGradVal: float = None,
                         calibSession: dataSession.CalibDataSession = None) -> dataSession.CalibDataSession:
        
        sessionHash = utils.q0Hash([timeParams, self.name, calibSession, refGradVal])
        
        # Only create a new calibration data session if one doesn't already
        # exist with those exact parameters
        if sessionHash not in self.q0DataSessions:
            session = self.genQ0DataSession(timeParams, valveParams, refGradVal,
                                            calibSession)
            self.q0DataSessions[sessionHash] = session
        
        return self.q0DataSessions[sessionHash]
    
    @property
    def averagedLiquidLevelDS(self) -> float:
        # try to do averaging of the last NUM_LL_POINTS_TO_AVG points to account
        # for signal noise
        avg_ll = nanmean(self.ll_buffer)
        if np.isnan(avg_ll):
            return caget(self.dsLevelPV)
        else:
            return avg_ll
    
    @property
    def calibIdxFile(self) -> str:
        
        if not isfile(self._calibIdxFile):
            os.makedirs(os.path.dirname(self._calibIdxFile), exist_ok=True)
            with open(self._calibIdxFile, "w+") as f:
                json.dump([], f)
        
        return self._calibIdxFile
    
    def fillAndLock(self, desiredLevel=utils.MAX_DS_LL):
        print("Setting the liquid level setpoint to its current readback")
        caput(self.dsLiqLevSetpointPV, caget(self.dsLevelPV), wait=True)
        
        # Allow the JT valve to regulate so that we fill (slowly)
        caput(self.jtAutoSelectPV, True, wait=True)
        
        self.rampLiquidLevel(desiredLevel)
        self.waitForLL(desiredLevel)
        
        # set to manual
        caput(self.jtManualSelectPV, True, wait=True)
        
        if caget(self.jtModePV) != utils.JT_MANUAL_MODE_VALUE:
            raise utils.CryoError("Unable to set JT to manual")
        
        caput(self.jtManPosSetpointPV, self.valveParams.refValvePos, wait=True)
        self.waitForJT(self.valveParams.refValvePos)
    
    def genQ0DataSession(self, timeParams: utils.TimeParams,
                         valveParams: utils.ValveParams, refGradVal: float = None,
                         calibSession: dataSession.CalibDataSession = None) -> dataSession.Q0DataSession:
        return dataSession.Q0DataSession(timeParams=timeParams, valveParams=valveParams, refGradVal=refGradVal,
                                         calibSession=calibSession, cryoModuleName=self.name,
                                         cryomodulePVs=self.cryomodulePVs)
    
    def getRefValveParams(self, start_time: datetime, end_time: datetime):
        print(f"\nSearching {start_time} to {end_time} for period of JT stability")
        window_start = start_time
        window_end = start_time + utils.DELTA_NEEDED_FOR_FLATNESS
        while window_end <= end_time:
            print(f"Checking window {window_start} to {window_end}")
            
            data = utils.ARCHIVER.getValuesOverTimeRange(pvList=[self.dsLevelPV],
                                                         startTime=window_start,
                                                         endTime=window_end)
            llVals = data.values[self.dsLevelPV]
            
            # Fit a line to the liquid level over the last [numHours] hours
            m, b, r, _, _ = linregress(range(len(llVals)), llVals)
            print(f"r^2 of linear fit: {r ** 2}")
            
            # If the LL slope is small enough, this may be a good period from
            # which to get a reference valve position & heater params
            if log10(abs(m)) < -5:
                
                signals = ([self.jtValveReadbackPV] + self.heaterDesPVs
                           + self.heaterActPVs)
                
                data = utils.ARCHIVER.getValuesOverTimeRange(startTime=window_start,
                                                             endTime=window_end,
                                                             pvList=signals)
                valveVals = data.values[self.jtValveReadbackPV]
                heaterDesVals = [sum(x) for x in zip(*itemgetter(*self.heaterDesPVs)(data.values))]
                heaterActVals = [sum(x) for x in zip(*itemgetter(*self.heaterActPVs)(data.values))]
                
                desValSet = set(heaterDesVals)
                
                # We only want to use time periods in which there were no
                # changes made to the heater settings
                if len(desValSet) == 1:
                    desPos = round(mean(valveVals), 1)
                    heaterDes = desValSet.pop()
                    heaterAct = mean(heaterActVals)
                    
                    print("Stable period found.")
                    print(f"Desired JT valve position: {desPos}")
                    print(f"Total heater des setting: {heaterDes}")
                    
                    return utils.ValveParams(desPos, heaterDes, heaterAct)
            
            window_end += utils.JT_SEARCH_OVERLAP_DELTA
            window_start += utils.JT_SEARCH_OVERLAP_DELTA
        
        # If we broke out of the while loop without returning anything, that
        # means that the LL hasn't been stable enough recently. Wait a while for
        # it to stabilize and then try again.
        complaint = ("Stable cryo conditions not found in search window"
                     " - determining new JT valve position. Please"
                     " do not adjust the heaters. Allow the PID loop to "
                     "regulate the JT valve position.")
        
        utils.writeAndWait("\nWaiting 30 minutes for LL to stabilize then "
                           "retrying...")
        
        start = datetime.now()
        while (datetime.now() - start).total_seconds() < 1800:
            utils.writeAndWait(".", 5)
        
        # Try again but only search the recent past. We have to manipulate the
        # search range a little bit due to how the search start time is rounded
        # down to the nearest half hour.
        return self.getRefValveParams(start_time=start_time + timedelta(minutes=30),
                                      end_time=end_time + timedelta(minutes=30))
    
    def holdAmplitude(self, desiredAmplitudes, minLL=utils.MIN_DS_LL, amplitudeTolerance=0.01):
        # type: (Dict[int, float], float, float) -> datetime
        
        startTime = datetime.now()
        
        print("\nStart time: {START}".format(START=startTime))
        
        utils.writeAndWait(
                "\nWaiting for the LL to drop {DIFF}% or below {MIN}%...".format(
                        MIN=minLL, DIFF=utils.TARGET_LL_DIFF))
        
        startingLevel = self.averagedLiquidLevelDS
        avgLevel = startingLevel
        
        prevDiffs = {i: (self.cavities[i].amplitudeActPVObject.value
                         - desiredAmplitudes[i]) for i in desiredAmplitudes.keys()}
        steps = {i: 0.01 for i in desiredAmplitudes.keys()}
        amplitudes = {i: self.cavities[i].amplitudeActPVObject.value
                      for i in desiredAmplitudes.keys()}
        
        # TODO figure out how to squish this with FE measurements
        while ((startingLevel - avgLevel) < utils.TARGET_LL_DIFF
               and (avgLevel > minLL)):
            
            for cavity in self.cavities.values():
                if cavity.cavNum not in desiredAmplitudes:
                    continue
                
                currAmp = cavity.amplitudeActPVObject.value
                
                amplitudes[cavity.cavNum] = cavity.quenchCheckCallback(amplitudes[cavity.cavNum])
                diff = amplitudes[cavity.cavNum] - desiredAmplitudes[cavity.cavNum]
                
                mult = 1 if (diff <= 0) else -1
                
                overshot = ((prevDiffs[cavity.cavNum] >= 0 > diff)
                            or (prevDiffs[cavity.cavNum] <= 0 < diff))
                
                step = steps[cavity.cavNum]
                
                # This only works if we're in SEL mode; in pulsed mode the scaling
                # is messed up because a 1% change in the drive doesn't correspond
                # to a 1 MV/m change in the gradient
                if abs(diff) < amplitudeTolerance:
                    pass
                elif (abs(diff) < (2 * step) or overshot) and (step > amplitudeTolerance):
                    step *= 0.5
                else:
                    step *= 1.5
                
                cavity.amplitudeActPVObject.put(currAmp + (mult * step))
                
                prevDiffs[cavity.cavNum] = diff
            
            utils.writeAndWait(".")
            avgLevel = self.averagedLiquidLevelDS
        
        print("\nEnd Time: {END}".format(END=datetime.now()))
        duration = (datetime.now() - startTime).total_seconds() / 3600
        print("Duration in hours: {DUR}".format(DUR=duration))
        return startTime
    
    def launchHeaterRun(self, delta: float = utils.CAL_HEATER_DELTA) -> None:
        
        print("Ramping heaters to the next setting...")
        
        self.walkHeaters(delta)
        
        utils.writeAndWait(utils.RUN_STATUS_MSSG)
        
        startingLevel = self.averagedLiquidLevelDS
        avgLevel = startingLevel
        
        self.current_calibration_run = CalibrationRun(delta * 8)
        self.calibration.data.append(self.current_calibration_run)
        
        while ((startingLevel - avgLevel) < utils.TARGET_LL_DIFF and (
                avgLevel > utils.MIN_DS_LL)):
            utils.writeAndWait(".", 10)
            avgLevel = self.averagedLiquidLevelDS
            print(avgLevel)
        
        print("\nDone\n")
    
    @property
    def totalHeatAct(self) -> float:
        heatAct = 0
        for pv in self.heaterActPVs:
            heatAct += caget(pv)
        return heatAct
    
    @property
    def totalHeatDes(self) -> float:
        heatDes = 0
        for pv in self.heaterDesPVs:
            heatDes += caget(pv)
        return heatDes
    
    def rampLiquidLevel(self, desiredLevel: float):
        """
        We'll see if this ends up being necessary later, but this is currently a
        requirement from the cryo group to slowly ramp the setpoint instead of
        just slamming the desired liquid level in at once
        :param desiredLevel: float
        :return:
        """
        # utils.writeAndWait("\nWaiting for the liquid level setpoint to be {setpoint}"
        #                    .format(setpoint=desiredLevel))
        fullDelta = desiredLevel - caget(self.dsLiqLevSetpointPV)
        if abs(fullDelta) > 0.01:
            print(f"Liquid level setpoint needs to change by {fullDelta}")
            steps = int(abs(fullDelta / utils.JT_STEP_SIZE_PER_SECOND))
            stepDelta = fullDelta / steps
            
            for i in range(steps):
                if abs(desiredLevel - caget(self.dsLiqLevSetpointPV)) <= 0.01:
                    break
                print(f"Step {i} out of {steps}")
                new_val = caget(self.dsLiqLevSetpointPV) + stepDelta
                print(f"Setting {self.dsLiqLevSetpointPV} to {new_val}")
                caput(self.dsLiqLevSetpointPV, new_val, wait=True)
                sleep(1)
                # utils.writeAndWait(".")
            
            caput(self.dsLiqLevSetpointPV, desiredLevel, wait=True)
        
        print("liquid level setpoint at required value.")
    
    def takeNewCalibration(self, initial_heat_load: int,
                           jt_search_start: datetime = None,
                           jt_search_end: datetime = None,
                           desired_ll: float = utils.MAX_DS_LL,
                           ll_drop: float = utils.TARGET_LL_DIFF,
                           heater_delta: float = utils.CAL_HEATER_DELTA,
                           num_cal_steps: int = utils.NUM_CAL_STEPS):
        """
        Launches a new cryomodule calibration. Expected to take ~4/5 hours
        :param num_cal_steps:
        :param heater_delta:
        :param ll_drop:
        :param desired_ll:
        :param jt_search_end:
        :param jt_search_start:
        :param initial_heat_load: provided as user input in the GUI
                                           measurement settings
        :return:
        """
        
        self.calibration.clear()
        
        if not self.valveParams:
            self.valveParams = self.getRefValveParams(start_time=jt_search_start,
                                                      end_time=jt_search_end)
        
        deltaTot = self.valveParams.refHeatLoadDes - self.totalHeatDes
        
        startTime = datetime.now().replace(microsecond=0)
        
        # Lumping in the initial
        self.walkHeaters(deltaTot / 8)
        
        self.fillAndLock(desired_ll)
        
        self.launchHeaterRun(initial_heat_load / 8)
        self.current_calibration_run = None
        
        for _ in range(num_cal_steps):
            if (self.averagedLiquidLevelDS - utils.MIN_DS_LL) < ll_drop:
                self.fillAndLock(desired_ll)
            self.launchHeaterRun(heater_delta)
            self.current_calibration_run = None
        
        endTime = datetime.now().replace(microsecond=0)
        
        print("\nStart Time: {START}".format(START=startTime))
        print("End Time: {END}".format(END=datetime.now()))
        
        duration = (datetime.now() - startTime).total_seconds() / 3600
        print("Duration in hours: {DUR}".format(DUR=duration))
        
        self.walkHeaters(-((num_cal_steps * heater_delta) + (initial_heat_load / 8)))
        
        timeParams = utils.TimeParams(startTime, endTime, utils.ARCHIVER_TIME_INTERVAL)
        
        # dataSession = self.addCalibDataSession(timeParams, self.valveParams)
        
        # Record this calibration dataSession's metadata
        
        newData = {"Total Reference Heater Setpoint": self.valveParams.refHeatLoadDes,
                   "Total Reference Heater Readback": self.valveParams.refHeatLoadAct,
                   "JT Valve Position"              : self.valveParams.refValvePos,
                   "Start Time"                     : startTime.strftime("%m/%d/%y %H:%M:%S"),
                   "End Time"                       : endTime.strftime("%m/%d/%y %H:%M:%S"),
                   "Archiver Time Interval"         : utils.ARCHIVER_TIME_INTERVAL,
                   "Calculated Adjustment"          : self.calibration.adjustment,
                   "Calculated Heat vs dll/dt Slope": self.calibration.slope}
        
        with open(self.calibIdxFile, 'r+') as f:
            data: List = json.load(f)
            data.append(newData)
            
            # go to the beginning of the file to overwrite the existing data structure
            f.seek(0)
            json.dump(data, f)
            f.truncate()
        
        # return dataSession, self.valveParams
    
    def takeNewQ0Measurement(self, desiredAmplitudes: Dict[int, float],
                             calibSession: dataSession.CalibDataSession = None,
                             valveParams: utils.ValveParams = None) -> (dataSession.Q0DataSession, utils.ValveParams):
        try:
            if not valveParams:
                valveParams = self.getRefValveParams()
            
            deltaTot = utils.ValveParams.refHeatLoadDes - self.totalHeatDes
            self.walkHeaters(deltaTot / 8)
            
            for cavity in self.cavities.values():
                print("\nRunning up Cavity {CAV}...".format(CAV=cavity.cavNum))
                
                cavity.checkAcqControl()
                cavity.setPowerStateSSA(True)
                
                cavity.pulseDriveLevelPV.put(utils.SAFE_PULSED_DRIVE_LEVEL)
                cavity.ssa.runCalibration()
                cavity.runCalibration()
                
                cavity.rfModePV.put(utils.RF_MODE_PULSE)
                
                cavity.setStateRF(True)
                cavity.pushGoButton()
                
                cavity.checkAndSetOnTime()
                cavity.amplitudeDesPVObject.put(2)
                
                cavity.rfModePV.put(utils.RF_MODE_SELA)
                
                cavity.walkToAmplitude(desiredAmplitudes[cavity.cavNum])
            
            self.waitForCryo(valveParams.refValvePos)
            
            startTime = self.holdAmplitude(desiredAmplitudes).replace(microsecond=0)
            
            for cavity in self.cavities.values():
                
                if cavity.cavNum not in desiredAmplitudes:
                    continue
                
                cavity.walkToAmplitude(5)
                cavity.powerDown()
            
            # self.waitForCryo(utils.ValveParams.refValvePos)
            self.waitForLL()
            self.walkHeaters(utils.FULL_MODULE_CALIBRATION_LOAD)
            self.waitForJT(utils.ValveParams.refValvePos)
            self.launchHeaterRun(0)
            endTime = datetime.now().replace(microsecond=0)
            
            print("\nEnd time: {END}".format(END=endTime))
            self.walkHeaters(-utils.FULL_MODULE_CALIBRATION_LOAD)
            
            utils.TimeParams = utils.TimeParams(startTime, endTime, utils.ARCHIVER_TIME_INTERVAL)
            
            desiredGradient = 0
            
            for grad in desiredAmplitudes.values():
                desiredGradient += grad
            
            session = self.addQ0DataSession(utils.TimeParams, utils.ValveParams,
                                            refGradVal=desiredGradient,
                                            calibSession=calibSession)
            
            desGrads = []
            totGrad = 0
            for i in range(8):
                if (i + 1) in desiredAmplitudes:
                    desGrads.append(desiredAmplitudes[i + 1])
                    totGrad += desiredAmplitudes[i + 1]
                else:
                    desGrads.append(0)
            
            # with open(self.q0IdxFile, 'a') as f:
            #     csvWriter = writer(f)
            #     csvWriter.writerow(
            #             [self.cryModNumJLAB, utils.ValveParams.refHeatLoadDes,
            #              utils.ValveParams.refHeatLoadAct, utils.ValveParams.refValvePos]
            #             + desGrads + [totGrad, startTime.strftime("%m/%d/%y %H:%M:%S"),
            #                           endTime.strftime("%m/%d/%y %H:%M:%S"),
            #                           utils.ARCHIVER_TIME_INTERVAL])
            
            print("\nStart Time: {START}".format(START=startTime))
            print("End Time: {END}".format(END=endTime))
            
            duration = (endTime - startTime).total_seconds() / 3600
            print("Duration in hours: {DUR}".format(DUR=duration))
            
            return session, utils.ValveParams
        
        except(CalledProcessError, IndexError, OSError, ValueError,
               AssertionError, KeyboardInterrupt) as e:
            utils.writeAndFlushStdErr(
                    "Procedure failed with error:\n{E}\n".format(E=e))
            for cavity in self.cavities.values():
                cavity.powerDown()
    
    def waitForCryo(self, refValvePos):
        # type: (float) -> None
        self.waitForLL()
        self.waitForJT(refValvePos)
    
    def waitForJT(self, refValvePos):
        # type: (float) -> None
        
        utils.writeAndWait("\nWaiting for JT Valve to be in manual...")
        
        # One way for the JT valve to be locked in the correct position is for
        # it to be in manual mode and at the desired value
        while caget(self.jtModePV) != utils.JT_MANUAL_MODE_VALUE:
            utils.writeAndWait(".", 5)
        
        utils.writeAndWait(f"\nWaiting for JT Valve to be locked at {refValvePos}...")
        while (caget(self.jtManPosSetpointPV) - refValvePos) > 0.01:
            utils.writeAndWait(".", 5)
        
        utils.writeAndWait(f"\nWaiting for JT Valve position to be in tolerance...")
        # Wait for the valve position to be within tolerance before continuing
        while abs(caget(self.jtValveReadbackPV) - refValvePos) > utils.VALVE_POS_TOL:
            utils.writeAndWait(".", 5)
        
        utils.writeAndWait(" JT Valve locked.\n")
    
    # We consider the cryo situation to be good when the liquid level is high
    # enough and the JT valve is locked in the correct position
    
    def waitForLL(self, desiredLiquidLevel=utils.MAX_DS_LL):
        utils.writeAndWait("\nWaiting for downstream liquid level to be {LL}%..."
                           .format(LL=desiredLiquidLevel))
        
        while (desiredLiquidLevel - self.averagedLiquidLevelDS) > 0.01:
            print(self.averagedLiquidLevelDS)
            utils.writeAndWait(".", 5)
        
        utils.writeAndWait(" downstream liquid level at required value.")
    
    def walkHeaters(self, perHeaterDelta: float):
        
        if perHeaterDelta == 0:
            return
        
        formatter = "\nWalking CM{NUM} heaters {DIR} by {VAL}"
        dirStr = "up" if perHeaterDelta > 0 else "down"
        formatter = formatter.format(NUM=self.name, DIR=dirStr,
                                     VAL=abs(perHeaterDelta))
        print(formatter)
        
        if abs(perHeaterDelta) <= 1:
            for heaterSetpointPV in self.heaterDesPVs:
                caput(heaterSetpointPV, caget(heaterSetpointPV) + perHeaterDelta, wait=True)
        
        else:
            
            # This whole thing is so that we only do 8W/min
            # TODO clean this
            steps = abs(int(perHeaterDelta))
            finalDelta = abs(perHeaterDelta) - steps
            
            # 1 or -1 depending on the direction
            stepDelta = perHeaterDelta / steps
            
            for i in range(steps):
                print(f"Step {i + 1} out of {steps}")
                
                for heaterSetpointPV in self.heaterDesPVs:
                    new_val = caget(heaterSetpointPV) + stepDelta
                    print(f"setting {heaterSetpointPV} to {new_val}")
                    caput(heaterSetpointPV, new_val, wait=True)
                
                print(f"Waiting 5 seconds at {datetime.now()}")
                sleep(5)
            
            for heaterSetpointPV in self.heaterDesPVs:
                caput(heaterSetpointPV, caget(heaterSetpointPV) + (finalDelta * stepDelta), wait=True)
        
        utils.writeAndWait("\nWaiting 5s for cryo to stabilize...\n", 5)


Q0_CRYOMODULES: Dict[str, Q0Cryomodule] = CryoDict(cavityClass=Q0Cavity, cryomoduleClass=Q0Cryomodule)
