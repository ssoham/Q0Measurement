from datetime import datetime

import lcls_tools.common.data_analysis.archiver as archiver
from q0_linac import Calibration, Q0Cryomodule, Q0Measurement, Q0_CRYOMODULES
from q0_utils import HeaterRun, ValveParams

a = archiver.Archiver("lcls")
strptime_formatter = "%m/%d/%y %H:%M:%S"


def get_q0_data(cm_name, cal_timestamp, heater_start: datetime,
                heater_end: datetime, rf_start: datetime, rf_end: datetime, cav_amps):
    cm: Q0Cryomodule = Q0_CRYOMODULES[cm_name]
    cm.load_calibration(cal_timestamp)
    
    q0_meas = Q0Measurement(cm)
    q0_meas.amplitudes = cav_amps
    q0_meas.start_time = rf_start
    q0_meas.heater_run_heatload = 48.0
    
    heater_run_data = a.getValuesOverTimeRange(pvList=[cm.dsLevelPV,
                                                       cm.heater_readback_pv],
                                               startTime=heater_start,
                                               endTime=heater_end)
    
    heater_timestamps = heater_run_data.timeStamps[cm.dsLevelPV]
    heater_values = heater_run_data.values[cm.dsLevelPV]
    
    for idx, value in enumerate(heater_values):
        timestamp = heater_timestamps[idx].timestamp()
        q0_meas.heater_run.ll_data[timestamp] = value
    
    q0_meas.heater_run.heater_readback_buffer = heater_run_data.values[cm.heater_readback_pv]
    
    rf_run_data = a.getValuesOverTimeRange(pvList=[cm.dsLevelPV,
                                                   cm.heater_readback_pv,
                                                   cm.dsPressurePV],
                                           startTime=rf_start,
                                           endTime=rf_end)
    
    rf_timestamps = rf_run_data.timeStamps[cm.dsLevelPV]
    rf_values = rf_run_data.values[cm.dsLevelPV]
    
    for idx, value in enumerate(rf_values):
        timestamp = rf_timestamps[idx].timestamp()
        q0_meas.rf_run.ll_data[timestamp] = value
    
    q0_meas.rf_run.heater_readback_buffer = rf_run_data.values[cm.heater_readback_pv]
    q0_meas.rf_run.pressure_buffer = rf_run_data.values[cm.dsPressurePV]
    q0_meas.save_data()
    q0_meas.save_results()


def get_cal_data():
    cm: Q0Cryomodule = Q0_CRYOMODULES["12"]
    cm.load_calibration("08/05/22 15:35:12")
    cm.valveParams = ValveParams(refHeatLoadAct=47.7, refHeatLoadDes=48.0, refValvePos=32.3)
    cal = Calibration(time_stamp="08/05/22 15:35:12", cryomodule=cm)
    
    ref_heat = 47.7
    cal_start_time = "08/03/22 15:42:36"
    
    run_times = [("08/03/22 15:42:36", "08/03/22 15:49:42"),
                 ("08/03/22 15:54:53", "08/03/22 16:00:11"),
                 ("08/03/22 16:06:01", "08/03/22 16:10:57"),
                 ("08/03/22 16:16:16", "08/03/22 16:20:59"),
                 ("08/03/22 16:26:09", "08/03/22 16:33:41")]
    
    for (start_time, end_time) in run_times:
        heat_load_data = a.getValuesOverTimeRange(pvList=[cm.heater_readback_pv],
                                                  startTime=datetime.strptime(start_time,
                                                                              strptime_formatter),
                                                  endTime=datetime.strptime(end_time,
                                                                            strptime_formatter))
        heater_run = HeaterRun(heat_load=48)
        heater_run.start_time = datetime.strptime(start_time, strptime_formatter)
        heater_run.end_time = datetime.strptime(end_time, strptime_formatter)
        heater_run.reference_heat = 47.7
        heater_run.heater_readback_buffer = heat_load_data.values[cm.heater_readback_pv]
        ll_data = a.getValuesOverTimeRange(pvList=[cm.dsLevelPV],
                                           startTime=datetime.strptime(start_time,
                                                                       strptime_formatter),
                                           endTime=datetime.strptime(end_time,
                                                                     strptime_formatter))
        timestamps = ll_data.timeStamps[cm.dsLevelPV]
        values = ll_data.values[cm.dsLevelPV]
        
        for idx, value in enumerate(values):
            timestamp = timestamps[idx].timestamp()
            heater_run.ll_data[timestamp] = value
        
        cal.heater_runs.append(heater_run)
    
    cal.save_data()
    cal.save_results()


if __name__ == "__main__":
    heater_start = datetime.strptime("08/05/22 20:33:20", strptime_formatter)
    heater_end = datetime.strptime("08/05/22 20:37:00", strptime_formatter)
    rf_start = datetime.strptime("08/05/22 19:45:10", strptime_formatter)
    rf_end = datetime.strptime("08/05/22 20:30:40", strptime_formatter)
    get_q0_data(cm_name="12", cal_timestamp="08/05/22 15:35:12",
                heater_start=heater_start, heater_end=heater_end,
                rf_start=rf_start, rf_end=rf_end, cav_amps={8: 10.0})