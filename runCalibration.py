################################################################################
# A script that runs a heater calibration for a given Cryomodule object. This
# consists of five heater runs at different electric heat loads. The script
# should take ~4 hours to run.
# Authors: Lisa Zacarias, Ben Ripman
################################################################################

from __future__ import division
from container import Cryomodule


# Information about the acceptable heat load ranges for the LERF cryomodueles
# (not currently used)
# HEAT_LOAD_PARAMS = {2: {"low": 16, "high": 120}, 3: {"low": 64, "high": 120}}


if __name__ == "__main__":
    cryMod = Cryomodule(int(input("SLAC CM: ")), int(input("JLAB CM: ")))
    session, _ = cryMod.runCalibration()
