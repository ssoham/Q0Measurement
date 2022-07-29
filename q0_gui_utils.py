import json
from datetime import datetime, timedelta
from functools import partial
from time import sleep
from typing import Dict

import numpy as np
from PyQt5.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (QButtonGroup, QCheckBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                             QMessageBox,
                             QProgressBar, QPushButton, QRadioButton, QVBoxLayout)
from epics import caget, camonitor, camonitor_clear, caput
from lcls_tools.common.pydm_tools.displayUtils import showDisplay
from lcls_tools.superconducting.scLinac import Cavity
from pydm import Display
from requests import ConnectTimeout
from urllib3.exceptions import ConnectTimeoutError

import q0_utils
from q0_linac import Q0Cavity, Q0Cryomodule, Q0_CRYOMODULES

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
            self.cryomodule.takeNewQ0Measurement(desiredAmplitudes=self.desired_amplitudes,
                                                 desired_ll=self.desired_ll,
                                                 ll_drop=self.ll_drop)
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


class CavityAmplitudeControls(QObject):
    def __init__(self, number, prefix, cryomodule_selector):
        super().__init__()
        self.number = number
        self.cryomodule_selector: CryomoduleSelector = cryomodule_selector
        self.groupbox: QGroupBox = QGroupBox()
        self.groupbox.setCheckable(True)
        self.groupbox.setTitle(f"Cavity {number}")
        
        horLayout: QHBoxLayout = QHBoxLayout()
        horLayout.addStretch()
        
        self.desAmpSpinbox: QDoubleSpinBox = QDoubleSpinBox()
        amax = caget(prefix + "ADES_MAX")
        self.desAmpSpinbox.setValue(min(16.6, amax))
        self.desAmpSpinbox.setRange(0, amax)
        horLayout.addWidget(self.desAmpSpinbox)
        horLayout.addWidget(QLabel("MV"))
        horLayout.addStretch()
        
        self.groupbox.setLayout(horLayout)


class CryomoduleSelector(QObject):
    def __init__(self, name: str, button_group: QButtonGroup, main_display):
        super().__init__()
        self.name: str = name
        self.main_display = main_display
        self.cm_display: Display = None
        
        self.cavity_amp_controls: Dict[int, CavityAmplitudeControls] = {}
        self.cav_amp_button: QPushButton = QPushButton(f"CM {name}")
        self.cav_amp_button.clicked.connect(self.open_cm_display)
        self.cav_amp_button.setEnabled(False)
        self.select_checkbox: QCheckBox = QCheckBox()
        self.select_checkbox.stateChanged.connect(self.cm_checked)
        
        hlayout: QHBoxLayout = QHBoxLayout()
        hlayout.addStretch()
        hlayout.addWidget(self.select_checkbox)
        hlayout.addWidget(self.cav_amp_button)
        hlayout.addStretch()
        
        self.groupbox: QGroupBox = QGroupBox()
        self.groupbox.setLayout(hlayout)
        button_group.addButton(self.select_checkbox)
    
    @pyqtSlot(int)
    def cm_checked(self, checkstate):
        if checkstate != Qt.Checked:
            self.cav_amp_button.setEnabled(False)
        else:
            self.cav_amp_button.setEnabled(True)
            if self.main_display.selectedCM:
                camonitor_clear(self.main_display.selectedCM.dsLevelPV)
            self.main_display.selectedCM = Q0_CRYOMODULES[self.name]
            camonitor(self.main_display.selectedCM.dsLevelPV,
                      callback=self.main_display.selectedCM.monitor_ll)
        self.main_display.updateSelectedText()
    
    def open_cm_display(self):
        if not self.cm_display:
            self.cm_display = Display()
            self.cm_display.setWindowTitle(f"CM {self.name} Q0 Measurement Amplitudes")
            vlayout: QVBoxLayout = QVBoxLayout()
            self.cm_display.setLayout(vlayout)
            groupbox: QGroupBox = QGroupBox()
            vlayout.addWidget(groupbox)
            
            grid_layout = QGridLayout()
            groupbox.setLayout(grid_layout)
            
            for cav_num in range(1, 9):
                row = int((cav_num - 1) / 4)
                column = (cav_num - 1) % 4
                controls = CavityAmplitudeControls(cav_num,
                                                   Q0_CRYOMODULES[self.name].cavities[cav_num].pvPrefix,
                                                   self)
                self.cavity_amp_controls[cav_num] = controls
                grid_layout.addWidget(controls.groupbox, row, column)
        
        showDisplay(self.cm_display)


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
        q0 = "{:e}".format(self.cryomodule.q0_measurement.q0)
        self.q0_loaded_signal.emit(f"Loaded q0 measurement for"
                                   f" CM{self.cryomodule.name} from {timestamp}"
                                   f" with q0 {q0}")


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
                                    f" with slope {self.cryomodule.calibration.dLLdt_dheat}")


class MeasurementSettings(QObject):
    
    def __init__(self, label: str):
        super().__init__()
        self.main_groupbox: QGroupBox = QGroupBox(label)
        main_vlayout: QVBoxLayout = QVBoxLayout()
        self.main_groupbox.setLayout(main_vlayout)
        self.main_groupbox.setEnabled(False)
        button_layout: QHBoxLayout = QHBoxLayout()
        self.new_button: QPushButton = QPushButton(f"Take New {label}")
        self.load_button: QPushButton = QPushButton(f"Load Existing {label}")
        self.data_button: QPushButton = QPushButton(f"Open {label} Data Analysis Dialog")
        self.status_label: QLabel = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignLeft)
        self.progress_bar: QProgressBar = QProgressBar()
        
        button_layout.addStretch()
        button_layout.addWidget(self.load_button)
        button_layout.addWidget(QLabel("- OR -"))
        button_layout.addWidget(self.new_button)
        button_layout.addStretch()
        
        main_vlayout.addLayout(button_layout)
        
        sub_groupbox: QGroupBox = QGroupBox()
        sub_vlayout: QVBoxLayout = QVBoxLayout()
        label_hlayout: QHBoxLayout = QHBoxLayout()
        sub_vlayout.addLayout(label_hlayout)
        label_hlayout.addWidget(QLabel(f"{label} Status: "))
        label_hlayout.addWidget(self.status_label)
        sub_vlayout.addWidget(self.progress_bar)
        sub_groupbox.setLayout(sub_vlayout)
        main_vlayout.addWidget(sub_groupbox)
        main_vlayout.addWidget(self.data_button)
    
    @pyqtSlot(str)
    def handle_error(self, message):
        self.status_label.setStyleSheet("color: red;")
        self.status_label.setText(message)
        make_error_popup("Error Taking New Calibration", message)
    
    @pyqtSlot(str)
    def handle_status(self, message):
        self.status_label.setStyleSheet("color: blue;")
        self.status_label.setText(message)
