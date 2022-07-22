import json
import os
from datetime import datetime, timedelta
from os.path import isfile
from time import sleep
from typing import Dict, List

import numpy as np
from epics import caget, camonitor, camonitor_clear, caput
from lcls_tools.superconducting.scLinac import Cavity, CryoDict, Cryomodule, Magnet, Piezo, Rack, SSA, StepperTuner
from scipy.signal import medfilt
from scipy.stats import linregress

import q0_utils


class Q0Cavity(Cavity):
    def __init__(self, cavityNum, rackObject, ssaClass=SSA,
                 stepperClass=StepperTuner, piezoClass=Piezo):
        super().__init__(cavityNum, rackObject)
        self.ready_for_q0 = False
    
    def mark_ready(self):
        self.ready_for_q0 = True


class Q0Cryomodule(Cryomodule):
    def __init__(self, cryoName, linacObject, isHarmonicLinearizer,
                 cavityClass=Q0Cavity, magnetClass=Magnet,
                 stepperClass=StepperTuner, piezoClass=Piezo,
                 rackClass=Rack, ssaClass=SSA):
        super().__init__(cryoName, linacObject,
                         isHarmonicLinearizer=isHarmonicLinearizer,
                         cavityClass=Q0Cavity)
        
        self.jtModePV: str = self.jtPrefix + "MODE"
        self.jtManualSelectPV: str = self.jtPrefix + "MANUAL"
        self.jtAutoSelectPV: str = self.jtPrefix + "AUTO"
        self.dsLiqLevSetpointPV: str = self.jtPrefix + "SP_RQST"
        self.jtManPosSetpointPV: str = self.jtPrefix + "MANPOS_RQST"
        
        self.heater_prefix = f"CPIC:CM{self.name}:0000:EHCV:"
        self.heater_setpoint_pv: str = self.heater_prefix + "MANPOS_RQST"
        self.heater_manual_pv: str = self.heater_prefix + "MANUAL"
        self.heater_sequencer_pv: str = self.heater_prefix + "SEQUENCER"
        
        self.cryo_access_pv: str = f"CRYO:CM{self.name}:0:CAS_ACCESS"
        
        self.q0DataSessions = {}
        self.calibDataSessions = {}
        
        self.valveParams: q0_utils.ValveParams = None
        
        self._calib_idx_file = ("calibrations/cm{CM}/calibrationsCM{CM}.json"
                                .format(CM=self.name))
        self._q0_idx_file = ("q0Measurements/cm{CM}/q0MeasurementsCM{CM}.json"
                             .format(CM=self.name))
        
        self.ll_buffer: np.array = np.empty(q0_utils.NUM_LL_POINTS_TO_AVG)
        self.ll_buffer[:] = np.nan
        self._ll_buffer_size = q0_utils.NUM_LL_POINTS_TO_AVG
        self.ll_buffer_idx = 0
        
        self.measurement_buffer = []
        self.calibration: q0_utils.Calibration = q0_utils.Calibration()
        self.q0_measurement: q0_utils.Q0Measurement = None
        self.current_data_run: q0_utils.DataRun = None
        self.cavity_amplitudes = {}
    
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
        if self.current_data_run:
            self.current_data_run.ll_buffer.append(value)
    
    @property
    def averagedLiquidLevelDS(self) -> float:
        # try to do averaging of the last NUM_LL_POINTS_TO_AVG points to account
        # for signal noise
        avg_ll = np.nanmean(self.ll_buffer)
        if np.isnan(avg_ll):
            return caget(self.dsLevelPV)
        else:
            return avg_ll
    
    @property
    def q0_idx_file(self) -> str:
        
        if not isfile(self._q0_idx_file):
            os.makedirs(os.path.dirname(self._q0_idx_file), exist_ok=True)
            with open(self._q0_idx_file, "w+") as f:
                json.dump([], f)
        
        return self._q0_idx_file
    
    @property
    def calib_idx_file(self) -> str:
        
        if not isfile(self._calib_idx_file):
            os.makedirs(os.path.dirname(self._calib_idx_file), exist_ok=True)
            with open(self._calib_idx_file, "w+") as f:
                json.dump([], f)
        
        return self._calib_idx_file
    
    def fillAndLock(self, desiredLevel=q0_utils.MAX_DS_LL):
        
        caput(self.dsLiqLevSetpointPV, desiredLevel, wait=True)
        
        print(f"Setting JT to auto for refill to {desiredLevel}")
        caput(self.jtAutoSelectPV, 1, wait=True)
        self.waitForLL(desiredLevel)
        
        caput(self.jtManPosSetpointPV, self.valveParams.refValvePos, wait=True)
        self.lock_jt(self.valveParams.refValvePos)
    
    def getRefValveParams(self, start_time: datetime, end_time: datetime):
        print(f"\nSearching {start_time} to {end_time} for period of JT stability")
        window_start = start_time
        window_end = start_time + q0_utils.DELTA_NEEDED_FOR_FLATNESS
        while window_end <= end_time:
            print(f"\nChecking window {window_start} to {window_end}")
            
            data = q0_utils.ARCHIVER.getValuesOverTimeRange(pvList=[self.dsLevelPV],
                                                            startTime=window_start,
                                                            endTime=window_end)
            llVals = medfilt(data.values[self.dsLevelPV])
            
            # Fit a line to the liquid level over the last [numHours] hours
            m, b, r, _, _ = linregress(range(len(llVals)), llVals)
            print(f"r^2 of linear fit: {r ** 2}")
            print(f"Slope: {m}")
            
            # If the LL slope is small enough, this may be a good period from
            # which to get a reference valve position & heater params
            if np.log10(abs(m)) < -5:
                
                signals = [self.jtValveReadbackPV, self.heater_setpoint_pv,
                           self.heater_readback_pv]
                
                data = q0_utils.ARCHIVER.getValuesOverTimeRange(startTime=window_start,
                                                                endTime=window_end,
                                                                pvList=signals)
                
                desValSet = set(data.values[self.heater_setpoint_pv])
                print(f"number of heater setpoints during this time: {len(desValSet)}")
                
                # We only want to use time periods in which there were no
                # changes made to the heater settings
                if len(desValSet) == 1:
                    desPos = round(np.mean(data.values[self.jtValveReadbackPV]), 1)
                    heaterDes = desValSet.pop()
                    heaterAct = np.mean(data.values[self.heater_readback_pv])
                    
                    print("Stable period found.")
                    print(f"Desired JT valve position: {desPos}")
                    print(f"Total heater des setting: {heaterDes}")
                    
                    self.valveParams = q0_utils.ValveParams(desPos, heaterDes, heaterAct)
                    return self.valveParams
            
            window_end += q0_utils.JT_SEARCH_OVERLAP_DELTA
            window_start += q0_utils.JT_SEARCH_OVERLAP_DELTA
        
        # If we broke out of the while loop without returning anything, that
        # means that the LL hasn't been stable enough recently. Wait a while for
        # it to stabilize and then try again.
        print("Stable cryo conditions not found in search window  - determining"
              " new JT valve position. Please do not adjust the heaters. Allow "
              "the PID loop to regulate the JT valve position.")
        
        print("Waiting 30 minutes for LL to stabilize then retrying")
        
        start = datetime.now()
        while (datetime.now() - start) < timedelta(minutes=30):
            sleep(5)
        
        # Try again but only search the recent past. We have to manipulate the
        # search range a little bit due to how the search start time is rounded
        # down to the nearest half hour.
        return self.getRefValveParams(start_time=start_time + timedelta(minutes=30),
                                      end_time=end_time + timedelta(minutes=30))
    
    def launchHeaterRun(self, delta: float = q0_utils.CAL_HEATER_DELTA,
                        target_ll_diff: float = q0_utils.TARGET_LL_DIFF) -> None:
        
        print(f"Changing heater by {delta}")
        
        caput(self.heater_setpoint_pv, caget(self.heater_readback_pv) + delta, wait=True)
        
        print(q0_utils.RUN_STATUS_MSSG)
        
        self.current_data_run: q0_utils.HeaterRun = q0_utils.HeaterRun(delta)
        self.calibration.heater_runs.append(self.current_data_run)
        
        self.current_data_run.start_time = datetime.now()
        self.wait_for_ll_drop(target_ll_diff)
        self.current_data_run.end_time = datetime.now()
        
        print("Heater run done")
    
    def wait_for_ll_drop(self, target_ll_diff):
        startingLevel = self.averagedLiquidLevelDS
        avgLevel = startingLevel
        while ((startingLevel - avgLevel) < target_ll_diff
               and (avgLevel > q0_utils.MIN_DS_LL)):
            print(f"Averaged level is {avgLevel}; waiting 10s")
            avgLevel = self.averagedLiquidLevelDS
            sleep(10)
    
    def fill_pressure_buffer(self, value, **kwargs):
        if self.q0_measurement:
            self.q0_measurement.rf_run.pressure_buffer.append(value)
    
    def takeNewQ0Measurement(self, desiredAmplitudes: Dict[int, float],
                             desired_ll: float = q0_utils.MAX_DS_LL,
                             ll_drop: float = q0_utils.TARGET_LL_DIFF):
        
        for cav_num in desiredAmplitudes.keys():
            while not self.cavities[cav_num].ready_for_q0:
                print(f"Waiting for cavity {cav_num} to be ready")
                sleep(5)
        
        self.current_data_run: q0_utils.RFRun = self.q0_measurement.rf_run
        start_time = datetime.now()
        
        self.current_data_run.start_time = datetime.now()
        self.wait_for_ll_drop(ll_drop)
        self.current_data_run.end_time = datetime.now()
        
        self.current_data_run = None
        
        self.fillAndLock(desired_ll)
        self.launchHeaterRun(q0_utils.FULL_MODULE_CALIBRATION_LOAD,
                             target_ll_diff=ll_drop)
        self.q0_measurement.heater_run = self.current_data_run
        
        camonitor_clear(self.dsPressurePV)
        self.q0_measurement.save_data(timestamp=start_time, cm_name=self.name)
        self.current_data_run = None
        
        end_time = datetime.now()
        caput(self.heater_setpoint_pv,
              caget(self.heater_readback_pv) - q0_utils.FULL_MODULE_CALIBRATION_LOAD)
        
        print("\nStart Time: {START}".format(START=start_time))
        print("End Time: {END}".format(END=end_time))
        
        duration = (end_time - start_time).total_seconds() / 3600
        print("Duration in hours: {DUR}".format(DUR=duration))
        
        newData = {"Total Reference Heater Setpoint": self.valveParams.refHeatLoadDes,
                   "Total Reference Heater Readback": self.valveParams.refHeatLoadAct,
                   "JT Valve Position"              : self.valveParams.refValvePos,
                   "Start Time"                     : start_time.strftime("%m/%d/%y %H:%M:%S"),
                   "End Time"                       : end_time.strftime("%m/%d/%y %H:%M:%S"),
                   "Cavity Amplitudes"              : desiredAmplitudes,
                   "Calculated Adjusted Heat Load"  : self.q0_measurement.heat_load,
                   "Calculated Raw Heat Load"       : self.q0_measurement.raw_heat,
                   "Calculated Adjustment"          : self.q0_measurement.adjustment,
                   "Calculated Q0"                  : self.q0_measurement.q0}
        
        with open(self.q0_idx_file, 'r+') as f:
            data: List = json.load(f)
            data.append(newData)
            
            # go to the beginning of the file to overwrite the existing data structure
            f.seek(0)
            json.dump(data, f)
            f.truncate()
    
    def setup_for_q0(self, desiredAmplitudes, desired_ll, jt_search_end, jt_search_start):
        self.q0_measurement = q0_utils.Q0Measurement(heater_run_heatload=q0_utils.FULL_MODULE_CALIBRATION_LOAD,
                                                     amplitude=sum(desiredAmplitudes.values()),
                                                     calibration=self.calibration)
        camonitor(self.dsPressurePV, callback=self.fill_pressure_buffer)
        if not self.valveParams:
            self.valveParams = self.getRefValveParams(start_time=jt_search_start,
                                                      end_time=jt_search_end)
        print(f"setting heater to {self.valveParams.refHeatLoadDes}")
        caput(self.heater_setpoint_pv, self.valveParams.refHeatLoadDes, wait=True)
        self.fillAndLock(desired_ll)
    
    def takeNewCalibration(self, initial_heat_load: int,
                           jt_search_start: datetime = None,
                           jt_search_end: datetime = None,
                           desired_ll: float = q0_utils.MAX_DS_LL,
                           ll_drop: float = q0_utils.TARGET_LL_DIFF,
                           heater_delta: float = q0_utils.CAL_HEATER_DELTA,
                           num_cal_steps: int = q0_utils.NUM_CAL_STEPS):
        
        self.calibration.clear()
        
        if not self.valveParams:
            self.valveParams = self.getRefValveParams(start_time=jt_search_start,
                                                      end_time=jt_search_end)
        
        deltaTot = self.valveParams.refHeatLoadDes - caget(self.heater_readback_pv)
        
        startTime = datetime.now().replace(microsecond=0)
        
        # Lumping in the initial
        caput(self.heater_manual_pv, 1, wait=True)
        print(f"Changing heater by {deltaTot}")
        caput(self.heater_setpoint_pv, caget(self.heater_readback_pv) + deltaTot, wait=True)
        
        starting_ll_setpoint = caget(self.dsLiqLevSetpointPV)
        print(f"Starting liquid level setpoint: {starting_ll_setpoint}")
        
        self.fillAndLock(desired_ll)
        
        self.launchHeaterRun(initial_heat_load, target_ll_diff=ll_drop)
        self.current_data_run = None
        
        for _ in range(num_cal_steps):
            if (self.averagedLiquidLevelDS - q0_utils.MIN_DS_LL) < ll_drop:
                self.fillAndLock(desired_ll)
            self.launchHeaterRun(heater_delta, target_ll_diff=ll_drop)
            self.current_data_run = None
        
        self.calibration.save_data(timestamp=startTime, cm_name=self.name)
        
        endTime = datetime.now().replace(microsecond=0)
        
        print("\nStart Time: {START}".format(START=startTime))
        print("End Time: {END}".format(END=datetime.now()))
        
        duration = (datetime.now() - startTime).total_seconds() / 3600
        print("Duration in hours: {DUR}".format(DUR=duration))
        
        full_heater_delta = -((num_cal_steps * heater_delta) + initial_heat_load)
        print(f"Changing heater by {full_heater_delta}")
        caput(self.heater_setpoint_pv, caget(self.heater_readback_pv) + full_heater_delta, wait=True)
        
        print("Restoring initial cryo conditions")
        caput(self.jtAutoSelectPV, 1, wait=True)
        caput(self.dsLiqLevSetpointPV, starting_ll_setpoint, wait=True)
        caput(self.heater_sequencer_pv, 1, wait=True)
        
        # Save a record of this calibration
        
        newData = {"Total Reference Heater Setpoint": self.valveParams.refHeatLoadDes,
                   "Total Reference Heater Readback": self.valveParams.refHeatLoadAct,
                   "JT Valve Position"              : self.valveParams.refValvePos,
                   "Start Time"                     : startTime.strftime("%m/%d/%y %H:%M:%S"),
                   "End Time"                       : endTime.strftime("%m/%d/%y %H:%M:%S"),
                   "Number of Points"               : num_cal_steps,
                   "Initial Heat Load"              : initial_heat_load,
                   "Heater Delta"                   : heater_delta,
                   "Calculated Heat vs dll/dt Slope": self.calibration.dLLdt_dheat,
                   "Calculated Adjustment"          : self.calibration.adjustment}
        
        with open(self.calib_idx_file, 'r+') as f:
            data: List = json.load(f)
            data.append(newData)
            
            # go to the beginning of the file to overwrite the existing data structure
            f.seek(0)
            json.dump(data, f)
            f.truncate()
    
    def lock_jt(self, refValvePos):
        # type: (float) -> None
        
        print("Setting JT to manual and waiting for readback to change")
        caput(self.jtManualSelectPV, 1, wait=True)
        
        # One way for the JT valve to be locked in the correct position is for
        # it to be in manual mode and at the desired value
        while caget(self.jtModePV) != q0_utils.JT_MANUAL_MODE_VALUE:
            sleep(1)
        
        print(f"Waiting for JT Valve to be locked at {refValvePos}")
        while (caget(self.jtManPosSetpointPV) - refValvePos) > 0.01:
            sleep(1)
        
        print("Waiting for JT Valve position to be in tolerance")
        # Wait for the valve position to be within tolerance before continuing
        while abs(caget(self.jtValveReadbackPV) - refValvePos) > q0_utils.VALVE_POS_TOL:
            sleep(1)
        
        print("JT Valve locked.")
    
    def waitForLL(self, desiredLiquidLevel=q0_utils.MAX_DS_LL):
        print(f"Waiting for downstream liquid level to be {desiredLiquidLevel}%")
        
        while (desiredLiquidLevel - self.averagedLiquidLevelDS) > 0.01:
            print(f"Current averaged level is {self.averagedLiquidLevelDS}; waiting 5 seconds for more data.")
            sleep(5)
        
        print("downstream liquid level at required value.")


Q0_CRYOMODULES: Dict[str, Q0Cryomodule] = CryoDict(cryomoduleClass=Q0Cryomodule,
                                                   cavityClass=Q0Cavity)
