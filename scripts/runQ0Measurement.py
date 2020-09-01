################################################################################
# A script that runs a Q0 measurement for a given Cavity object. This consists
# of some initial RF setup followed by a 40 minute RF run and a 40 minute
# heater calibration run. The script takes 2-4 hours to run depending on the
# initial conditions.
# Author: Lisa Zacarias
################################################################################

from __future__ import print_function, division
from container import Cryomodule


if __name__ == "__main__":
    # noinspection PyUnboundLocalVariable

    cavity = (Cryomodule(int(input("SLAC CM: ")),
                         int(input("JLAB CM: ")))
              .cavities[int(input("Cavity: "))])

    cavity.runQ0Meas(float(input("Desired Gradient: ")))
