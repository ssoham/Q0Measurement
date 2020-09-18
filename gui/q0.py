from pydm import Display
from PyQt5 import QtGui
from PyQt5.QtWidgets import QWidgetItem, QCheckBox, QPushButton, QLineEdit
from os import path
from qtpy.QtCore import Slot
from pydm.widgets.template_repeater import PyDMTemplateRepeater
from pydm.widgets.related_display_button import PyDMRelatedDisplayButton
from typing import List, Dict
from functools import partial
import sys

DEFAULT_AMPLITUDE = 16.608

sys.path.insert(0, '..')


class Q0Measurement(Display):
    def __init__(self, parent=None, args=[], ui_filename="q0.ui"):
        super(Q0Measurement, self).__init__(parent=parent, args=args,
                                            ui_filename=ui_filename)

        pathHere = path.dirname(sys.modules[self.__module__].__file__)
        pathToSelectionWindow = path.join(pathHere, "cmSelection.ui")
        self.selectWindow = Display(ui_filename=pathToSelectionWindow)
        self.ui.cmSelectButton.clicked.connect(self.openSelectWindow)

        self.pathToAmplitudeWindow = path.join(pathHere, "amplitude.ui")

        self.checkboxes = {}  # type: Dict[str, QCheckBox]
        self.buttons = {}  # type: Dict[str, PyDMRelatedDisplayButton]
        self.checkboxButtonMap = {}  # type: Dict[QCheckBox, QPushButton]
        self.desiredCavAmps = {}  # type: Dict[str, List[QLineEdit]]
        self.buttonDisplays = {}  # type: Dict[str, Display]

        self.selectedDisplayCMs = set()
        self.selectedFullCMs = set()

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
            checkbox.stateChanged.connect(partial(self.selectAll, checkbox, sector))

        repeaters = [self.selectWindow.ui.cryomodulesL0B,
                     self.selectWindow.ui.cryomodulesL1B,
                     self.selectWindow.ui.cryomodulesL2B,
                     self.selectWindow.ui.cryomodulesL3B]  # type: List[PyDMTemplateRepeater]

        for sector, repeater in enumerate(repeaters):
            num = repeater.count()

            for i in range(num):
                item = repeater.layout().itemAt(i)  # type: QWidgetItem

                button = item.widget().layout().itemAt(1).widget()  # type: QPushButton
                name = button.text().split()[1]
                self.buttons[name] = button
                button.clicked.connect(partial(self.openAmplitudeWindow, name,
                                               sector))

                checkbox = item.widget().layout().itemAt(0).widget()  # type: QCheckBox
                self.checkboxes[name] = checkbox

                self.checkboxButtonMap[checkbox] = button

                checkbox.stateChanged.connect(partial(self.checkboxToggled, checkbox))

                self.sectors[sector].append(name)
                self.checkboxSectorMap[checkbox] = sector

    @Slot()
    def openSelectWindow(self):
        self.selectWindow.show()

    @Slot()
    # TODO use AMAX/ops limit
    def checkForDefaultAmp(self, sector, cm, cav, lineEdit):
        # type: (int, str, int, QLineEdit) -> None

        if lineEdit.text():
            try:
                amplitude = float(lineEdit.text())
                starredName = "{CM}*".format(CM=cm)
                if amplitude != DEFAULT_AMPLITUDE:
                    if cm in self.selectedDisplayCMs:
                        self.selectedDisplayCMs.discard(cm)
                        self.selectedDisplayCMs.add(starredName)
                else:
                    if starredName in self.selectedDisplayCMs:
                        self.selectedDisplayCMs.add(cm)
                        self.selectedDisplayCMs.discard(starredName)

                self.updateSelectedText()

            except ValueError:
                lineEdit.clear()
        else:
            if cm in self.selectedDisplayCMs:
                self.selectedDisplayCMs.discard(cm)
                self.selectedDisplayCMs.add("{CM}*".format(CM=cm))
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

            for cav in range(8):
                item = repeater.layout().itemAt(cav)  # type: QWidgetItem
                displayCav = item.widget()  # type: Display
                lineEdit = displayCav.ui.desiredAmplitude  # type: QLineEdit
                lineEdit.textChanged.connect(partial(self.checkForDefaultAmp,
                                                     sector, cm, cav, lineEdit))
                lineEdits.append(lineEdit)

            self.desiredCavAmps[cm] = lineEdits
            self.buttonDisplays[cm] = displayCM

        self.buttonDisplays[cm].show()

    def updateSelectedText(self):
        self.ui.selectionOutput.setText(str(sorted(self.selectedDisplayCMs))
                                        .replace("'", "").replace("[", "")
                                        .replace("]", ""))
        # print(self.selectedFullCMs)

    # Tried to pass in name and checkbox as part of a lambda function, but that
    # didn't work. Very inelegant, so looking for suggestions here
    def checkboxToggled(self, checkbox):
        # type:  (QCheckBox) -> None

        # for checkbox in self.checkboxes.values():

        button = self.checkboxButtonMap[checkbox]
        cm = button.text().split()[1]

        sector = self.checkboxSectorMap[checkbox]
        selectAllCheckbox = self.selectAllCheckboxes[sector]

        if checkbox.isChecked():
            self.selectedDisplayCMs.add(cm)
            self.selectedFullCMs.add(cm)

            if cm in self.desiredCavAmps:
                for cav, lineEdit in enumerate(self.desiredCavAmps[cm]):
                    self.checkForDefaultAmp(lineEdit=lineEdit, cm=cm,
                                            sector=None, cav=cav)

            button.setEnabled(True)

        else:
            self.selectedFullCMs.discard(cm)
            button.setEnabled(False)

            sectorLabel = "L{SECTOR}B".format(SECTOR=sector)
            if sectorLabel in self.selectedDisplayCMs:
                self.selectedDisplayCMs.discard(sectorLabel)

                for label in self.sectors[sector]:
                    self.selectedDisplayCMs.add(label)

            self.selectedDisplayCMs.discard(cm + "*")
            self.selectedDisplayCMs.discard(cm)

        selectAllCheckbox.setCheckState(1)

        self.updateSelectedText()

    def selectAll(self, selectAllCheckbox, sector):
        # type:  (QCheckBox, int) -> None

        # for sector, selectAllCheckbox in enumerate(self.selectAllCheckboxes):

        if selectAllCheckbox.checkState() == 2:

            for name in self.sectors[sector]:
                self.checkboxes[name].setChecked(True)

            for name in self.sectors[sector]:
                self.selectedDisplayCMs.discard(name)

            selectAllCheckbox.setCheckState(2)
            self.selectedDisplayCMs.add("L{SECTOR}B".format(SECTOR=sector))

        elif selectAllCheckbox.checkState() == 0:
            for name in self.sectors[sector]:
                self.checkboxes[name].setChecked(False)

            selectAllCheckbox.setCheckState(0)

            self.selectedDisplayCMs.discard("L{SECTOR}B".format(SECTOR=sector))

        # else:

        self.updateSelectedText()
