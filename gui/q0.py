from pydm import Display
from PyQt5 import QtGui
from PyQt5.QtWidgets import (QWidgetItem, QCheckBox, QPushButton, QLineEdit,
                             QGroupBox, QHBoxLayout)
from os import path
from qtpy.QtCore import Slot
from pydm.widgets.template_repeater import PyDMTemplateRepeater
from typing import List, Dict
from functools import partial, reduce
from datetime import datetime, timedelta
import sys

DEFAULT_AMPLITUDE = 16.608

sys.path.insert(0, '..')


class Q0Measurement(Display):
    def __init__(self, parent=None, args=[], ui_filename="q0.ui"):

        super(Q0Measurement, self).__init__(parent=parent, args=args,
                                            ui_filename=ui_filename)

        pathHere = path.dirname(sys.modules[self.__module__].__file__)

        def getPath(fileName):
            return path.join(pathHere, fileName)

        self.selectWindow = Display(ui_filename=getPath("cmSelection.ui"))
        self.ui.cmSelectButton.clicked.connect(partial(self.showDisplay,
                                                       self.selectWindow))

        self.settingsWindow = Display(ui_filename=getPath("settings.ui"))
        self.settingsWindow.ui.valvePosSearchStart.setDateTime(datetime.now())
        self.settingsWindow.ui.valvePosSearchEnd.setDateTime(datetime.now()
                                                             - timedelta(hours=24))
        self.ui.settingsButton.clicked.connect(partial(self.showDisplay,
                                                       self.settingsWindow))

        self.pathToAmplitudeWindow = getPath("amplitude.ui")

        # maps cryomodule names to their checkboxes
        self.cryomoduleCheckboxes = {}  # type: Dict[str, QCheckBox]

        # maps cryomodule names to their buttons
        self.cryomoduleButtons = {}  # type: Dict[str, QPushButton]

        # maps checkboxes to their buttons
        self.cryomoduleCheckboxButtonMap = {}  # type: Dict[QCheckBox, QPushButton]

        # maps cryomodule names to the cavity display
        self.buttonDisplays = {}  # type: Dict[str, Display]

        # maps cyomodule names to desired cavity amplitudes
        self.desiredCavAmps = {}  # type: Dict[str, List[QLineEdit]]

        # maps cryomodule names to cavity groupboxes
        self.cavityGroupBoxes = {}  # type: Dict[str, List[QGroupBox]]

        # maps cryomodule names to select all cavities checkbox
        self.selectAllCavitiesCheckboxes = {}  # type: Dict[str, QCheckBox]

        self.selectedDisplayCMs = set()
        self._selectedFullCMs = set()

        self.sectors = [[], [], [], []]  # type: List[List[str]]
        self.selectAllCheckboxes = [self.selectWindow.ui.selectAllCheckboxL0B,
                                    self.selectWindow.ui.selectAllCheckboxL1B,
                                    self.selectWindow.ui.selectAllCheckboxL2B,
                                    self.selectWindow.ui.selectAllCheckboxL3B]  # type: List[QCheckBox]

        self.checkboxSectorMap = {}  # type: Dict[QCheckBox, int]

        self.populateCheckboxes()

    # These nested methods have HIGH deprecation potential...
    def populateCheckboxes(self):

        for sector, checkbox in enumerate(self.selectAllCheckboxes):
            # TODO consider changing to QButtonGroup
            checkbox.stateChanged.connect(partial(self.selectAllSectorCryomodules, checkbox, sector))

        repeaters = [self.selectWindow.ui.cryomodulesL0B,
                     self.selectWindow.ui.cryomodulesL1B,
                     self.selectWindow.ui.cryomodulesL2B,
                     self.selectWindow.ui.cryomodulesL3B]  # type: List[PyDMTemplateRepeater]

        for sector, repeater in enumerate(repeaters):

            pairs = repeater.findChildren(QHBoxLayout)  # type: List[QHBoxLayout]

            for pair in pairs:
                button = pair.itemAt(1).widget()  # type: QPushButton
                name = button.text().split()[1]
                self.cryomoduleButtons[name] = button
                button.clicked.connect(partial(self.openAmplitudeWindow, name,
                                               sector))

                checkbox = pair.itemAt(0).widget()
                self.cryomoduleCheckboxes[name] = checkbox # type: QCheckBox

                self.cryomoduleCheckboxButtonMap[checkbox] = button

                # TODO consider changing to QButtonGroup
                checkbox.stateChanged.connect(partial(self.cryomoduleCheckboxToggled, name))

                self.sectors[sector].append(name)
                self.checkboxSectorMap[checkbox] = sector

    @Slot()
    def showDisplay(self, display):
        display.show()

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

        for lineEdit in self.desiredCavAmps[cm]:
            if lineEdit.text():
                try:
                    amplitude = float(lineEdit.text())
                    if amplitude != DEFAULT_AMPLITUDE:
                        isDefault = False
                        break

                except ValueError:
                    lineEdit.clear()
                    isDefault = False
                    break
            else:
                isDefault = False
                break

        if updateOutput:
            self.updateOutputBasedOnDefaultConfig(cm, isDefault)

        return isDefault

    # TODO put sector logic in here
    def updateOutputBasedOnDefaultConfig(self, cm, isDefault):
        starredName = "{CM}*".format(CM=cm)

        if isDefault:
            if starredName in self.selectedDisplayCMs:
                self.selectedDisplayCMs.add(cm)
                self.selectedDisplayCMs.discard(starredName)
        else:
            if cm in self.selectedDisplayCMs:
                self.selectedDisplayCMs.discard(cm)
                self.selectedDisplayCMs.add(starredName)

        self.updateSelectedText()

    @Slot()
    def openAmplitudeWindow(self, cm, sector):
        # type: (str, int) -> None

        # Put this here to speed things up (it was suuuuuuuper slow trying to do
        # this on startup)
        if cm not in self.buttonDisplays:
            displayCM = Display(ui_filename=self.pathToAmplitudeWindow,
                                macros={"cm": cm,
                                        "area": "L{SECTOR}B".format(SECTOR=sector)})
            repeater = displayCM.ui.cavityRepeater

            lineEdits = []
            groupBoxes = []

            for cav in range(8):
                item = repeater.layout().itemAt(cav)  # type: QWidgetItem
                displayCav = item.widget()  # type: Display

                lineEdit = displayCav.ui.desiredAmplitude  # type: QLineEdit
                lineEdit.textChanged.connect(partial(self.checkIfAllCavitiesAtDefault, cm))
                lineEdits.append(lineEdit)

                groupBox = displayCav.ui.cavityGroupbox  # type: QGroupBox
                groupBox.toggled.connect(partial(self.cavityToggled, cm))
                groupBoxes.append(groupBox)

                restoreDefaultButton = displayCav.ui.restoreDefaultButton  # type: QPushButton
                restoreDefaultButton.clicked.connect(partial(self.restoreDefault,
                                                             lineEdit))

            self.desiredCavAmps[cm] = lineEdits
            self.buttonDisplays[cm] = displayCM
            self.cavityGroupBoxes[cm] = groupBoxes

            selectAllCheckbox = displayCM.ui.selectAllCheckbox  # type: QCheckBox
            self.selectAllCavitiesCheckboxes[cm] = selectAllCheckbox
            selectAllCheckbox.stateChanged.connect(partial(self.selectAllCavitiesToggled,
                                                           cm))

        self.buttonDisplays[cm].show()

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
    # TODO use ops max
    def restoreDefault(self, desiredAmplitude):
        # type: (QLineEdit) -> None

        desiredAmplitude.setText(str(DEFAULT_AMPLITUDE))

    def updateSelectedText(self):
        self.ui.selectionOutput.setText(str(sorted(self.selectedDisplayCMs))
                                        .replace("'", "").replace("[", "")
                                        .replace("]", ""))

    @Slot()
    # TODO check if all checkboxes in sector are clicked
    def cryomoduleCheckboxToggled(self, cryomodule):
        # type:  (str) -> None

        checkbox = self.cryomoduleCheckboxes[cryomodule]
        button = self.cryomoduleButtons[cryomodule]

        sector = self.checkboxSectorMap[checkbox]
        selectAllCheckbox = self.selectAllCheckboxes[sector]

        state = selectAllCheckbox.checkState()
        sectorLabel = "L{SECTOR}B*".format(SECTOR=sector)

        if checkbox.isChecked():
            self._selectedFullCMs.add(cryomodule)
            button.setEnabled(True)

            isDefault = (self.checkIfAllCavitiesAtDefault(cryomodule, False)
                         if cryomodule in self.desiredCavAmps else True)

            if state == 0:
                selectAllCheckbox.setCheckState(1)

            elif state == 1:
                self.selectedDisplayCMs.add(cryomodule if isDefault
                                            else cryomodule + "*")

            else:
                if not isDefault:
                    self.selectedDisplayCMs.add(sectorLabel)

        else:
            self._selectedFullCMs.discard(cryomodule)
            button.setEnabled(False)

            if state == 2:
                selectAllCheckbox.setCheckState(1)
                self.selectedDisplayCMs.discard(sectorLabel)
                self.selectedDisplayCMs.discard("L{SECTOR}B".format(SECTOR=sector))

                for sectorCryomodule in self.sectors[sector]:
                    isDefault = (self.checkIfAllCavitiesAtDefault(sectorCryomodule, False)
                                 if sectorCryomodule in self.desiredCavAmps
                                 else True)
                    self.selectedDisplayCMs.add(sectorCryomodule
                                                if isDefault
                                                else (sectorCryomodule + "*"))

            self.selectedDisplayCMs.discard(cryomodule + "*")
            self.selectedDisplayCMs.discard(cryomodule)

        self.updateSelectedText()

    @Slot()
    def selectAllSectorCryomodules(self, selectAllCheckbox, sector):
        # type:  (QCheckBox, int) -> None

        sectorLabel = "L{SECTOR}B".format(SECTOR=sector)

        if selectAllCheckbox.checkState() == 2:

            for name in self.sectors[sector]:
                self.cryomoduleCheckboxes[name].setChecked(True)

            if (sectorLabel + "*") not in self.selectedDisplayCMs:
                self.selectedDisplayCMs.add(sectorLabel)

        elif selectAllCheckbox.checkState() == 0:
            for name in self.sectors[sector]:
                self.cryomoduleCheckboxes[name].setChecked(False)

            self.selectedDisplayCMs.discard(sectorLabel)
            self.selectedDisplayCMs.discard(sectorLabel + "*")

        self.updateSelectedText()
