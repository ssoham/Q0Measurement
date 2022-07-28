from datetime import datetime
from functools import partial
from typing import Dict, Optional

from PyQt5.QtCore import pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (QButtonGroup, QDateTimeEdit, QDoubleSpinBox, QGridLayout, QGroupBox,
                             QHBoxLayout, QLabel, QSpinBox, QTabWidget, QVBoxLayout, QWidget)
from lcls_tools.common.pydm_tools.displayUtils import showDisplay
from lcls_tools.superconducting.scLinac import L1BHL, LINAC_TUPLES
from pydm import Display
from pyqtgraph import PlotWidget, plot

import q0_gui_utils
from q0_gui_utils import (CalibrationWorker, CryoParamWorker, CryomoduleSelector,
                          DEFAULT_END_HEAT, DEFAULT_JT_START_DELTA,
                          DEFAULT_LL_BUFFER_SIZE,
                          DEFAULT_LL_DROP,
                          DEFAULT_NUM_CAL_POINTS,
                          DEFAULT_START_HEAT,
                          MIN_STARTING_LL, MeasurementSettings)
from q0_linac import Q0Cryomodule
from q0_utils import ValveParams


class Q0GUI(Display):
    calibration_error_signal = pyqtSignal(str)
    calibration_status_signal = pyqtSignal(str)
    
    def ui_filename(self):
        return "q0.ui"
    
    def __init__(self, parent=None, args=None):
        super().__init__(parent=parent, args=args)
        
        self.selectedCM: Optional[Q0Cryomodule] = None
        
        self.button_group: QButtonGroup = QButtonGroup()
        
        # Note: if we don't save these, they get garbage collected
        self.cm_selectors: Dict[str, CryomoduleSelector] = {}
        
        self.cm_selector_window: Display = None
        self.ui.cmSelectButton.clicked.connect(self.open_cm_selector_window)
        
        self.settings_window: Display = None
        self.ui.settingsButton.clicked.connect(self.open_settings_window)
        
        self.num_cal_points_spinbox: QSpinBox = QSpinBox()
        self.end_heat_spinbox: QDoubleSpinBox = QDoubleSpinBox()
        self.start_heat_spinbox: QDoubleSpinBox = QDoubleSpinBox()
        self.min_start_ll_spinbox: QDoubleSpinBox = QDoubleSpinBox()
        self.ll_drop_spinbox: QDoubleSpinBox = QDoubleSpinBox()
        self.jt_search_start_edit: QDateTimeEdit = QDateTimeEdit()
        self.jt_search_end_edit: QDateTimeEdit = QDateTimeEdit()
        self.ll_buffer_spinbox: QSpinBox = QSpinBox()
        self.ll_buffer_spinbox.valueChanged.connect(self.update_ll_buffer)
        self.setup_settings()
        
        self.calibrationSection: MeasurementSettings = MeasurementSettings("Calibration")
        self.calibrationSection.new_button.clicked.connect(self.takeNewCalibration)
        self.calibrationSection.load_button.clicked.connect(self.load_calibration)
        self.cal_option_windows: Dict[str, Display] = {}
        self.calibrationSection.data_button.clicked.connect(self.show_calibration_data)
        
        self.rfSection: MeasurementSettings = MeasurementSettings("RF Measurement")
        self.rfSection.new_button.clicked.connect(self.takeNewQ0Measurement)
        self.rfSection.load_button.clicked.connect(self.load_q0)
        self.rf_option_windows: Dict[str, Display] = {}
        self.rfSection.data_button.clicked.connect(self.show_q0_data)
        
        self.ui.groupbox_layout.addWidget(self.calibrationSection.main_groupbox)
        self.ui.groupbox_layout.addWidget(self.rfSection.main_groupbox)
        
        self.calibration_worker = None
        self.cryo_param_worker = None
        self.q0_setup_worker = None
        self.q0_ramp_workers = {i: None for i in range(1, 9)}
        self.q0_meas_worker = None
        self.cryo_param_setup_worker: q0_gui_utils.CryoParamSetupWorker = None
        
        self.ui.new_cryo_params_button.clicked.connect(self.getNewCryoParams)
        self.ui.setup_param_button.clicked.connect(self.setup_for_cryo_params)
        
        self.calibration_data_plot: PlotWidget = None
        self.calibration_data_plot_items = []
        self.calibration_fit_plot: PlotWidget = None
        self.calibration_fit_plot_items = []
        
        self.q0_data_plot: PlotWidget = None
        self.q0_data_plot_items = []
        self.q0_fit_plot: PlotWidget = None
        self.q0_fit_plot_items = []
        
        self.calibration_window: Display = None
        self.q0_window: Display = None
    
    @pyqtSlot()
    def show_q0_data(self):
        if not self.q0_window:
            self.q0_window = Display()
            self.q0_window.setWindowTitle("Q0 Plots")
            layout: QHBoxLayout = QHBoxLayout()
            self.q0_data_plot: PlotWidget = plot()
            self.q0_data_plot.setTitle("Q0 Data")
            self.q0_fit_plot: PlotWidget = plot()
            self.q0_fit_plot.setTitle("Heat On Calibration Curve (with adjustments)")
            layout.addWidget(self.q0_data_plot)
            layout.addWidget(self.q0_fit_plot)
            self.q0_window.setLayout(layout)
        
        while self.q0_data_plot_items:
            self.q0_data_plot.removeItem(self.q0_data_plot_items.pop())
        
        while self.q0_fit_plot_items:
            self.q0_fit_plot.removeItem(self.q0_fit_plot_items.pop())
        
        measurement = self.selectedCM.q0_measurement
        self.q0_data_plot_items.append(
                self.q0_data_plot.plot(list(measurement.rf_run.ll_data.keys()),
                                       list(measurement.rf_run.ll_data.values())))
        self.q0_data_plot_items.append(
                self.q0_data_plot.plot(list(measurement.heater_run.ll_data.keys()),
                                       list(measurement.heater_run.ll_data.values())))
        
        dll_dts = [measurement.rf_run.dll_dt, measurement.heater_run.dll_dt]
        self.q0_fit_plot_items.append(self.q0_fit_plot.plot([measurement.heat_load, measurement.heater_run_heatload],
                                                            dll_dts,
                                                            pen=None, symbol="o"))
        
        self.q0_fit_plot_items.append(
                self.q0_fit_plot.plot([self.selectedCM.calibration.get_heat(dll_dt) for dll_dt in dll_dts], dll_dts))
        
        showDisplay(self.q0_window)
    
    @pyqtSlot()
    def show_calibration_data(self):
        if not self.calibration_window:
            self.calibration_window = Display()
            self.calibration_window.setWindowTitle("Calibration Plots")
            layout: QHBoxLayout = QHBoxLayout()
            self.calibration_data_plot: PlotWidget = plot()
            self.calibration_data_plot.setTitle("Calibration Data")
            self.calibration_fit_plot: PlotWidget = plot()
            self.calibration_fit_plot.setTitle("Heat vs dll/dt")
            layout.addWidget(self.calibration_data_plot)
            layout.addWidget(self.calibration_fit_plot)
            self.calibration_window.setLayout(layout)
        
        while self.calibration_data_plot_items:
            self.calibration_data_plot.removeItem(self.calibration_data_plot_items.pop())
        
        while self.calibration_fit_plot_items:
            self.calibration_fit_plot.removeItem(self.calibration_fit_plot_items.pop())
        
        dll_dts = []
        
        for heater_run in self.selectedCM.calibration.heater_runs:
            self.calibration_data_plot_items.append(self.calibration_data_plot.plot(list(heater_run.ll_data.keys()),
                                                                                    list(heater_run.ll_data.values())))
            dll_dts.append(heater_run.dll_dt)
            self.calibration_fit_plot_items.append(
                    self.calibration_fit_plot.plot([heater_run.average_heat],
                                                   [heater_run.dll_dt], pen=None,
                                                   symbol="o"))
        
        heat_loads = [self.selectedCM.calibration.get_heat(dll_dt) for dll_dt in dll_dts]
        
        self.calibration_fit_plot_items.append(self.calibration_fit_plot.plot(heat_loads, dll_dts))
        
        showDisplay(self.calibration_window)
    
    @pyqtSlot()
    def load_calibration(self):
        if self.selectedCM.name not in self.cal_option_windows:
            option_window: Display = Display()
            cal_options = q0_gui_utils.CalibrationOptions(self.selectedCM)
            cal_options.cal_loaded_signal.connect(self.calibrationSection.handle_status)
            cal_options.cal_loaded_signal.connect(partial(self.rfSection.main_groupbox.setEnabled, True))
            cal_options.cal_loaded_signal.connect(self.show_calibration_data)
            window_layout = QVBoxLayout()
            window_layout.addWidget(cal_options.main_groupbox)
            option_window.setLayout(window_layout)
            self.cal_option_windows[self.selectedCM.name] = option_window
        showDisplay(self.cal_option_windows[self.selectedCM.name])
    
    @pyqtSlot()
    def load_q0(self):
        if self.selectedCM.name not in self.rf_option_windows:
            option_window: Display = Display()
            rf_options = q0_gui_utils.Q0Options(self.selectedCM)
            rf_options.q0_loaded_signal.connect(self.rfSection.handle_status)
            rf_options.q0_loaded_signal.connect(self.show_q0_data)
            window_layout = QVBoxLayout()
            window_layout.addWidget(rf_options.main_groupbox)
            option_window.setLayout(window_layout)
            self.rf_option_windows[self.selectedCM.name] = option_window
        showDisplay(self.rf_option_windows[self.selectedCM.name])
    
    @pyqtSlot(int)
    def update_ll_buffer(self, value):
        if self.selectedCM:
            self.selectedCM.ll_buffer_size = value
    
    @property
    def jt_search_start(self):
        return self.jt_search_start_edit.dateTime().toPyDateTime()
    
    @property
    def jt_search_end(self):
        return self.jt_search_end_edit.dateTime().toPyDateTime()
    
    @pyqtSlot()
    def getNewCryoParams(self):
        self.cryo_param_worker = CryoParamWorker(cryomodule=self.selectedCM,
                                                 start_time=self.jt_search_start,
                                                 end_time=self.jt_search_end)
        self.cryo_param_worker.status.connect(self.ui.cryo_param_status_label.setText)
        self.cryo_param_worker.error.connect(self.ui.cryo_param_status_label.setText)
        self.cryo_param_worker.finished.connect(self.update_cryo_params)
        self.cryo_param_worker.start()
    
    @pyqtSlot()
    def update_cryo_params(self):
        self.ui.ref_heat_spinbox.setValue(self.selectedCM.valveParams.refHeatLoadDes)
        self.ui.jt_pos_spinbox.setValue(self.selectedCM.valveParams.refValvePos)
    
    @pyqtSlot()
    def setup_for_cryo_params(self):
        self.cryo_param_setup_worker = q0_gui_utils.CryoParamSetupWorker(self.selectedCM)
        self.cryo_param_setup_worker.error.connect(partial(q0_gui_utils.make_error_popup,
                                                           "Cryo Setup Error"))
        self.cryo_param_setup_worker.start()
    
    @pyqtSlot()
    def takeNewCalibration(self):
        heater_delta = ((self.end_heat_spinbox.value()
                         - self.start_heat_spinbox.value()) / self.num_cal_points_spinbox.value())
        if self.ui.manual_cryo_groupbox.isChecked():
            self.selectedCM.valveParams = ValveParams(refHeatLoadDes=self.ui.ref_heat_spinbox.value(),
                                                      refValvePos=self.ui.jt_pos_spinbox.value(),
                                                      refHeatLoadAct=self.ui.ref_heat_spinbox.value())
        
        self.calibration_worker = CalibrationWorker(cryomodule=self.selectedCM,
                                                    start_heat=self.start_heat_spinbox.value(),
                                                    jt_search_start=self.jt_search_start,
                                                    jt_search_end=self.jt_search_end,
                                                    desired_ll=self.min_start_ll_spinbox.value(),
                                                    heater_delta=heater_delta,
                                                    num_cal_steps=self.num_cal_points_spinbox.value(),
                                                    ll_drop=self.ll_drop_spinbox.value())
        self.calibration_worker.status.connect(self.calibrationSection.handle_status)
        self.calibration_worker.finished.connect(self.calibrationSection.handle_status)
        self.calibration_worker.error.connect(self.calibrationSection.handle_error)
        self.calibration_worker.finished.connect(partial(self.rfSection.main_groupbox.setEnabled, True))
        self.calibration_worker.start()
    
    @property
    def desiredCavityAmplitudes(self):
        amplitudes = {}
        cm_selector = self.cm_selectors[self.selectedCM.name]
        for cav_amp_control in cm_selector.cavity_amp_controls.values():
            if cav_amp_control.groupbox.isChecked():
                amplitudes[cav_amp_control.number] = cav_amp_control.desAmpSpinbox.value()
        print(f"Cavity amplitudes: {amplitudes}")
        return amplitudes
    
    @pyqtSlot()
    def ramp_cavities(self):
        des_amps = self.desiredCavityAmplitudes
        
        for cav_num, des_amp in des_amps.items():
            cavity = self.selectedCM.cavities[cav_num]
            ramp_worker = q0_gui_utils.CavityRampWorker(cavity, des_amp)
            self.q0_ramp_workers[cav_num] = ramp_worker
            ramp_worker.finished.connect(cavity.mark_ready)
            ramp_worker.start()
        
        self.q0_meas_worker = q0_gui_utils.Q0Worker(cryomodule=self.selectedCM,
                                                    jt_search_start=self.jt_search_start,
                                                    jt_search_end=self.jt_search_end,
                                                    desired_ll=self.min_start_ll_spinbox.value(),
                                                    ll_drop=self.ll_drop_spinbox.value(),
                                                    desired_amplitudes=self.desiredCavityAmplitudes)
        self.q0_meas_worker.error.connect(partial(q0_gui_utils.make_error_popup, "Q0 Measurement Error"))
        self.q0_meas_worker.start()
    
    @pyqtSlot()
    def takeNewQ0Measurement(self):
        if self.ui.manual_cryo_groupbox.isChecked():
            self.selectedCM.valveParams = ValveParams(refHeatLoadDes=self.ui.ref_heat_spinbox.value(),
                                                      refValvePos=self.ui.jt_pos_spinbox.value(),
                                                      refHeatLoadAct=None)
        
        self.q0_setup_worker = q0_gui_utils.Q0SetupWorker(cryomodule=self.selectedCM,
                                                          jt_search_start=self.jt_search_start,
                                                          jt_search_end=self.jt_search_end,
                                                          desired_ll=self.min_start_ll_spinbox.value(),
                                                          ll_drop=self.ll_drop_spinbox.value(),
                                                          desired_amplitudes=self.desiredCavityAmplitudes)
        self.q0_setup_worker.status.connect(self.rfSection.handle_status)
        self.q0_setup_worker.finished.connect(self.rfSection.handle_status)
        self.q0_setup_worker.finished.connect(self.ramp_cavities)
        self.q0_setup_worker.error.connect(self.rfSection.handle_error)
        self.q0_setup_worker.start()
    
    @staticmethod
    def make_setting_groupbox(title: str, widget: QWidget, unit: str = None):
        hor_layout: QHBoxLayout = QHBoxLayout()
        hor_layout.addWidget(widget)
        if unit:
            hor_layout.addWidget(QLabel(unit))
        
        groupbox: QGroupBox = QGroupBox(title)
        groupbox.setLayout(hor_layout)
        
        return groupbox
    
    def setup_settings(self):
        self.ll_drop_spinbox.setRange(1, 5)
        self.ll_drop_spinbox.setValue(DEFAULT_LL_DROP)
        
        self.min_start_ll_spinbox.setRange(91, 93)
        self.min_start_ll_spinbox.setValue(MIN_STARTING_LL)
        
        self.start_heat_spinbox.setRange(20, 160)
        self.start_heat_spinbox.setValue(DEFAULT_START_HEAT)
        
        self.end_heat_spinbox.setRange(20, 160)
        self.end_heat_spinbox.setValue(DEFAULT_END_HEAT)
        
        self.num_cal_points_spinbox.setRange(2, 20)
        self.num_cal_points_spinbox.setValue(DEFAULT_NUM_CAL_POINTS)
        
        end_time: datetime = datetime.now()
        self.jt_search_end_edit.setDateTime(end_time)
        self.jt_search_start_edit.setCalendarPopup(True)
        self.jt_search_start_edit.setDateTime(end_time - DEFAULT_JT_START_DELTA)
        self.jt_search_end_edit.setCalendarPopup(True)
        
        self.ll_buffer_spinbox.setRange(1, 25)
        self.ll_buffer_spinbox.setValue(DEFAULT_LL_BUFFER_SIZE)
    
    def open_settings_window(self):
        if not self.settings_window:
            self.settings_window = Display()
            self.settings_window.setWindowTitle("Q0 Measurement Settings")
            vlayout: QVBoxLayout = QVBoxLayout()
            vlayout.addWidget(self.make_setting_groupbox("Liquid Level Drop",
                                                         self.ll_drop_spinbox, "%"))
            vlayout.addWidget(self.make_setting_groupbox("Minimum LL to start",
                                                         self.min_start_ll_spinbox, "%"))
            vlayout.addWidget(self.make_setting_groupbox("Initial Calibration Heat Load",
                                                         self.start_heat_spinbox, "W"))
            vlayout.addWidget(self.make_setting_groupbox("Final Calibration Heat Load",
                                                         self.end_heat_spinbox, "W"))
            vlayout.addWidget(self.make_setting_groupbox("Number of Calibration Data Points",
                                                         self.num_cal_points_spinbox))
            vlayout.addWidget(self.make_setting_groupbox("JT Stability Search Start",
                                                         self.jt_search_start_edit))
            vlayout.addWidget(self.make_setting_groupbox("JT Stability Search End",
                                                         self.jt_search_end_edit))
            vlayout.addWidget(self.make_setting_groupbox("Liquid Level Buffer Size (for rolling average)",
                                                         self.ll_buffer_spinbox))
            self.settings_window.setLayout(vlayout)
        
        showDisplay(self.settings_window)
    
    def updateSelectedText(self):
        if not self.selectedCM:
            self.ui.cmSelectionLabel.setStyleSheet("color: red;")
            self.ui.cmSelectionLabel.setText("No Cryomodules Selected")
            self.calibrationSection.main_groupbox.setEnabled(False)
            self.rfSection.main_groupbox.setEnabled(False)
            self.ui.new_cryo_params_button.setEnabled(False)
            self.ui.setup_param_button.setEnabled(False)
        
        else:
            self.ui.cmSelectionLabel.setStyleSheet("color: green;")
            self.ui.cmSelectionLabel.setText(self.selectedCM.name)
            self.calibrationSection.main_groupbox.setEnabled(True)
            self.ui.new_cryo_params_button.setEnabled(True)
            self.ui.setup_param_button.setEnabled(True)
    
    def open_cm_selector_window(self):
        if not self.cm_selector_window:
            self.cm_selector_window: Display = Display()
            self.cm_selector_window.setWindowTitle("Q0 Cryomodule Selector")
            vlayout: QVBoxLayout = QVBoxLayout()
            tab_widget: QTabWidget = QTabWidget()
            vlayout.addWidget(tab_widget)
            self.cm_selector_window.setLayout(vlayout)
            
            for linac_name, cm_list in LINAC_TUPLES:
                if linac_name == "L1B":
                    cm_list += L1BHL
                page: QWidget = QWidget()
                gridlayout: QGridLayout = QGridLayout()
                page.setLayout(gridlayout)
                tab_widget.addTab(page, linac_name)
                
                for idx, cm_name in enumerate(cm_list):
                    cm_selector = CryomoduleSelector(cm_name, self.button_group,
                                                     self)
                    column = idx % 5
                    row = int(idx / 5)
                    gridlayout.addWidget(cm_selector.groupbox, row, column)
                    self.cm_selectors[cm_name] = cm_selector
        
        showDisplay(self.cm_selector_window)
