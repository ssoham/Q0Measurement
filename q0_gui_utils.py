import json
from datetime import datetime, timedelta
from functools import partial
from time import sleep
from typing import Dict

import numpy as np
from PyQt5.QtCore import QObject, QThread, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                             QMessageBox,
                             QRadioButton)
from epics import caget, caput
from pydm.widgets import PyDMLabel
from requests import ConnectTimeout
from urllib3.exceptions import ConnectTimeoutError

import q0_utils
from lcls_tools.superconducting.scLinac import Cavity
from q0_linac import Q0Cavity, Q0Cryomodule

DEFAULT_LL_DROP = 4
MIN_STARTING_LL = 93
DEFAULT_START_HEAT = 40
DEFAULT_END_HEAT = 112
DEFAULT_NUM_CAL_POINTS = 5
DEFAULT_POST_RF_HEAT = 24
DEFAULT_JT_START_DELTA = timedelta(hours=24)
DEFAULT_LL_BUFFER_SIZE = 10


class Worker(QThread):
    finished = pyqtSignal(str)
    progress = pyqtSignal(int)
    error = pyqtSignal(str)
    status = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.finished.connect(print)
        self.progress.connect(print)
        self.error.connect(print)
        self.status.connect(print)
    
    def terminate(self) -> None:
        self.error.emit("Thread termination requested")
        super().terminate()


class CryoParamSetupWorker(Worker):
    def __init__(self, cryomodule: Q0Cryomodule):
        super().__init__()
        self.cryomodule = cryomodule
    
    def run(self) -> None:
        self.status.emit("Checking for required cryo permissions")
        if caget(self.cryomodule.cryo_access_pv) != q0_utils.CRYO_ACCESS_VALUE:
            self.error.emit("Required cryo permissions not granted - call cryo ops")
            return
        
        caput(self.cryomodule.heater_manual_pv, 1, wait=True)
        sleep(3)
        caput(self.cryomodule.heater_setpoint_pv, q0_utils.MINIMUM_HEATLOAD)
        caput(self.cryomodule.jtAutoSelectPV, 1, wait=True)
        caput(self.cryomodule.dsLiqLevSetpointPV, 93, wait=True)
        self.finished.emit("Cryo setup for new reference parameters in ~3 hours")


class CryoParamWorker(Worker):
    
    def __init__(self, cryomodule: Q0Cryomodule, start_time: datetime,
                 end_time: datetime):
        super().__init__()
        self.cryomodule: Q0Cryomodule = cryomodule
        self.start_time: datetime = start_time
        self.end_time: datetime = end_time
    
    def run(self) -> None:
        self.status.emit("Getting new reference cryo parameters")
        self.cryomodule.getRefValveParams(start_time=self.start_time,
                                          end_time=self.end_time)
        self.finished.emit("New reference cryo params loaded")


class RFWorker(Worker):
    
    def __init__(self, cryomodule: Q0Cryomodule,
                 jt_search_start: datetime, jt_search_end: datetime,
                 desired_ll, ll_drop, desired_amplitudes):
        super().__init__()
        self.cryomodule = cryomodule
        self.jt_search_end = jt_search_end
        self.jt_search_start = jt_search_start
        self.desired_ll = desired_ll
        self.ll_drop = ll_drop
        self.desired_amplitudes = desired_amplitudes


class Q0Worker(RFWorker):
    
    def run(self) -> None:
        if caget(self.cryomodule.cryo_access_pv) != q0_utils.CRYO_ACCESS_VALUE:
            self.error.emit("Required cryo permissions not granted - call cryo ops")
            return
        
        try:
            self.status.emit("Taking new Q0 Measurement")
            self.cryomodule.takeNewQ0Measurement(desiredAmplitudes=self.desired_amplitudes,
                                                 desired_ll=self.desired_ll,
                                                 ll_drop=self.ll_drop)
            self.finished.emit(f"Recorded Q0: {self.cryomodule.q0_measurement.q0:.2e}")
        except TypeError as e:
            self.error.emit(str(e))


class Q0SetupWorker(RFWorker):
    
    def run(self) -> None:
        if caget(self.cryomodule.cryo_access_pv) != q0_utils.CRYO_ACCESS_VALUE:
            self.error.emit("Required cryo permissions not granted - call cryo ops")
            return
        self.cryomodule.setup_for_q0(desiredAmplitudes=self.desired_amplitudes,
                                     desired_ll=self.desired_ll,
                                     jt_search_start=self.jt_search_start,
                                     jt_search_end=self.jt_search_end)
        self.finished.emit(f"CM{self.cryomodule.name} ready for cavity ramp up")


class CavityRampWorker(Worker):
    def __init__(self, cavity: Cavity, des_amp: float):
        super().__init__()
        self.cavity: Q0Cavity = cavity
        self.des_amp = des_amp
    
    def run(self) -> None:
        self.status.emit(f"Ramping Cavity {self.cavity.number} to {self.des_amp}")
        self.cavity.setup_SELA(self.des_amp)
        self.finished.emit(f"Cavity {self.cavity.number} ramped up to {self.des_amp}")


class CalibrationWorker(Worker):
    
    def __init__(self, cryomodule: Q0Cryomodule, start_heat: float,
                 jt_search_start: datetime, jt_search_end: datetime,
                 desired_ll, heater_delta, num_cal_steps, ll_drop):
        super().__init__()
        self.cryomodule = cryomodule
        self.jt_search_end = jt_search_end
        self.jt_search_start = jt_search_start
        self.start_heat = start_heat
        self.desired_ll = desired_ll
        self.heater_delta = heater_delta
        self.num_cal_steps = num_cal_steps
        self.ll_drop = ll_drop
    
    def run(self) -> None:
        if caget(self.cryomodule.cryo_access_pv) != q0_utils.CRYO_ACCESS_VALUE:
            self.error.emit("Required cryo permissions not granted - call cryo ops")
            return
        try:
            self.status.emit("Taking new calibration")
            self.cryomodule.takeNewCalibration(initial_heat_load=self.start_heat,
                                               jt_search_start=self.jt_search_start,
                                               jt_search_end=self.jt_search_end,
                                               desired_ll=self.desired_ll,
                                               heater_delta=self.heater_delta,
                                               num_cal_steps=self.num_cal_steps,
                                               ll_drop=self.ll_drop)
            self.finished.emit("Calibration Loaded")
        except (ConnectTimeoutError, ConnectTimeout, q0_utils.CryoError) as e:
            self.error.emit(str(e))


def make_error_popup(title, message: str):
    popup = QMessageBox()
    popup.setIcon(QMessageBox.Critical)
    popup.setWindowTitle(title)
    popup.setText(message)
    popup.exec()


class CavAmpControl:
    def __init__(self):
        self.groupbox: QGroupBox = QGroupBox()
        self.groupbox.setCheckable(True)
        horLayout: QHBoxLayout = QHBoxLayout()
        horLayout.addStretch()
        
        self.desAmpSpinbox: QDoubleSpinBox = QDoubleSpinBox()
        
        horLayout.addWidget(self.desAmpSpinbox)
        horLayout.addWidget(QLabel("MV"))
        self.aact_label: PyDMLabel = PyDMLabel()
        self.aact_label.showUnits = True
        self.aact_label.alarmSensitiveContent = True
        self.aact_label.alarmSensitiveBorder = True
        horLayout.addWidget(self.aact_label)
        horLayout.addStretch()
        
        self.groupbox.setLayout(horLayout)
    
    def connect(self, cavity: Q0Cavity):
        self.groupbox.setTitle(f"Cavity {cavity.number}")
        amax = caget(cavity.ades_max_PV.pvname)
        self.desAmpSpinbox.setValue(min(16.6, amax))
        self.desAmpSpinbox.setRange(0, amax)
        self.aact_label.channel = cavity.selAmplitudeActPV.pvname


class Q0Options(QObject):
    q0_loaded_signal = pyqtSignal(str)
    
    def __init__(self, cryomodule: Q0Cryomodule):
        super().__init__()
        self.cryomodule = cryomodule
        self.main_groupbox: QGroupBox = QGroupBox(f"Q0 Measurements for CM{cryomodule.name}")
        grid_layout: QGridLayout = QGridLayout()
        self.main_groupbox.setLayout(grid_layout)
        
        with open(cryomodule.q0_idx_file, "r+") as f:
            q0_measurements: Dict = json.load(f)
            col_count = get_dimensions(q0_measurements)
            for idx, time_stamp in enumerate(q0_measurements.keys()):
                cav_amps = q0_measurements[time_stamp]["Cavity Amplitudes"]
                radio_button: QRadioButton = QRadioButton(f"{time_stamp}: {cav_amps}")
                grid_layout.addWidget(radio_button, int(idx / col_count),
                                      idx % col_count)
                radio_button.clicked.connect(partial(self.load_q0,
                                                     time_stamp))
    
    @pyqtSlot()
    def load_q0(self, timestamp: str):
        self.cryomodule.load_q0_measurement(time_stamp=timestamp)
        self.q0_loaded_signal.emit(f"Loaded q0 measurement for"
                                   f" CM{self.cryomodule.name} from {timestamp}"
                                   f" with q0 {self.cryomodule.q0_measurement.q0:.2e}")


def get_dimensions(options):
    num_options = len(options.keys())
    row_count = int(np.sqrt(num_options))
    col_count = int(np.ceil(np.sqrt(num_options)))
    if row_count * col_count != num_options:
        col_count += 1
    return col_count


class CalibrationOptions(QObject):
    cal_loaded_signal = pyqtSignal(str)
    
    def __init__(self, cryomodule: Q0Cryomodule):
        super().__init__()
        self.cryomodule = cryomodule
        self.main_groupbox: QGroupBox = QGroupBox(f"Calibrations for CM{cryomodule.name}")
        grid_layout: QGridLayout = QGridLayout()
        self.main_groupbox.setLayout(grid_layout)
        
        with open(cryomodule.calib_idx_file, 'r+') as f:
            calibrations: Dict = json.load(f)
            col_count = get_dimensions(calibrations)
            
            for idx, time_stamp in enumerate(calibrations.keys()):
                radio_button: QRadioButton = QRadioButton(time_stamp)
                grid_layout.addWidget(radio_button, int(idx / col_count),
                                      idx % col_count)
                radio_button.clicked.connect(partial(self.load_calibration,
                                                     time_stamp))
    
    @pyqtSlot()
    def load_calibration(self, timestamp: str):
        self.cryomodule.load_calibration(time_stamp=timestamp)
        self.cal_loaded_signal.emit(f"Loaded calibration for"
                                    f" CM{self.cryomodule.name} from {timestamp}"
                                    f" with slope {self.cryomodule.calibration.dLLdt_dheat:.2e}")