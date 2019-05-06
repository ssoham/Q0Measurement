# JLab Q0 Measurement Using dLL/dt (Rate of Change of Liquid Helium Level) #

## Overview ##
We're indirectly measuring the Q0 by figuring out how much heat a cavity is
generating at a given gradient. We figure out that heat load by looking at how
quickly the helium evaporates inside the cryomodule (more heat, faster 
evaporation).

### Calibration ###
We make a calibration curve that maps heat load to dLL/dt (change in liquid
level over time)
  
1) Look at the past couple of hours and see if the liquid level has been
stable (the idea is to find the JT valve position that neither leaks nor adds
helium, so that any change in liquid level is due *only* to the heat load)

    1) If so, find the average JT valve position over that time
    
    2) If not, ask cryo to fill to 95% on the downstream sensor and wait for the
    liquid level to stabilize (at least 1.5 hours), then average the JT position
    over the last 5-10 minutes (dealer's choice of whatever looks the most flat)
 
2) Ask cryo to lock the JT Valve at the position found in step 1

3) Using the heaters, increase the heat load on the cryomodule by 13 W.
Distribute that heat across the heaters as evenly as possible

4) Wait for 40 minutes.

5) Ask cryo to refill to 95%

6) Repeat steps 2 through 5 with 10, 7, 4, and 1 W from the heaters.
    - Note that you might be able to skip step 5 if you think that the liquid
    level won't dip below 90% during the next heater run (where the behavior is
    no longer linear)
    
### Q0 Measurement ###
Run the handy dandy script! But as to what the script does per cavity:

1) Makes sure that all the waveform acquisition controls are enabled/at the
correct values

2) Checks that the downstream liquid level is at 95% and that the valve is
locked
    1) Note that manual mode locking isn't detected yet, so it won't wait for
    that
    2) Also note that it doesn't check that the locked value is CORRECT in 
    automatic mode, just that it's locked
    
3) Turns the SSA on

4) Turns the RF on in pulsed

5) Checks that the On Time in 70ms (and sets it if not)

6) Increases the drive until it's at least 15 OR the gradient is at least 1 MV/m

7) Phases the cavity by getting the "valley" of the reverse waveform as close
to 0 as possible 

8) Goes to CW

9) Walks the gradient up to the requested gradient (usually 16) and holds it
there for 40 minutes n (with some rudimentary quench detection built in that 
triggers an abort)

10) Powers down the RF

11) Launches a heater run for normalization (in order to find an offset for the 
RF heat load later)
    1) Increases each heater by 1 W
    2) Holds for 40 minutes or until the downstream level dips below 90
    3) Decreases each heater by 1 W
    
### Analysis ###

#### Input ####

A TSV file (for human readability)
- Row[0] is cryomodule metadata (SLAC number, JLAB number, Electric
 Heat Load, JT Valve Position, Start Time, End Time).
    - EX: 12	2	16	24	3-27-2019-11-00	3-27-2019-16-00

- Rows[1:n] are cavity metadata (cavity number, gradient, valve position, start
time, end time, electric heat load)
    - EX: 1	16	26	3-28-2019-14-30	3-28-2019-15-10 16

#### Calculation ####
1) Using the information from the metadata TSV, it either generates a CSV with
the archive data from that time period, or it uses a previously 
generated CSV.

2) It parses that data into data runs based on heater and/or RF settings

    - For the calibration, it fits the 5 data points (one per heater setting) 
    to a line (heat load vs dLL)
    - For the Q0 measurement:
        - It fits the liquid level to a line and finds that dLL/dt
        - For the heater run, it plugs that dLL/dt into the calibration curve to
        get a heat load. If that back-calculated heat load is not equal to the 
        heat put on the heaters, find that offset
        - For the RF run, it does the same thing and adjusts the heat load by 
        the amount determined from the heater run
        - It plugs the RF heat load into a magic formula to calculate Q0!