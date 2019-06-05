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
Run the handy dandy script! It makes a calibration curve that maps heat load to 
dLL/dt (change in liquid level over time) for the cryomodule in question. 
But as to what the script actually does:
  
1) Looks at the past couple of hours to see if the liquid level has been
stable. The idea is to find the JT valve position that lets helium in at the
same rate at which it's being boiled off by the static heat on the cryomodule
(where the static heat is the heat leaking into the cryomodule from the outside
world plus a contribution from the electric heaters at their default settings).
After locking the valve at this setting we know that any change in liquid level
is due \*only* to the heat load that we've added.

    1) If the liquid level has been stable, finds the average JT valve position
    over that time span.
    
    2) If not, prompts the user to ask cryo to fill to 95% on the downstream 
    sensor and wait 1.75 hours for the liquid level to stabilize, then average 
    the JT position over the last 15 minutes.
 
2) Prompts the user to ask the cryo group to lock the JT Valve at the position 
found in step 1.

3) Increases the heat load on the cryomodule by 8 W using the heaters 
(distributed evenly across them)

4) Waits for 40 minutes or until the liquid level falls below 90%.

5) Prompts the user to ask cryo to refill to 95%.

6) Repeats steps 2 through 5 with 16, 24, 32, and 40 W from the heaters.
    
### Cavity Q0 Measurement ###
Run the other handy dandy script! But as to what the script does per cavity:
1) Determines a new JT Valve position if necessary (using the same method as in
Calibration step 1)

2) Checks that the downstream liquid level is at 95% and that the valve is
locked to the correct value

1) Makes sure that all the waveform acquisition controls are enabled/at the
correct values
    
3) Turns the SSA on

4) Characterizes the cavity

4) Turns the RF on in pulsed mode

5) Checks that the On Time in 70ms (and sets it if not)

6) Increases the drive until it's at least 15 OR the gradient is at least 1 MV/m

7) Phases the cavity by getting the "valley" of the reverse waveform as close
to 0 as possible 

8) Goes to CW mode

9) Walks the gradient up to the requested value (usually 16 MV/m) and holds it
there for 40 minutes or until the liquid level dips below 90% (with some quench
detection built in that triggers an abort)

10) Powers down the RF

11) Launches a heater run to be used for error correction during analysis (in
order to find an offset for the RF heat load)
    1) Increases each heater by 3 W
    2) Holds for 40 minutes or until the downstream liquid level dips below 90%
    3) Decreases each heater by 3 W
    
### Analysis ###

#### Input ####

A CSV file (input.csv) where each row follows the header format:

| SLAC Cryomodule Number | Cavity 1 Gradient | Cavity 2 Gradient | Cavity 3 Gradient | Cavity 4 Gradient | Cavity 5 Gradient | Cavity 6 Gradient | Cavity 7 Gradient | Cavity 8 Gradient |
|------------------------|-------------------|-------------------|-------------------|-------------------|-------------------|-------------------|-------------------|-------------------|

As currently written, the program reads \*every* row after the header and runs
a separate analysis on each. Per row, the script will:

1) Read the first cell to determine the desired cryomodule
2) Look through an internal record of previous calibrations and 
   present them as options, along with an option to run a brand new calibration
3) Analyze that data to generate a calibration curve (mapping dLL/dt to heat 
   load)
4) Iterate through the remaining cells, reading the desired gradient per 
   cavity (a blank cell will simply skip that cavity)
5) Look through an internal record of previous Q0 measurements for each desired
   cavity and present them as options, along with an option to run a brand new
   measurement
    
#### Calculation ####
After pulling all the required data, the script parses it into data runs based 
on heater and/or RF settings.

For the calibration, it fits the 5 data points (one per heater setting) 
to a line (heat load vs. dLL).

For the Q0 measurement, it:

1) Fits the liquid level to a line and finds that dLL/dt for both the RF and 
heater runs

2) Plugs the dLL/dt from the heater run into the calibration curve to
get a heat load
    
    - If that back-calculated heat load is not equal to the heat added to the 
    heaters during the run, finds that offset

3) Plugs the dLL/dt from the RF run into the calibration curve to
get a heat load

4) Adjusts the RF heat load by the amount determined in step 2

5) Plugs that adjusted RF heat load into a magic formula to calculate Q0!