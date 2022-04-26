from container import Cryomodule
from q0Utils import cagetPV, caputPV

TARGET = 10

if __name__ == "__main__":

    module = Cryomodule(16, 2)

    heaterPVs = module.heaterDesPVs
    ogHeaterVals = []

    for pv in heaterPVs:
        ogHeaterVals.append(cagetPV(pv))
        caputPV(pv, str(TARGET))

#    raw_input("Heaters at " + str(TARGET) + "; press enter to restore... ")

#    for i, pv in enumerate(heaterPVs):
#        caputPV(pv, ogHeaterVals[i])
