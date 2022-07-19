import json
import sys
from datetime import datetime, timedelta
from functools import partial, reduce
from os import pardir, path
from typing import Callable, Dict, List, Optional, Union

from PyQt5.QtGui import (QDoubleValidator, QIntValidator, QStandardItem, QStandardItemModel)
from PyQt5.QtWidgets import (QButtonGroup, QCheckBox, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QMessageBox, QPushButton,
                             QRadioButton, QTableView, QVBoxLayout, QWidget,
                             QWidgetItem)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from pydm import Display
from pydm.widgets.embedded_display import PyDMEmbeddedDisplay
from pydm.widgets.label import PyDMLabel
from pydm.widgets.template_repeater import PyDMTemplateRepeater
from qtpy.QtCore import Slot

sys.path.insert(0, '..')

# This is down here because we need the sys path insert first to access this module
from q0Utils import (FULL_CALIBRATION_FILENAME_TEMPLATE,
                     CAVITY_CALIBRATION_FILENAME_TEMPLATE, redrawAxis)
from q0Linac import Q0Cryomodule, Q0_CRYOMODULES
from dataSession import CalibDataSession


class MplCanvas(FigureCanvasQTAgg):
    
    def __init__(self, width=5, height=4, dpi=100):
        fig = Figure(figsize=(width, height), dpi=dpi)
        self.axes = fig.add_subplot(111)
        super(MplCanvas, self).__init__(fig)


def findWidget(accessibleName: str,
               widgetList: List[Union[QLineEdit, QPushButton]]) -> Union[QLineEdit, QPushButton]:
    for widget in widgetList:
        if widget.accessibleName() == accessibleName:
            return widget


class Q0Measurement(Display):
    
    def __init__(self, parent=None, args=None):
        
        super(Q0Measurement, self).__init__(parent=parent, args=args,
                                            ui_filename="q0.ui")
        
        self.pathHere = path.dirname(sys.modules[self.__module__].__file__)
        
        # Set up calibration data window
        self.calibrationLiquidLevelCanvas = MplCanvas()
        self.calibrationLineFitCanvas = MplCanvas()
        self.calibrationResultsWindow = None
        self.setupCalibrationDataWindow()
        
        # Set up RF data window
        self.rfLiquidLevelCanvas = MplCanvas()
        self.rfLineFitCanvas = MplCanvas()
        self.rfResultsWindow = None
        self.setupRfDataWindow()
        
        self.settingsWindow = Display(ui_filename=self.getPath("settings.ui"))
        self.setupSettingsWindow()
        
        heaterLineEdits = self.settingsWindow.ui.heaterSettingRepeater.findChildren(QLineEdit)
        self.initialCalibrationHeatLoadLineEdit: QLineEdit = findWidget("initialCalibrationHeatLoad",
                                                                        heaterLineEdits)
        self.initialCalibrationHeatLoadLineEdit.setValidator(QIntValidator(8, 48))
        
        self.selectedCryomoduleObject: Q0Cryomodule = None
        
        self.calibrationOptionModel = QStandardItemModel(self)
        
        self.calibrationOptionsWindow = Display(ui_filename=self.getPath("options.ui"))
        self.setupCalibrationOptionsWindow()
        
        self.calibrationSelection = {}
        
        self.calibrationStatusLabel = None
        self.q0StatusLabel = None
        self.setupLabels()
        
        self.calibrationGroupBox = None
        self.rfGroupBox = None
        self.setupFrames()
        
        self.selectWindow = Display(ui_filename=self.getPath("cmSelection.ui"))
        self.ui.cmSelectButton.clicked.connect(partial(self.showDisplay,
                                                       self.selectWindow))
        
        self.pathToAmplitudeWindow = self.getPath("amplitude.ui")
        
        # maps cryomodule names to their checkboxes
        self.cryomoduleRadioButtons: Dict[str, QRadioButton] = {}
        
        # maps cryomodule names to their buttons
        self.cryomoduleButtons: Dict[str, QPushButton] = {}
        
        # maps checkboxes to their buttons
        self.cryomoduleRadioButtonMap: Dict[QRadioButton, QPushButton] = {}
        
        # maps cryomodule names to the cavity display
        self.buttonDisplays: Dict[str, Display] = {}
        
        # maps cyomodule names to desired cavity amplitudes
        self.desiredCavAmpLineEdits: Dict[str, List[QLineEdit]] = {}
        
        self.maxAmplitudeLabels: Dict[str, PyDMLabel] = {}
        
        # maps cryomodule names to cavity groupboxes
        self.cavityGroupBoxes: Dict[str, List[QGroupBox]] = {}
        
        # maps cryomodule names to select all cavities checkbox
        self.selectAllCavitiesCheckboxes: Dict[str, QCheckBox] = {}
        
        self.selectedDisplayCM: Optional[str] = None
        self.selectedCM: Optional[str] = None
        
        self.sectors = [[], [], [], []]  # type: List[List[str]]
        
        self.radioButtonSectorMap: Dict[QRadioButton, int] = {}
        self.buttonGroup = QButtonGroup(self)
        self.populateRadioButtons()
        
        for sector in self.sectors:
            self.calibrationOptionsWindow.ui.cryomoduleComboBox.addItems(sector)
        
        self.calibrationOptionsWindow.ui.cavityComboBox.addItem("ALL")
        for i in range(1, 9):
            self.calibrationOptionsWindow.ui.cavityComboBox.addItem(str(i))
        
        self.calibrationSanityCheck = QMessageBox()
        self.setupSanityCheck(self.calibrationSanityCheck,
                              self.calibrationStatusLabel, self.takeNewCalibration)
        
        self.q0SanityCheck = QMessageBox()
        self.setupSanityCheck(self.q0SanityCheck,
                              self.q0StatusLabel, self.takeNewQ0Measurement)
        
        self.setupButtons()
        
        self.calibration = None
        
        self.valveParams = {}
    
    @Slot()
    # TODO needs to check all cavities
    def cavityToggled(self, cm):
        # type: (str) -> None
        
        statusList = list(map(lambda groupbox: groupbox.isChecked(),
                              self.cavityGroupBoxes[cm]))
        allChecked = reduce((lambda isChecked1, isChecked2: isChecked1 and isChecked2),
                            statusList)
        
        if statusList.count(statusList[0]) != len(statusList):
            self.selectAllCavitiesCheckboxes[cm].setCheckState(1)
        
        else:
            self.selectAllCavitiesCheckboxes[cm].setCheckState(0 if not allChecked else 2)
        
        self.updateOutputBasedOnDefaultConfig(cm, allChecked)
    
    @Slot()
    # TODO use AMAX/ops limit
    def checkIfAllCavitiesAtDefault(self, cm, updateOutput=True):
        # type: (str, bool) -> bool
        
        isDefault = True
        
        for idx, lineEdit in enumerate(self.desiredCavAmpLineEdits[cm]):
            if lineEdit.text():
                try:
                    amplitude = float(lineEdit.text())
                    maxAmp = float(self.maxAmplitudeLabels[cm][idx].text())
                    if amplitude > maxAmp:
                        lineEdit.clear()
                    elif amplitude != maxAmp:
                        isDefault = False
                
                except ValueError:
                    lineEdit.clear()
                    isDefault = False
            else:
                isDefault = False
        
        if updateOutput:
            self.updateOutputBasedOnDefaultConfig(cm, isDefault)
        
        return isDefault
    
    @Slot()
    # TODO check if all checkboxes in sector are clicked
    def cryomoduleRadioButtonToggled(self, cryomoduleName):
        # type:  (str) -> None
        
        radioButton = self.cryomoduleRadioButtons[cryomoduleName]
        button = self.cryomoduleButtons[cryomoduleName]
        
        if radioButton.isChecked():
            button.setEnabled(True)
            
            isDefault = (self.checkIfAllCavitiesAtDefault(cryomoduleName, False)
                         if cryomoduleName in self.desiredCavAmpLineEdits else True)
            
            self.selectedDisplayCM = cryomoduleName + ("*" if not isDefault else "")
            self.selectedCM = cryomoduleName
            
            self.selectedCryomoduleObject = Q0_CRYOMODULES[self.selectedCM]
        
        else:
            button.setEnabled(False)
        
        self.updateSelectedText()
        self.calibrationOptionsWindow.cryomoduleComboBox.setCurrentText(self.selectedCM)
    
    @property
    def desiredCavityAmplitudes(self):
        output = {}
        for cavity, lineEdit in enumerate(self.desiredCavAmpLineEdits[self.selectedCM]):
            try:
                output[cavity] = float(lineEdit.text())
            except ValueError:
                print("Amplitude for cm{cm} cavity {cav} invalid, using 0".format(cm=self.selectedCM,
                                                                                  cav=cavity))
                output[cavity] = 0.0
        
        return output
    
    def getPath(self, fileName):
        return path.join(self.pathHere, fileName)
    
    def loadCalibration(self):
        
        cryomodule: Q0Cryomodule = Q0_CRYOMODULES[self.calibrationSelection["CM"]]
        self.calibration: CalibDataSession = cryomodule.addCalibDataSessionFromGUI(self.calibrationSelection)
        
        redrawAxis(self.calibrationLiquidLevelCanvas,
                   title="Liquid Level vs. Time", xlabel="Unix Time (s)",
                   ylabel="Downstream Liquid Level (%)")
        
        redrawAxis(self.calibrationLineFitCanvas,
                   title="Liquid Level Rate of Change vs. Heat Load",
                   xlabel="Heat Load (W)", ylabel="dLL/dt (%/s)")
        
        for run in self.calibration.dataRuns:
            self.calibrationLiquidLevelCanvas.axes.plot(run.timeStamps, run.data,
                                                        label=run.label)
            self.calibrationLiquidLevelCanvas.axes.plot(run.timeStamps, [run.slope * x
                                                                         + run.intercept
                                                                         for x
                                                                         in run.timeStamps])
        
        self.calibrationLineFitCanvas.axes.plot(self.calibration.runElecHeatLoadsAdjusted,
                                                self.calibration.adjustedRunSlopes,
                                                marker="o", linestyle="None",
                                                label="Heater Calibration Data")
        
        slopeStr = "{slope} %/(s*W)".format(slope=self.calibration.calibSlope)
        self.calibrationResultsWindow.ui.slope.setText(slopeStr)
        
        self.calibrationLineFitCanvas.axes.plot(self.calibration.runElecHeatLoadsAdjusted,
                                                [self.calibration.calibSlope * x
                                                 for x in self.calibration.runElecHeatLoadsAdjusted],
                                                label=slopeStr)
        
        self.calibrationLineFitCanvas.axes.legend(loc='best')
        self.calibrationLiquidLevelCanvas.axes.legend(loc='best')
        
        self.calibrationStatusLabel.setStyleSheet("color: green;")
        self.calibrationStatusLabel.setText(
                "CM {CM} calibration from {START} loaded".format(
                        START=self.calibrationSelection["Start"],
                        CM=self.calibrationSelection["CM"]))
        
        self.rfGroupBox.setEnabled(True)
    
    @Slot()
    def openAmplitudeWindow(self, cm, sector):
        # type: (str, int) -> None
        
        # Put this here to speed things up (it was suuuuuuuper slow trying to do
        # this on startup)
        if cm not in self.buttonDisplays:
            displayCM = Display(ui_filename=self.pathToAmplitudeWindow,
                                macros={"cm"  : cm,
                                        "area": "L{SECTOR}B".format(SECTOR=sector)})
            repeater = displayCM.ui.cavityRepeater
            
            lineEdits = []
            groupBoxes = []
            amaxLabels = []
            
            for cav in range(8):
                item = repeater.layout().itemAt(cav)  # type: QWidgetItem
                displayCav = item.widget()  # type: Display
                
                lineEdit = displayCav.ui.desiredAmplitude  # type: QLineEdit
                validator = QDoubleValidator(0.0, 21.0, 2, lineEdit)
                validator.setNotation(QDoubleValidator.StandardNotation)
                lineEdit.setValidator(validator)
                amaxLabel = displayCav.ui.amaxLabel
                
                try:
                    maxAmplitude = float(amaxLabel.text())
                except (TypeError, ValueError):
                    maxAmplitude = 0
                
                lineEdit.setText(str(maxAmplitude))
                lineEdit.textChanged.connect(partial(self.checkIfAllCavitiesAtDefault, cm))
                lineEdits.append(lineEdit)
                
                groupBox = displayCav.ui.cavityGroupbox  # type: QGroupBox
                groupBox.toggled.connect(partial(self.cavityToggled, cm))
                groupBoxes.append(groupBox)
                
                amaxLabels.append(amaxLabel)
                
                restoreDefaultButton: QPushButton = displayCav.ui.restoreDefaultButton
                restoreDefaultButton.clicked.connect(partial(self.restoreDefault,
                                                             lineEdit,
                                                             displayCav.ui.amaxLabel))
            
            self.desiredCavAmpLineEdits[cm] = lineEdits
            self.buttonDisplays[cm] = displayCM
            self.cavityGroupBoxes[cm] = groupBoxes
            self.maxAmplitudeLabels[cm] = amaxLabels
            
            selectAllCheckbox = displayCM.ui.selectAllCheckbox  # type: QCheckBox
            self.selectAllCavitiesCheckboxes[cm] = selectAllCheckbox
            selectAllCheckbox.stateChanged.connect(partial(self.selectAllCavitiesToggled,
                                                           cm))
        
        self.buttonDisplays[cm].show()
    
    # This is some convoluted bullshit
    
    def populateCalibrationTable(self, calibrationFilePath):
        with open(calibrationFilePath) as calibrationFile:
            data: List[dict] = json.load(calibrationFile)
            
            header = list(data[0].keys())
            self.calibrationOptionModel.setHorizontalHeaderLabels(header)
            for sessionDict in data:
                items = []
                for key in header:
                    # Need to cast ints/floats as strings or they don't show up
                    items.append(QStandardItem(str(sessionDict[key])))
                self.calibrationOptionModel.appendRow(items)
        
        self.calibrationOptionsWindow.ui.optionView.resizeColumnsToContents()
    
    def populateCavityCalibrationOptions(self):
        cavity = self.calibrationOptionsWindow.ui.cavityComboBox.currentText()
        
        if cavity == "ALL":
            self.populateCryomoduleCalibrationOptions()
            return
        
        cmName, calFolderPath = self.setupCalibrationTable()
        calibrationFile = path.join(calFolderPath,
                                    CAVITY_CALIBRATION_FILENAME_TEMPLATE.format(CM=cmName,
                                                                                CAV=cavity))
        if not path.isfile(calibrationFile):
            print(calibrationFile)
            return
        
        self.populateCalibrationTable(calibrationFile)
    
    def populateCryomoduleCalibrationOptions(self):
        
        cmName, calFolderPath = self.setupCalibrationTable()
        
        calibrationFile = path.join(calFolderPath,
                                    FULL_CALIBRATION_FILENAME_TEMPLATE.format(CM=cmName))
        
        if not path.isfile(calibrationFile):
            return
        
        self.populateCalibrationTable(calibrationFile)
    
    def populateRadioButtons(self):
        
        displays: List[PyDMEmbeddedDisplay] = [self.selectWindow.ui.cryomodulesL0B,
                                               self.selectWindow.ui.cryomodulesL1B,
                                               self.selectWindow.ui.cryomodulesL2B,
                                               self.selectWindow.ui.cryomodulesL3B]
        
        for sector, display in enumerate(displays):
            display.loadWhenShown = False
            
            groupbox: QVBoxLayout = display.findChildren(QVBoxLayout).pop()
            repeater: PyDMTemplateRepeater = groupbox.itemAt(0).widget()
            
            pairs: List[QHBoxLayout] = repeater.findChildren(QHBoxLayout)
            
            for pair in pairs:
                button: QPushButton = pair.itemAt(1).widget()
                name = button.text().split()[1]
                self.cryomoduleButtons[name] = button
                button.clicked.connect(partial(self.openAmplitudeWindow, name,
                                               sector))
                
                radioButton: QRadioButton = pair.itemAt(0).widget()
                self.cryomoduleRadioButtons[name] = radioButton
                
                self.cryomoduleRadioButtonMap[radioButton] = button
                
                self.buttonGroup.addButton(radioButton)
                
                radioButton.toggled.connect(partial(self.cryomoduleRadioButtonToggled,
                                                    name))
                
                self.sectors[sector].append(name)
                self.radioButtonSectorMap[radioButton] = sector
    
    @Slot()
    def restoreDefault(self, desiredAmplitude: QLineEdit, amaxLabel: PyDMLabel):
        try:
            maxAmplitude = float(amaxLabel.text())
        except (ValueError, TypeError):
            maxAmplitude = 0
        
        desiredAmplitude.setText(str(maxAmplitude))
    
    @Slot()
    def selectAllCavitiesToggled(self, cm):
        state = self.selectAllCavitiesCheckboxes[cm].checkState()
        
        if state == 0:
            for cavityGroupbox in self.cavityGroupBoxes[cm]:
                cavityGroupbox.setChecked(False)
        elif state == 2:
            for cavityGroupbox in self.cavityGroupBoxes[cm]:
                cavityGroupbox.setChecked(True)
    
    @Slot()
    def selectAllSectorCryomodules(self, selectAllCheckbox, sector):
        # type:  (QCheckBox, int) -> None
        
        sectorLabel = "L{SECTOR}B".format(SECTOR=sector)
        
        if selectAllCheckbox.checkState() == 2:
            
            for name in self.sectors[sector]:
                self.cryomoduleRadioButtons[name].setChecked(True)
                self.selectedDisplayCM.discard(name)
            
            if (sectorLabel + "*") not in self.selectedDisplayCM:
                self.selectedDisplayCM.add(sectorLabel)
        
        elif selectAllCheckbox.checkState() == 0:
            for name in self.sectors[sector]:
                self.cryomoduleRadioButtons[name].setChecked(False)
            
            self.selectedDisplayCM.discard(sectorLabel)
            self.selectedDisplayCM.discard(sectorLabel + "*")
        
        self.updateSelectedText()
    
    def selectCalibration(self):
        
        self.calibrationSelection["CM"] = self.calibrationOptionsWindow.ui.cryomoduleComboBox.currentText()
        
        calibrationTableView = self.calibrationOptionsWindow.ui.optionView  # type: QTableView
        row = calibrationTableView.selectionModel().selectedRows().pop().row()
        
        for column in range(self.calibrationOptionModel.columnCount()):
            data = self.calibrationOptionModel.index(row, column).data()
            header = self.calibrationOptionModel.horizontalHeaderItem(column).text()
            self.calibrationSelection[header] = data
        
        # self.calibrationStatus.setStyleSheet("color: blue;")
        self.calibrationStatusLabel.setText(
                "CM {CM} calibration from {START} selected but not loaded".format(
                        START=self.calibrationSelection["Start"],
                        CM=self.calibrationSelection["CM"]))
        
        self.calibrationOptionsWindow.ui.loadButton.setEnabled(True)
    
    def setupCalibrationOptionsWindow(self):
        self.calibrationOptionsWindow.ui.cryomoduleComboBox.currentTextChanged.connect(
                self.populateCryomoduleCalibrationOptions)
        self.calibrationOptionsWindow.ui.cavityComboBox.currentTextChanged.connect(
                self.populateCavityCalibrationOptions)
        self.calibrationOptionsWindow.ui.optionView.setModel(self.calibrationOptionModel)
        self.calibrationOptionsWindow.ui.optionView.selectionModel().selectionChanged.connect(self.selectCalibration)
        self.calibrationOptionsWindow.ui.loadButton.clicked.connect(self.loadCalibration)
    
    def setupRfDataWindow(self):
        self.rfResultsWindow = Display(ui_filename=self.getPath("results.ui"),
                                       macros={"label": "dLL/dt Slope"})
        self.rfResultsWindow.ui.dataLayout.addWidget(self.rfLiquidLevelCanvas)
        self.rfResultsWindow.ui.lineFitLayout.addWidget(self.rfLineFitCanvas)
    
    def setupCalibrationDataWindow(self):
        self.calibrationResultsWindow = Display(ui_filename=self.getPath("results.ui"),
                                                macros={"label": "dLL/dt Slope"})
        self.calibrationResultsWindow.ui.dataLayout.addWidget(self.calibrationLiquidLevelCanvas)
        self.calibrationResultsWindow.ui.lineFitLayout.addWidget(self.calibrationLineFitCanvas)
    
    def setupCalibrationTable(self) -> (str, str):
        self.calibrationOptionModel.clear()
        parent = path.abspath(path.join(self.pathHere, pardir))
        cmName = self.calibrationOptionsWindow.ui.cryomoduleComboBox.currentText()
        calibrationFolder = path.join(path.join(parent, "calibrations"), "cm{NAME}".format(NAME=cmName))
        return cmName, calibrationFolder
    
    def setupFrames(self):
        groupBoxes = {}  # type: Dict[str, QGroupBox]
        for groupBox in self.ui.dialogues.findChildren(QGroupBox):
            if groupBox.accessibleName():
                groupBoxes[groupBox.accessibleName()] = groupBox
        self.calibrationGroupBox = groupBoxes["calibrationGroupBox"]
        self.rfGroupBox = groupBoxes["rfGroupBox"]
        
        # For some reason, I need to disable these here vs in designer or the
        # conditional enabling doesn't work
        self.calibrationGroupBox.setEnabled(False)
        self.rfGroupBox.setEnabled(False)
    
    def setupLabels(self):
        name2label = {}  # type: Dict[str, QLabel]
        for label in self.ui.dialogues.findChildren(QLabel):
            name2label[label.accessibleName()] = label
        self.calibrationStatusLabel = name2label["calibrationLabel"]
        self.q0StatusLabel = name2label["rfLabel"]
    
    def setupButtons(self):
        name2button = {}  # type: Dict[str, QPushButton]
        # Get all the buttons from my template repeater and figure out which
        # one's which with the accessible names (set in dialogues.json)
        for button in self.ui.dialogues.findChildren(QPushButton):
            name2button[button.accessibleName()] = button
        
        name2button["newCalibrationButton"].clicked.connect(self.calibrationSanityCheck.show)
        name2button["loadCalibrationButton"].clicked.connect(partial(self.showDisplay,
                                                                     self.calibrationOptionsWindow))
        name2button["calibrationDataButton"].clicked.connect(partial(self.showDisplay,
                                                                     self.calibrationResultsWindow))
    
    def setupSettingsWindow(self):
        self.settingsWindow.ui.valvePosSearchStart.setDateTime(datetime.now())
        self.settingsWindow.ui.valvePosSearchEnd.setDateTime(datetime.now()
                                                             - timedelta(hours=24))
        self.ui.settingsButton.clicked.connect(partial(self.showDisplay,
                                                       self.settingsWindow))
    
    def setupSanityCheck(self, sanityCheckWindow: QMessageBox,
                         statusLabel: QLabel, measurementFunction: Callable):
        def takeMeasurement(decision):
            if "No" in decision.text():
                print("Measurement canceled")
                return
            else:
                print("Taking new measurement")
                statusLabel.setStyleSheet("color: blue;")
                statusLabel.setText("Starting Procedure")
                measurementFunction()
        
        sanityCheckWindow.setWindowTitle("Sanity Check")
        sanityCheckWindow.setText("Are you sure? This will take multiple hours")
        sanityCheckWindow.setIcon(QMessageBox.Warning)
        sanityCheckWindow.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        sanityCheckWindow.setDefaultButton(QMessageBox.No)
        sanityCheckWindow.buttonClicked.connect(takeMeasurement)
    
    @Slot()
    def takeNewCalibration(self):
        self.selectedCryomoduleObject.takeNewCalibration(int(self.initialCalibrationHeatLoadLineEdit.text()))
    
    @Slot()
    def takeNewQ0Measurement(self):
        self.selectedCryomoduleObject.takeNewQ0Measurement(self.desiredCavityAmplitudes)
    
    @Slot()
    def showDisplay(self, display):
        # type: (QWidget) -> None
        display.show()
        
        # brings the display to the front
        display.raise_()
        
        # gives the display focus
        display.activateWindow()
    
    def updateOutputBasedOnDefaultConfig(self, cm, isDefault):
        starredName = "{CM}*".format(CM=cm)
        
        self.selectedDisplayCM = cm if isDefault else starredName
        
        self.updateSelectedText()
    
    def updateSelectedText(self):
        if not self.selectedDisplayCM:
            self.ui.cmSelectionLabel.setStyleSheet("color: red;")
            self.ui.cmSelectionLabel.setText("No Cryomodules Selected")
            self.calibrationGroupBox.setEnabled(False)
            self.rfGroupBox.setEnabled(False)
        
        else:
            self.ui.cmSelectionLabel.setStyleSheet("color: green;")
            self.ui.cmSelectionLabel.setText(self.selectedDisplayCM)
            self.calibrationGroupBox.setEnabled(True)
