import json
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


class Calibration:
    def __init__(self, time_stamp, cryomodule):
        # type: (str, Q0Cryomodule) -> None
        
        self.time_stamp: str = time_stamp
        self.cryomodule: Q0Cryomodule = cryomodule
        
        self.heater_runs: List[q0_utils.HeaterRun] = []
        self._slope = None
        self.adjustment = 0
    
    def load_data(self):
        
        self.heater_runs: List[q0_utils.HeaterRun] = []
        
        with open(self.cryomodule.calib_data_file, 'r+') as f:
            all_data: Dict = json.load(f)
            data: Dict = all_data[self.time_stamp]
            
            for heater_run_data in data.values():
                run = q0_utils.HeaterRun(heater_run_data["Desired Heat Load"])
                run._start_time = datetime.strptime(heater_run_data[q0_utils.JSON_START_KEY],
                                                    q0_utils.DATETIME_FORMATTER)
                run._end_time = datetime.strptime(heater_run_data[q0_utils.JSON_END_KEY],
                                                  q0_utils.DATETIME_FORMATTER)
                run.ll_data = heater_run_data[q0_utils.JSON_LL_KEY]
                run.average_heat = heater_run_data[q0_utils.JSON_HEATER_READBACK_KEY]
                run.dll_dt = heater_run_data[q0_utils.JSON_DLL_KEY]
                
                self.heater_runs.append(run)
    
    def save_data(self):
        
        new_data = {}
        for idx, heater_run in enumerate(self.heater_runs):
            key = heater_run.start_time
            heater_data = {q0_utils.JSON_START_KEY          : heater_run.start_time,
                           q0_utils.JSON_END_KEY            : heater_run.end_time,
                           "Desired Heat Load"              : heater_run.heat_load_des,
                           q0_utils.JSON_HEATER_READBACK_KEY: heater_run.average_heat,
                           q0_utils.JSON_DLL_KEY            : heater_run.dll_dt,
                           q0_utils.JSON_LL_KEY             : heater_run.ll_data}
            
            new_data[key] = heater_data
        
        q0_utils.update_json_data(self.cryomodule.calib_data_file, self.time_stamp, new_data)
    
    def save_results(self):
        newData = {q0_utils.JSON_START_KEY          : self.time_stamp,
                   "Calculated Heat vs dll/dt Slope": self.dLLdt_dheat,
                   "Calculated Adjustment"          : self.adjustment,
                   "Total Reference Heater Setpoint": self.cryomodule.valveParams.refHeatLoadDes,
                   "Total Reference Heater Readback": self.cryomodule.valveParams.refHeatLoadAct,
                   "JT Valve Position"              : self.cryomodule.valveParams.refValvePos}
        q0_utils.update_json_data(self.cryomodule.calib_idx_file,
                                  self.time_stamp, newData)
    
    @property
    def dLLdt_dheat(self):
        if not self._slope:
            heat_loads = []
            dll_dts = []
            for run in self.heater_runs:
                heat_loads.append(run.average_heat)
                dll_dts.append(run.dll_dt)
            
            slope, intercept, r_val, p_val, std_err = linregress(
                    heat_loads, dll_dts)
            
            if np.isnan(slope):
                self._slope = None
            else:
                self.adjustment = intercept
                self._slope = slope
        
        return self._slope
    
    def get_heat(self, dll_dt: float):
        return (dll_dt - self.adjustment) / self.dLLdt_dheat


class RFRun(q0_utils.DataRun):
    def __init__(self, amplitudes: Dict[int, float]):
        super().__init__()
        self.amplitudes = amplitudes
        self.pressure_buffer = []
        self._avg_pressure = None
    
    @property
    def avg_pressure(self):
        if not self._avg_pressure:
            self._avg_pressure = np.mean(self.pressure_buffer)
        return self._avg_pressure
    
    @avg_pressure.setter
    def avg_pressure(self, value: float):
        self._avg_pressure = value


class Q0Measurement:
    def __init__(self, cryomodule):
        # type: (Q0Cryomodule) -> None
        self.cryomodule: Q0Cryomodule = cryomodule
        self.heater_run: q0_utils.HeaterRun = None
        self.rf_run: RFRun = None
        self._raw_heat: float = None
        self._adjustment: float = None
        self._heat_load: float = None
        self._q0: float = None
        self._start_time: str = None
        self._amplitudes: Dict[int, float] = None
        self._heater_run_heatload: float = None
    
    @property
    def amplitudes(self):
        return self._amplitudes
    
    @amplitudes.setter
    def amplitudes(self, amplitudes: Dict[int, float]):
        self._amplitudes = amplitudes
        self.rf_run = RFRun(amplitudes)
    
    @property
    def heater_run_heatload(self):
        return self._heater_run_heatload
    
    @heater_run_heatload.setter
    def heater_run_heatload(self, heat_load: float):
        self._heater_run_heatload = heat_load
        self.heater_run = q0_utils.HeaterRun(heat_load)
    
    @property
    def start_time(self):
        return self._start_time
    
    @start_time.setter
    def start_time(self, start_time: datetime):
        if not self._start_time:
            self._start_time = start_time.strftime(q0_utils.DATETIME_FORMATTER)
    
    def load_data(self, time_stamp: str):
        # TODO need to load the other parameters
        self.start_time = datetime.strptime(time_stamp, q0_utils.DATETIME_FORMATTER)
        
        with open(self.cryomodule.q0_data_file, 'r+') as f:
            all_data: Dict = json.load(f)
            q0_meas_data: Dict = all_data[time_stamp]
            
            heater_run_data: Dict = q0_meas_data[q0_utils.JSON_HEATER_RUN_KEY]
            
            self.heater_run_heatload = heater_run_data[q0_utils.JSON_HEATER_READBACK_KEY]
            self.heater_run.average_heat = heater_run_data[q0_utils.JSON_HEATER_READBACK_KEY]
            self.heater_run.start_time = datetime.strptime(heater_run_data[q0_utils.JSON_START_KEY],
                                                           q0_utils.DATETIME_FORMATTER)
            self.heater_run.end_time = datetime.strptime(heater_run_data[q0_utils.JSON_END_KEY],
                                                         q0_utils.DATETIME_FORMATTER)
            ll_data = {}
            for time_str, val in heater_run_data[q0_utils.JSON_LL_KEY].items():
                ll_data[float(time_str)] = val
            self.heater_run.ll_data = ll_data
            
            rf_run_data: Dict = q0_meas_data[q0_utils.JSON_RF_RUN_KEY]
            cav_amps = {}
            for cav_num_str, amp in rf_run_data[q0_utils.JSON_CAV_AMPS_KEY].items():
                cav_amps[int(cav_num_str)] = amp
            
            self.amplitudes = cav_amps
            self.rf_run.start_time = datetime.strptime(rf_run_data[q0_utils.JSON_START_KEY],
                                                       q0_utils.DATETIME_FORMATTER)
            self.rf_run.end_time = datetime.strptime(rf_run_data[q0_utils.JSON_END_KEY],
                                                     q0_utils.DATETIME_FORMATTER)
            
            ll_data = {}
            for time_str, val in rf_run_data[q0_utils.JSON_LL_KEY].items():
                ll_data[float(time_str)] = val
            self.rf_run.ll_data = ll_data
            
            self.rf_run.avg_pressure = rf_run_data[q0_utils.JSON_AVG_PRESS_KEY]
    
    def save_data(self):
        q0_utils.make_json_file(self.cryomodule.q0_data_file)
        heater_data = {q0_utils.JSON_START_KEY          : self.heater_run.start_time,
                       q0_utils.JSON_END_KEY            : self.heater_run.end_time,
                       q0_utils.JSON_LL_KEY             : self.heater_run.ll_data,
                       q0_utils.JSON_HEATER_READBACK_KEY: self.heater_run.average_heat,
                       q0_utils.JSON_DLL_KEY            : self.heater_run.dll_dt}
        
        rf_data = {q0_utils.JSON_START_KEY          : self.rf_run.start_time,
                   q0_utils.JSON_END_KEY            : self.rf_run.end_time,
                   q0_utils.JSON_LL_KEY             : self.rf_run.ll_data,
                   q0_utils.JSON_HEATER_READBACK_KEY: self.rf_run.average_heat,
                   q0_utils.JSON_AVG_PRESS_KEY      : self.rf_run.avg_pressure,
                   q0_utils.JSON_DLL_KEY            : self.rf_run.dll_dt,
                   q0_utils.JSON_CAV_AMPS_KEY       : self.rf_run.amplitudes}
        
        new_data = {q0_utils.JSON_HEATER_RUN_KEY: heater_data,
                    q0_utils.JSON_RF_RUN_KEY    : rf_data}
        
        q0_utils.update_json_data(self.cryomodule.q0_data_file, self.start_time,
                                  new_data)
    
    def save_results(self):
        newData = {q0_utils.JSON_START_KEY        : self.start_time,
                   q0_utils.JSON_CAV_AMPS_KEY     : self.rf_run.amplitudes,
                   "Calculated Adjusted Heat Load": self.heat_load,
                   "Calculated Raw Heat Load"     : self.raw_heat,
                   "Calculated Adjustment"        : self.adjustment,
                   "Calculated Q0"                : self.q0,
                   "Calibration Used"             : self.cryomodule.calibration.time_stamp}
        
        q0_utils.update_json_data(self.cryomodule.q0_idx_file, self.start_time,
                                  newData)
    
    @property
    def raw_heat(self):
        if not self._raw_heat:
            self._raw_heat = self.cryomodule.calibration.get_heat(self.rf_run.dll_dt)
        return self._raw_heat
    
    @property
    def adjustment(self):
        if not self._adjustment:
            heater_run_raw_heat = self.cryomodule.calibration.get_heat(self.heater_run.dll_dt)
            self._adjustment = self.heater_run.average_heat - heater_run_raw_heat
        return self._adjustment
    
    @property
    def heat_load(self):
        if not self._heat_load:
            self._heat_load = self.raw_heat + self.adjustment
        return self._heat_load
    
    @property
    def q0(self):
        if not self._q0:
            cav_length = self.cryomodule.cavities[1].length
            grads = [amp / cav_length for amp in self.rf_run.amplitudes.values()]
            sum_square_grad = 0
            
            for grad in grads:
                sum_square_grad += grad ** 2
            
            effective_gradient = np.sqrt(sum_square_grad)
            
            self._q0 = q0_utils.calcQ0(gradient=effective_gradient,
                                       rfHeatLoad=self.heat_load,
                                       avgPressure=self.rf_run.avg_pressure)
        return self._q0


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
        
        self.q0_measurements: Dict[str, Q0Measurement] = {}
        self.calibrations: Dict[str, Calibration] = {}
        
        self.valveParams: q0_utils.ValveParams = None
        
        self._calib_idx_file = ("calibrations/cm{CM}.json"
                                .format(CM=self.name))
        self._calib_data_file = f"data/calibrations/cm{self.name}.json"
        self._q0_idx_file = ("q0_measurements/cm{CM}.json"
                             .format(CM=self.name))
        self._q0_data_file = f"data/q0_measurements/cm{self.name}.json"
        
        self.ll_buffer: np.array = np.empty(q0_utils.NUM_LL_POINTS_TO_AVG)
        self.ll_buffer[:] = np.nan
        self._ll_buffer_size = q0_utils.NUM_LL_POINTS_TO_AVG
        self.ll_buffer_idx = 0
        
        self.measurement_buffer = []
        self.calibration: Calibration = None
        self.q0_measurement: Q0Measurement = None
        self.current_data_run: q0_utils.DataRun = None
        self.cavity_amplitudes = {}
    
    @property
    def calib_data_file(self):
        if not isfile(self._calib_data_file):
            q0_utils.make_json_file(self._calib_data_file)
        return self._calib_data_file
    
    @property
    def q0_data_file(self):
        if not isfile(self._q0_data_file):
            q0_utils.make_json_file(self._q0_data_file)
        return self._q0_data_file
    
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
            self.current_data_run.ll_data[datetime.now().timestamp()] = value
    
    @property
    def averaged_liquid_level(self) -> float:
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
            q0_utils.make_json_file(self._q0_idx_file)
        
        return self._q0_idx_file
    
    @property
    def calib_idx_file(self) -> str:
        
        if not isfile(self._calib_idx_file):
            q0_utils.make_json_file(self._calib_idx_file)
        
        return self._calib_idx_file
    
    def fillAndLock(self, desiredLevel=q0_utils.MAX_DS_LL):
        
        starting_heat = caget(self.heater_setpoint_pv)
        
        print("Setting heaters to 0 to assist fill")
        caput(self.heater_setpoint_pv, 0, wait=True)
        
        caput(self.dsLiqLevSetpointPV, desiredLevel, wait=True)
        
        print(f"Setting JT to auto for refill to {desiredLevel}")
        caput(self.jtAutoSelectPV, 1, wait=True)
        self.waitForLL(desiredLevel)
        
        caput(self.jtManPosSetpointPV, self.valveParams.refValvePos, wait=True)
        
        print(f"Setting heat back to {starting_heat}")
        caput(self.heater_setpoint_pv, starting_heat, wait=True)
        
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
        
        new_val = caget(self.heater_readback_pv) + delta
        caput(self.heater_setpoint_pv, new_val, wait=True)
        
        print(q0_utils.RUN_STATUS_MSSG)
        
        self.current_data_run: q0_utils.HeaterRun = q0_utils.HeaterRun(new_val)
        self.calibration.heater_runs.append(self.current_data_run)
        
        self.current_data_run.start_time = datetime.now()
        
        camonitor(self.heater_readback_pv, callback=self.fill_heater_readback_buffer)
        self.wait_for_ll_drop(target_ll_diff)
        camonitor_clear(self.heater_readback_pv)
        
        self.current_data_run.end_time = datetime.now()
        
        print("Heater run done")
    
    def wait_for_ll_drop(self, target_ll_diff):
        startingLevel = self.averaged_liquid_level
        avgLevel = startingLevel
        while ((startingLevel - avgLevel) < target_ll_diff
               and (avgLevel > q0_utils.MIN_DS_LL)):
            print(f"Averaged level is {avgLevel}; waiting 10s")
            avgLevel = self.averaged_liquid_level
            sleep(10)
    
    def fill_pressure_buffer(self, value, **kwargs):
        if self.q0_measurement:
            self.q0_measurement.rf_run.pressure_buffer.append(value)
    
    def fill_heater_readback_buffer(self, value, **kwargs):
        if self.current_data_run:
            self.current_data_run.heater_readback_buffer.append(value)
    
    # to be called after setup_for_q0 and each cavity's setup_SELA
    def takeNewQ0Measurement(self, desiredAmplitudes: Dict[int, float],
                             desired_ll: float = q0_utils.MAX_DS_LL,
                             ll_drop: float = q0_utils.TARGET_LL_DIFF):
        
        for cav_num in desiredAmplitudes.keys():
            while not self.cavities[cav_num].ready_for_q0:
                print(f"Waiting for cavity {cav_num} to be ready")
                sleep(5)
        
        self.current_data_run: RFRun = self.q0_measurement.rf_run
        camonitor(self.heater_readback_pv, callback=self.fill_heater_readback_buffer)
        
        start_time = datetime.now()
        self.q0_measurement.start_time = start_time
        self.current_data_run.start_time = start_time
        
        self.wait_for_ll_drop(ll_drop)
        camonitor_clear(self.heater_readback_pv)
        self.current_data_run.end_time = datetime.now()
        
        self.current_data_run = None
        
        for cav_num in desiredAmplitudes.keys():
            self.cavities[cav_num].turnOff()
        
        self.fillAndLock(desired_ll)
        self.launchHeaterRun(q0_utils.FULL_MODULE_CALIBRATION_LOAD,
                             target_ll_diff=ll_drop)
        self.q0_measurement.heater_run = self.current_data_run
        
        camonitor_clear(self.dsPressurePV)
        self.q0_measurement.save_data()
        self.current_data_run = None
        
        end_time = datetime.now()
        caput(self.heater_setpoint_pv,
              caget(self.heater_readback_pv) - q0_utils.FULL_MODULE_CALIBRATION_LOAD)
        
        print("\nStart Time: {START}".format(START=start_time))
        print("End Time: {END}".format(END=end_time))
        
        duration = (end_time - start_time).total_seconds() / 3600
        print("Duration in hours: {DUR}".format(DUR=duration))
        
        print("Caluclated Q0: ", self.q0_measurement.q0)
        self.q0_measurement.save_results()
    
    def setup_for_q0(self, desiredAmplitudes, desired_ll, jt_search_end, jt_search_start):
        self.q0_measurement = Q0Measurement(cryomodule=self)
        self.q0_measurement.amplitudes = desiredAmplitudes
        self.q0_measurement.heater_run_heatload = q0_utils.FULL_MODULE_CALIBRATION_LOAD
        
        if not self.valveParams:
            self.valveParams = self.getRefValveParams(start_time=jt_search_start,
                                                      end_time=jt_search_end)
        print(f"setting heater to {self.valveParams.refHeatLoadDes}")
        
        caput(self.heater_setpoint_pv, self.valveParams.refHeatLoadDes, wait=True)
        self.fillAndLock(desired_ll)
        camonitor(self.dsPressurePV, callback=self.fill_pressure_buffer)
    
    def load_calibration(self, time_stamp: str):
        self.calibration: Calibration = Calibration(time_stamp=time_stamp,
                                                    cryomodule=self)
        self.calibration.load_data()
    
    def load_q0_measurement(self, time_stamp):
        self.q0_measurement: Q0Measurement = Q0Measurement(self)
        self.q0_measurement.load_data(time_stamp)
    
    def takeNewCalibration(self, initial_heat_load: int,
                           jt_search_start: datetime = None,
                           jt_search_end: datetime = None,
                           desired_ll: float = q0_utils.MAX_DS_LL,
                           ll_drop: float = q0_utils.TARGET_LL_DIFF,
                           heater_delta: float = q0_utils.CAL_HEATER_DELTA,
                           num_cal_steps: int = q0_utils.NUM_CAL_STEPS):
        
        if not self.valveParams:
            self.valveParams = self.getRefValveParams(start_time=jt_search_start,
                                                      end_time=jt_search_end)
        
        deltaTot = self.valveParams.refHeatLoadDes - caget(self.heater_readback_pv)
        
        startTime = datetime.now().replace(microsecond=0)
        self.calibration = Calibration(time_stamp=startTime.strftime(q0_utils.DATETIME_FORMATTER),
                                       cryomodule=self)
        
        caput(self.heater_manual_pv, 1, wait=True)
        print(f"Changing heater by {deltaTot}")
        caput(self.heater_setpoint_pv, caget(self.heater_readback_pv) + deltaTot, wait=True)
        
        starting_ll_setpoint = caget(self.dsLiqLevSetpointPV)
        print(f"Starting liquid level setpoint: {starting_ll_setpoint}")
        
        self.fillAndLock(desired_ll)
        
        self.launchHeaterRun(initial_heat_load, target_ll_diff=ll_drop)
        self.current_data_run = None
        
        for _ in range(num_cal_steps):
            if (self.averaged_liquid_level - q0_utils.MIN_DS_LL) < ll_drop:
                self.fillAndLock(desired_ll)
            self.launchHeaterRun(heater_delta, target_ll_diff=ll_drop)
            self.current_data_run = None
        
        self.calibration.save_data()
        
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
        
        self.calibration.save_results()
    
    def lock_jt(self, refValvePos):
        # type: (float) -> None
        
        print("Setting JT to manual and waiting for readback to change")
        caput(self.jtManualSelectPV, 1, wait=True)
        
        # One way for the JT valve to be locked in the correct position is for
        # it to be in manual mode and at the desired value
        while caget(self.jtModePV) != q0_utils.JT_MANUAL_MODE_VALUE:
            sleep(1)
        
        print(f"Waiting for JT Valve to be locked at {refValvePos}")
        caput(self.jtManPosSetpointPV, refValvePos, wait=True)
        while (caget(self.jtManPosSetpointPV) - refValvePos) > 0.01:
            sleep(1)
        
        print("Waiting for JT Valve position to be in tolerance")
        # Wait for the valve position to be within tolerance before continuing
        while abs(caget(self.jtValveReadbackPV) - refValvePos) > q0_utils.VALVE_POS_TOL:
            sleep(1)
        
        print("JT Valve locked.")
    
    def waitForLL(self, desiredLiquidLevel=q0_utils.MAX_DS_LL):
        print(f"Waiting for downstream liquid level to be {desiredLiquidLevel}%")
        
        while (desiredLiquidLevel - self.averaged_liquid_level) > 0.01:
            print(f"Current averaged level is {self.averaged_liquid_level}; waiting 10 seconds for more data.")
            sleep(10)
        
        print("downstream liquid level at required value.")


Q0_CRYOMODULES: Dict[str, Q0Cryomodule] = CryoDict(cryomoduleClass=Q0Cryomodule,
                                                   cavityClass=Q0Cavity)
