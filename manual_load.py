import os
from datetime import datetime

from q0_linac import Calibration, Q0Cryomodule, Q0_CRYOMODULES
from q0_utils import HeaterRun, ValveParams

cm: Q0Cryomodule = Q0_CRYOMODULES["03"]
cm.valveParams = ValveParams(refHeatLoadAct=47.7, refHeatLoadDes=48.0, refValvePos=32.3)
cal = Calibration(time_stamp="08/03/22 15:42:36", cryomodule=cm)

ref_heat = 47.7
date_formatter = "%m/%d/%y %H:%M:%S"

os.getcwd()

import lcls_tools.common.data_analysis.archiver as archiver

a = archiver.Archiver("lcls")

cal_start_time = "08/03/22 15:42:36"

run_times = [("08/03/22 15:42:36", "08/03/22 15:49:42"),
             ("08/03/22 15:54:53", "08/03/22 16:00:11"),
             ("08/03/22 16:06:01", "08/03/22 16:10:57"),
             ("08/03/22 16:16:16", "08/03/22 16:20:59"),
             ("08/03/22 16:26:09", "08/03/22 16:33:41")]

for (start_time, end_time) in run_times:
    heat_load_data = a.getValuesOverTimeRange(pvList=[cm.heater_readback_pv],
                                              startTime=datetime.strptime(start_time,
                                                                          date_formatter),
                                              endTime=datetime.strptime(end_time,
                                                                        date_formatter))
    heater_run = HeaterRun(heat_load=48)
    heater_run.start_time = datetime.strptime(start_time, date_formatter)
    heater_run.end_time = datetime.strptime(end_time, date_formatter)
    heater_run.reference_heat = 47.7
    heater_run.heater_readback_buffer = heat_load_data.values[cm.heater_readback_pv]
    ll_data = a.getValuesOverTimeRange(pvList=[cm.dsLevelPV],
                                       startTime=datetime.strptime(start_time,
                                                                   date_formatter),
                                       endTime=datetime.strptime(end_time,
                                                                 date_formatter))
    timestamps = ll_data.timeStamps[cm.dsLevelPV]
    values = ll_data.values[cm.dsLevelPV]
    
    for idx, value in enumerate(values):
        timestamp = timestamps[idx].timestamp()
        heater_run.ll_data[timestamp] = value
    
    cal.heater_runs.append(heater_run)

cal.save_data()
cal.save_results()
