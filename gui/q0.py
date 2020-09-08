from pydm import Display
from PyQt5 import QtGui
from PyQt5.QtWidgets import QWidgetItem, QCheckBox
from os import path
from sys import modules
from qtpy.QtCore import Slot
from pydm.widgets.template_repeater import PyDMTemplateRepeater
from pydm.widgets.related_display_button import PyDMRelatedDisplayButton
from typing import List, Dict


class Q0Measurement(Display):
    def __init__(self, parent=None, args=[], ui_filename="q0.ui"):
        super(Q0Measurement, self).__init__(parent=parent, args=args,
                                            ui_filename=ui_filename)

        pathHere = path.dirname(modules[self.__module__].__file__)
        pathToWindow = path.join(pathHere, "cmSelection.ui")
        self.selectWindow = Display(ui_filename=pathToWindow)
        self.ui.cmSelectButton.clicked.connect(self.openSelectWindow)

        self.checkboxes = {}  # type: Dict[str, QCheckBox]
        self.buttons = {}  # type: Dict[str, PyDMRelatedDisplayButton]
        self.checkboxButtonMap = {}  # type: Dict[QCheckBox, PyDMRelatedDisplayButton]
        self.selectedCMs = set()

        self.populateCheckboxes()

    # These nested methods have HIGH deprecation potential...
    def populateCheckboxes(self):
        repeaters = [self.selectWindow.ui.cryomodulesL0B,
                     self.selectWindow.ui.cryomodulesL1B,
                     self.selectWindow.ui.cryomodulesL2B,
                     self.selectWindow.ui.cryomodulesL3B]  # type: List[PyDMTemplateRepeater]

        for repeater in repeaters:
            num = repeater.count()

            for i in range(num):
                item = repeater.layout().itemAt(i)  # type: QWidgetItem

                button = item.widget().layout().itemAt(1).widget()
                name = button.text().split()[1]
                self.buttons[name] = button

                checkbox = item.widget().layout().itemAt(0).widget()  # type: QCheckBox
                self.checkboxes[name] = checkbox

                self.checkboxButtonMap[checkbox] = button

                checkbox.stateChanged.connect(self.updateCmList)

    @Slot()
    def openSelectWindow(self):
        self.selectWindow.show()

    # Tried to pass in name and checkbox as part of a lambda function, but that
    # didn't work. Very inelegant, so looking for suggestions here
    def updateCmList(self):

        for checkbox in self.checkboxes.values():

            name = self.checkboxButtonMap[checkbox].text().split()[1]

            if checkbox.isChecked():
                self.selectedCMs.add(name)
            else:
                self.selectedCMs.discard(name)

        self.ui.selectionOutput.setText(str(sorted(self.selectedCMs)))
