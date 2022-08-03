# Q0 Measurement Using dLL/dt (Rate of Change of Liquid Helium Level) #

## Overview ##
We're indirectly measuring the Q0 by figuring out how much heat a cavity is
generating at a given gradient. We figure out that heat load by looking at how
quickly the helium evaporates inside the cryomodule (more heat, faster 
evaporation).

Note that the relationship between the liquid level sensor readback and the
volume of LHE held in the cryomodule is not consistently linear. This is due to
the interior shape of the helium vessel. Imagine you were pouring liquid into
the following vessel at a constant rate:

```
     ----------------       D
    |                |
    |~^~^~^~^~^~^~^~^|
     ----        ----       C
         |      |
         |      |
     ----        ----       B
    |                |
    |                |
     ----------------       A
    
```
If there were a level sensor in the vessel, it would increase
at one rate in the [A,B] and [C,D] regions and at another (higher) rate in the
[B,C] region. 

We only take data with the downstream liquid level sensor
reading 90-95% in our calculations. Everything is nice and linear in this 
region, just as it would be in region [C,D] (crossing 90 is analogous to 
crossing into region [B,C])

### Cryomodule Calibration ###
1) Scan the past 24 hours to see if there's a 1.5 hour chunk of time when the
liquid level was stable. The idea is to find the JT valve position that lets helium
in at the same rate at which it's being boiled off by the static heat on the cryomodule
(where the static heat is the heat leaking into the cryomodule from the outside
world plus a contribution from the electric heaters at their default settings).
After locking the valve at this setting we know that any change in liquid level
is due \*only* to the heat load that we've added.

    1) If we find a stable period, it finds the average JT valve position
    over that time span.
    
    2) If not, we prompt the user to ask cryo to fill to 95% on the downstream 
    sensor and wait 1.75 hours for the liquid level to stabilize, then average 
    the JT position over the last 30 minutes.
 
2) Prompt the user to ask the cryo group to lock the JT Valve at the position 
found in step 1.

3) Increase the heat load on the cryomodule by INITIAL_CAL_HEAT_LOAD using the heaters 
(distributed evenly across them)

4) Wait for the liquid level to drop by TARGET_LL_DIFF% (The amount we've experimentally determined
is necessary for a good linear fit).

5) Prompt the user to ask cryo to refill to 95% if the current liquid level is below
94%.

6) Repeat steps 2 through 5 NUM_CAL_STEPS more times, except with a heat load increment of CAL_HEATER_DELTA
per heater.
    
### Cavity Q0 Measurement ###
1) Determine a new JT Valve position if necessary (using the same method as in
Calibration step 1)

2) Check that the downstream liquid level is above 94% and that the valve is
locked to the correct value

1) Make sure that all the waveform acquisition controls are enabled/at the
correct values
    
3) Turn all SSAs on

4) Characterize all the cavities

4) Turn the RF on in pulsed mode

5) Check that the On Time is 70ms (and sets it if not)

6) Increase the drive until it's at least 15% OR the gradient is at least 1 MV/m

7) Phase the cavities by getting the "valley" of the reverse waveform as close
to 0 as possible 

8) Go to CW mode

9) Walk the gradient up to the requested value (usually GMAX) and hold it
there until the liquid level drops TARGET_LL_DIFF% (with some quench detection built in that
triggers an abort)

10) Power down the RF

11) Launch a heater run to be used for error correction during analysis (in
order to find an offset for the RF heat load)
    1) Increases each heater by FULL_MODULE_CALIBRATION_LOAD/8 
    2) Holds until the downstream liquid level drops by TARGET_LL_DIFF%
    3) Decreases each heater by FULL_MODULE_CALIBRATION_LOAD/8
    
    
#### Calculation ####
After pulling all the required data, the script parses it into data runs based 
on heater and/or RF settings.

For the calibration, it fits the NUM_CAL_STEPS data points to a line (heat load vs. dLL/dt).

For the Q0 measurement, it:

1) Fits the liquid level to a line and finds that dLL/dt for both the RF and 
heater runs

2) Plugs the dLL/dt from the heater run into the calibration curve to
get a heat load
    
    - If that back-calculated heat load is not equal to the heat added to the 
    heaters during the run, it finds that offset

3) Plugs the dLL/dt from the RF run into the calibration curve to
get a heat load

4) Adjusts the RF heat load by the amount determined in step 2

5) Plugs that adjusted RF heat load into our Q0 formula corrected for helium pressure