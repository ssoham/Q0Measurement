from abc import ABCMeta, abstractmethod


class DataRun(object):
    __metaclass__ = ABCMeta

    def __init__(self, runStartIdx, runEndIdx, dataSession, num):
        # type: (int, int, DataSession, int) -> None

        self.dataSession = dataSession
        self.num = num

        # startIdx and endIdx define the beginning and the end of this data run
        # within the cryomodule or cavity's data buffers
        self.startIdx = runStartIdx
        self.endIdx = runEndIdx

        self.elecHeatLoadDes = (dataSession.totalHeaterSetpointBuffer[runStartIdx]
                                - dataSession.valveParams.refHeatLoadDes)

        runElecHeatActBuff = self.dataSession.totalHeaterReadbackBuffer[self.startIdx:
                                                                        self.endIdx]

        self.heatActDelta = (mean(runElecHeatActBuff)
                             - self.dataSession.valveParams.refHeatLoadAct)

        # All data runs have liquid level information which gets fitted with a
        # line (giving us dLL/dt). The slope and intercept parametrize the line.
        self.slope = None
        self.intercept = None

        # A dictionary with some diagnostic information that only gets printed
        # if we're in test mode
        self.diagnostics = {}

    @property
    @abstractmethod
    def name(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def adjustedTotalHeatLoad(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def label(self):
        raise NotImplementedError

    @abstractmethod
    def printRunReport(self):
        raise NotImplementedError

    # noinspection PyTypeChecker
    @property
    def elecHeatLoadAct(self):
        # type: () -> float
        return self.heatActDelta

    @property
    def data(self):
        # type: () -> List[float]
        return self.dataSession.downstreamLiquidLevelBuffer[self.startIdx:self.endIdx]

    @property
    def timeStamps(self):
        # type: () -> List[float]
        return self.dataSession.unixTimeBuff[self.startIdx:self.endIdx]

    @property
    def timeEnvelope(self):
        start = datetime.fromtimestamp(self.timeStamps[0]).strftime('%m/%d/%Y %H:%M')
        end = datetime.fromtimestamp(self.timeStamps[-1]).strftime('%H:%M')
        return "{START} to {END}".format(START=start, END=end)

    def genElecLabel(self):
        # type: () -> str
        labelStr = "{slope} %/s @ {heatLoad} W Electric Load [{TIME}]"
        return labelStr.format(slope='%.2E' % Decimal(self.slope),
                               heatLoad=round(self.elecHeatLoadAct, 2),
                               TIME=self.timeEnvelope)

    def process(self):
        # type: () -> None
        # noinspection PyTupleAssignmentBalance
        self.slope, self.intercept, r_val, p_val, std_err = linregress(
                self.timeStamps, self.data)

        self.diagnostics["R^2"] = r_val ** 2

        startTime = self.dataSession.unixTimeBuff[self.startIdx]
        endTime = self.dataSession.unixTimeBuff[self.endIdx]
        self.diagnostics["Duration"] = ((endTime - startTime) / 60.0)

    def printDiagnostics(self):
        # type: () -> None

        print("            Cutoff: {CUT}"
              .format(CUT=self.diagnostics["Cutoff"]))

        print("          Duration: {DUR}"
              .format(DUR=round(self.diagnostics["Duration"], 4)))

        # Print R^2 for the run's fit line to diagnose whether or not it was
        # long enough
        print("               R^2: {R2}\n"
              .format(R2=round(self.diagnostics["R^2"], 4)))


class HeaterDataRun(DataRun):

    def __init__(self, runStartIdx, runEndIdx, dataSession, num):
        # type: (int, int, DataSession, int) -> None

        super(HeaterDataRun, self).__init__(runStartIdx, runEndIdx, dataSession,
                                            num)
        self.dataSession = dataSession

    @property
    def name(self):
        # type: () -> str
        return "Run {NUM} ({TYPE})".format(NUM=self.num, TYPE="heater")

    @property
    def adjustedTotalHeatLoad(self):
        # type: () -> float
        return self.elecHeatLoadAct

    # Heat error due to the position of the JT valve
    @property
    def heatAdjustment(self):
        # type: () -> float
        calcHeatLoad = (self.slope / self.dataSession.calibSlope)
        return self.elecHeatLoadAct - calcHeatLoad

    @property
    def elecHeatLoadActAdjusted(self):
        # type: () -> float
        return self.heatActDelta + self.dataSession.heatAdjustment

    @property
    def label(self):
        # type: () -> str
        return self.genElecLabel()

    def printRunReport(self):
        # type: () -> None

        print("   ------- Run {NUM} (Heater) -------\n".format(NUM=self.num))

        reportStr = "     Electric heat load: {ELEC} W\n"
        report = reportStr.format(ELEC=round(self.elecHeatLoadAct, 2))

        # print(report.format(Q0Val=None))
        print(report)

        # if TEST_MODE:
        #    self.printDiagnostics()
        self.printDiagnostics()


class RFDataRun(DataRun):

    def __init__(self, runStartIdx, runEndIdx, dataSession, num):
        # type: (int, int, Q0DataSession, int) -> None

        super(RFDataRun, self).__init__(runStartIdx, runEndIdx, dataSession,
                                        num)

        # Stores the average RF gradient for this run
        self.grad = None

        self._calculatedQ0 = None
        self.dataSession = dataSession

    @property
    def name(self):
        # type: () -> str
        return "Run {NUM} ({TYPE})".format(NUM=self.num, TYPE="RF")

    # Each Q0 measurement run has a total heat load value. If it is an RF run
    # we calculate the heat load by projecting the run's dLL/dt on the
    # cryomodule's heater calibration curve. If it is a heater run we just
    # return the electric heat load.
    @property
    def adjustedTotalHeatLoad(self):
        # type: () -> float
        return ((self.slope / self.dataSession.calibSlope)
                + self.dataSession.avgHeatAdjustment)

    # The RF heat load is equal to the total heat load minus the electric
    # heat load.
    @property
    def rfHeatLoad(self):
        # type: () -> float
        return self.adjustedTotalHeatLoad - self.elecHeatLoadAct

    @property
    def q0(self):
        # type: () -> float

        if not self._calculatedQ0:
            q0s = []
            numInvalidGrads = 0
            calcFile = "calculations/cm{NUM}/cav{CAV}.csv".format(NUM=self.dataSession.container.cryModNumSLAC,
                                                                  CAV=self.dataSession.container.cavNum)

            compatibleMkdirs(calcFile)
            with open(calcFile, "w+") as f:
                csvWriter = writer(f, delimiter=',')
                csvWriter.writerow(["Gradient", "RF Heat Load", "Pressure",
                                    "Q0"])

                for idx in range(self.startIdx, self.endIdx):
                    if isinstance(self.dataSession.container, Cavity):
                        archiveGrad = self.dataSession.totalGradientBuffer[idx]
                    else:
                        archiveGrad = sqrt(self.dataSession.totalGradientBuffer[idx])

                    if archiveGrad:
                        q0s.append(self.calcQ0(archiveGrad, self.rfHeatLoad,
                                               self.dataSession.dsPressBuff[idx]))
                        csvWriter.writerow([archiveGrad, self.rfHeatLoad,
                                            self.dataSession.dsPressBuff[idx], q0s[-1]])

                    # Sometimes the archiver messes up and records 0 for some
                    # reason. We use the reference desired value as an approximation
                    else:
                        numInvalidGrads += 1
                        q0s.append(self.calcQ0(self.dataSession.refGradVal,
                                               self.rfHeatLoad,
                                               self.dataSession.dsPressBuff[idx]))
                        csvWriter.writerow([self.dataSession.refGradVal, self.rfHeatLoad,
                                            self.dataSession.dsPressBuff[idx], q0s[-1]])

                if numInvalidGrads:
                    writeAndFlushStdErr("\nGradient buffer had {NUM} invalid points"
                                        " (used reference gradient value instead) "
                                        "- Consider refetching the data from the "
                                        "archiver\n"
                                        .format(NUM=numInvalidGrads))

            self._calculatedQ0 = float(mean(q0s))

        return self._calculatedQ0

    @property
    def label(self):
        # type: () -> str

        labelStr = "{slope} %/s @ {grad} MV/m\nCalculated Q0: {Q0}"
        q0Str = '{:.2e}'.format(Decimal(self.q0))

        return labelStr.format(slope='%.2E' % Decimal(self.slope),
                               grad=self.dataSession.refGradVal, Q0=q0Str)

    def printRunReport(self):
        # type: () -> None

        print("    --------- Run {NUM} (RF) ---------\n".format(NUM=self.num))

        reportStr = ("      Avg Pressure: {PRES} Torr\n"
                     "       RF Gradient: {GRAD} MV/m\n"
                     "      RF heat load: {RFHEAT} W\n"
                     "   Heat Adjustment: {ADJUST} W\n"
                     "     Calculated Q0: {Q0Val}\n")

        avgPress = mean(self.dataSession.dsPressBuff[self.startIdx:self.endIdx])

        gradVals = self.dataSession.totalGradientBuffer[self.startIdx:self.endIdx]
        rmsGrad = sqrt(sum(g ** 2 for g in gradVals)
                       / (self.endIdx - self.startIdx))

        heatAdjust = self.dataSession.avgHeatAdjustment

        Q0 = '{:.2e}'.format(Decimal(self.q0))

        # noinspection PyTypeChecker
        report = reportStr.format(PRES=round(avgPress, 2),
                                  GRAD=round(rmsGrad, 2),
                                  RFHEAT=round(self.rfHeatLoad, 2),
                                  ADJUST=round(heatAdjust, 2),
                                  Q0Val=Q0)

        print(report)

        # if TEST_MODE:
        #    self.printDiagnostics()
        self.printDiagnostics()

    # The calculated Q0 value for this run. Magical formula from Mike Drury
    # (drury@jlab.org) to calculate Q0 from the measured heat load on a cavity,
    # the RF gradient used during the test, and the pressure of the incoming
    # 2 K helium.
    @staticmethod
    def calcQ0(amplitude, rfHeatLoad, avgPressure):
        # type: (float, float, float) -> float
        # The initial Q0 calculation doesn't account for the temperature
        # variation of the 2 K helium
        rUponQ = 1012

        uncorrectedQ0 = (((amplitude * 1000000) ** 2)
                         / (rUponQ * rfHeatLoad))

        # uncorrectedQ0 = ((grad * 1000000) ** 2) / (939.3 * rfHeatLoad)

        # We can correct Q0 for the helium temperature!
        tempFromPress = (avgPressure * 0.0125) + 1.705

        C1 = 271
        C2 = 0.0000726
        C3 = 0.00000214
        C4 = amplitude - 0.7
        C5 = 0.000000043
        C6 = -17.02
        C7 = C2 - (C3 * C4) + (C5 * (C4 ** 2))

        return (C1 / ((C7 / 2) * exp(C6 / 2) + C1 / uncorrectedQ0
                      - (C7 / tempFromPress) * exp(C6 / tempFromPress)))
