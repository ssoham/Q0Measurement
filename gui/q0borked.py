from pydm import Display
from PyQt5 import QtGui
from os import path
from sys import modules
from qtpy.QtCore import Slot

class Q0(Display):

    def __init__(self, parent=None, args=None):
        super(Q0, self).__init__(parent=parent, args=args)
        
        #pathHere = path.dirname(modules[self.__module__].__file__)
        #pathToWindow = path.join(pathHere, "cmSelection.ui")
        #self.selectWindow = Display(ui_filename=pathToWindow)
        
        #childWidgets = self.selectWindow.ui.cryomodulesL0B.children()
        
        #for childWidget in childWidgets:
        #    if not isinstance(childWidget, Display):
        #        pass
        #    print("child: {CHILD}".format(CHILD=childWidget))
        #    grandchildren = childWidget.children()
        #    for grandchild in grandchildren:
        #        print("\tgrandchild: {GRANDCHILD}".format(GRANDCHILD=grandchild))

        #print(dir(self.ui.cmSelectorButton))
        #print(self.ui.embedded.embedded_widget)
        #self.ui.cmSelectButton.clicked.connect(self.openSelectWindow)

    def ui_filename(self):
        pathHere = path.dirname(modules[self.__module__].__file__)
        return path.join(pathHere, "q0.ui")
        
    #@Slot()
    #def openSelectWindow(self):
    #    self.selectWindow.show()
    
    def updateCmList(self):
        self.ui.selectionOutput.setText("updated")

