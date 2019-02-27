# JLab Helium Level Rate of Change Analysis

A simple script that takes MyaPlot (Archiver) CSV data and parses it to
generate a dLL vs Heater Power correlation (Final results in the PNG's).
It takes in the helium level data, detects when the heater changes value,
filters out wonky points, and figures out the slope per heater run.
