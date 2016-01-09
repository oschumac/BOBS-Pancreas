#andrewaps - a bolus-based APS using the OpenAPS toolset

#attempts to maintain stable blood glucose level in a T1D by delivering
#multiple small boluses via the standard Medtronic remote control, using the Easy Bolus function on the pump.
#does not take into account nor modify the basal rate

#establishes a maximum IOB which can only be exceeded with express agreement of the user
#in the absence of consent from the user, high glucose is managed by keeping IOB at the set maximum
#(via repeated delta bolus) until the glucose approaches normal

#by design, the insulin pump remains the lead system, and in the absence of communication from the APS,
#continues to operate normally.
#risks relating to over bolusing (hardware or software failure) or under bolusing (lack of communication)
#are mitigated respectively by Easy Bolus audible notifications, and by CGM alarms + Pump Suspend audible notifications & reminders - all of which are features of the pump

#TO DO:
#  - Notify user on persistent communication error
#  - Leave unused ports off all the time
#  - Make GlucoseHistory and Predict time-aware
#  - Create silent (vibrate) mode
#  - Begin work on message bus

import os
import sys
import urllib2
import subprocess
import time
import json
import RPi.GPIO as GPIO
import zmq
from collections import deque

#set up GPIO
GPIO.setmode(GPIO.BOARD)  #physical pin reference scheme
GPIO.setwarnings(False)   #avoid "already in use" warnings when GPIO.cleanup() does not have the opportunity to run

RemoteAct=11     #GPIO physical pin 11, connected to remote "Act" button
GPIO.setup(RemoteAct, GPIO.OUT)
GPIO.output(RemoteAct, GPIO.LOW)

RemoteBolus=13   #GPIO physical pin 13, connected to remote "Bolus" button
GPIO.setup(RemoteBolus, GPIO.OUT)
GPIO.output(RemoteBolus, GPIO.LOW)

RemoteSuspend=15 #GPIO physical pin 15, connected to remote "Suspend" button
GPIO.setup(RemoteSuspend, GPIO.OUT)
GPIO.output(RemoteSuspend, GPIO.LOW)

#set some global variables. most of these will be populated with real data in the main loop at bottom of the program
#all are set to initially to safe values.
MaxIOB=0.0           #float (gets populated later from max_iob.json)
IOB=0.0              #float (gets populated later from pump data)
DIA=0                #int (gets populated later from profile.json) (number of hours insulin remains active.)
TargetGlucose=140    #int (mg/dl for compatibility with OpenAPS toolset) (gets re-populated later from pump settings. for now set at a generic reasonably safe level.)
Glucose=0            #int (mg/dl for compatibility with OpenAPS toolset) (gets populated later from pump data)
GlucoseHistory=deque([0,0,0,0]) #list (mg/dl for compatibility with OpenAPS toolset) (gets populated later from pump data)
CorrectionFactor=0   #int (this many mg/dl are reduced by 1 unit of insulin) (gets populated later from pump settings - this is the P in a PID controller.)
Reservoir=300.0      #float  (gets populated later from pump data)
LogF="x"             #str (gets populated later by the InitLog() function)
ErrorCodeFile="./error-codes.json"
CorrectionFactorFile="./settings/insulin_sensitivities.json"
TargetGlucoseFile="./settings/bg_targets.json"    #the "high" glucose target from the pump settings is used as the target in this implementation
GlucosePredictFile="./predict.json"
WaitingThreshold=25  #int (mg/dl for compatibility with OpenAPS toolset)
                     #the loop runs every 15 minutes, giving each treatment time to start working between each loop. However two conditions trigger the loop to run every 5 minutes instead of 15:
                     #1- if the glucose changes more than 5 mg/dl since the last loop
                     #2- if current glucose is higher than (TargetGlucose + WaitingThreshold)
APSBatteryLow=False  #bln (will be triggered by GPIO when battery gets low)
ExerciseFactor=1     #int (will be increased when user reports he/she is exercising. this reduces the aggressiveness of the algorithm & therefore reduces insulin during exercise)
QuietMode=False      #bln (when true, the commands from the remote control are slowed down to take into account the time needed for the vibration motor on the pump.)
KeepAlive=True       #bln (when true, the USB ports are not shut down between loops)
UsePrediction=True   #bln (EXPERIMENTAL - when true, the algorithm will check actual results against predictions to detect meals or exercise & react. this is the D in a PID controller.)
MealDetectReponseFactor=2.2  #float (goes with UsePrediction. a number which can be changed to tune the algorithm's response to a meal. higher = more aggressive. this factor dampens glucose change speed in both directions, up and down. it is the D in a PID controller.)
LoopFrequency=300    #int (How many seconds between loops - used to calculate predicted change
LoopWaitTime=240     #int (roughly equivalent to LoopFrequency minus the time it takes for the loop to run. this is the actual time the loop will wait between runs.)
LoopsPerHour=3600/LoopFrequency
ContinueLooping=True #bln (yep. keep that up until I say False.)

#Next: Gain Scheduling  *** DANGER ZONE ***
#considering that the correction factor (amount of insulin needed to reduce by (x) mg/dl) is not linear, and that a higher proportion of insulin is needed at higher glucose values,
#the algorithm applies an AggressionFactor multiplier to the CorrectionFactor to tweak how aggressive it is: The higher the glucose, the more aggressive the algorithm.
#these factors create a curve similar to an exponential curve.

UseGainScheduling=True  #set this to False if you want a linear aggression. linear aggression is SAFER because it is simpler and easier to understand. if setting this to False, you can then ignore the next two values.

AggressionFactorBase=1.3  #increase this number by increments of 0.1 (testing along the way) if the algorithm has a tendency to overshoot and push glucose level too far below the target glucose (e.g. hypo happens even if pump has been suspended for a while).
AggressionFactorWidth=275 #decrease this number by increments of 25 (testing along the way) if it takes too long to come down from a high glucose. Doing so will increase the aggressiveness at high glucose values.
#these two numbers are linked together, so each time you change one, you **must** change the other or you will get unexpected and potentially dangerous results. they require some patience to tweak.
#the relationship is: AggressionFactor = AggressionFactorBase - ((Glucose-TargetGlucose)/AggressionFactorWidth)
#see CalculateBolus() for the full AggressionFactor calculation.
#the AggressionFactor only has an effect when (Glucose - TargetGlucose) > 0.

def CalculateBolus(glucose):
#v2 done, tested.

    if glucose < 0:                        #glucose == -1 means that the glucose data is older that 4m59s
       AppendLog("CalculateBolus()",5005)
       return -1
      
    floatUnits=0.0        #start from a safe place
    Proceed=0             #start from a safe place
    
    GlucoseCorrection = glucose - TargetGlucose
    try: 
        AggressionFactor = AggressionFactorBase - (float(GlucoseCorrection) / AggressionFactorWidth)
    except:
        AggressionFactor = 1
    if not UseGainScheduling:
        AggressionFactor = 1
    
    AdjustedCorrectionFactor = abs(CorrectionFactor * AggressionFactor * ExerciseFactor)   #later, NeededGlucoseCorrection may be negative. to avoid flipping the sign accidentally, this number MUST come out positive.
   
    if UsePrediction:
        try:    
            with open(GlucosePredictFile) as GlucosePredict:    
                GlucosePredictData = json.load(GlucosePredict)
        except:
            #UsePrediction = False
            PredictionCorrection = 0
            AppendLog("CalculateBolus()", 5010, "Could not open %s." % (GlucosePredictFile))
        else:
            lg4p = GlucosePredictData["lg4p"]     #"last glucose, for prediction"
            lcf4p = GlucosePredictData["lcf4p"]   #"last correction factor, for prediction"
            lpc4p = GlucosePredictData["lpc4p"]   #"last PredictionCorrection, for prediction"
            GlucosePredict.close()
            if lg4p>0 and lcf4p>0:
                CurrentPredictionCorrection=(glucose - (lg4p - (IOB * lcf4p / (DIA * LoopsPerHour)))) * MealDetectReponseFactor
#**************************
#to be tested, can this new averaging manage 110g of carb in pizza, keeping the spike over 10mmol/l to under 2 hours? also never exceeding 242.
                if CurrentPredictionCorrection > 0 and lpc4p > 0:
                    list= [CurrentPredictionCorrection, lpc4p]   
                    PredictionCorrection = sum(list)/len(list)   #after the initial upward "jolt" in glucose, need to "pull up" subsequent prediction correction values, otherwise the IOB is high enough to kill the effect of the prediction correction.
                else:
                    PredictionCorrection = CurrentPredictionCorrection #positive if sugar dropping too slowly (or rising) and negative if sugar dropping too quickly.
#**************************
            else:
                PredictionCorrection = 0
    else:
        PredictionCorrection = 0 
    
    NeededGlucoseCorrection = GlucoseCorrection + PredictionCorrection
    UnitsNeeded = float(NeededGlucoseCorrection) / AdjustedCorrectionFactor
    floatUnits = UnitsNeeded - IOB

    Logdata="Gluc=%i TgtGluc=%i PredictCorr=%f NeededCorr=%i CorrFactor=%i ExerFactor=%i AggrFactor=%f AdjCorrFactor=%f Needed=%f IOB=%f Units=%f MaxIOB=%f" % (glucose,TargetGlucose,PredictionCorrection,NeededGlucoseCorrection,CorrectionFactor,ExerciseFactor,AggressionFactor,AdjustedCorrectionFactor,UnitsNeeded,IOB,floatUnits,MaxIOB)
    if UsePrediction:
        Logdata += " lg4p=%s lcf4p=%s lpc4p=%s DIA=%s LoopsPerHour=%s MealDetectReponseF=%s." % (lg4p,lcf4p,lpc4p,DIA,LoopsPerHour,MealDetectReponseFactor)
    AppendLog("CalculateBolus()", 7000, Logdata)

    #now store current predict-relevant data for next time this function runs.
    data={"lg4p":glucose,"lcf4p":AdjustedCorrectionFactor,"lpc4p":PredictionCorrection}
    try:
        with open (GlucosePredictFile,"w") as GlucosePredict:
            json.dump(data,GlucosePredict)
    except:
        AppendLog("CalculateBolus()",5010, "Could not write to %s." % (GlucosePredictFile))
    else:
        GlucosePredict.close()

    #now all the calculation has been done. we'll need all that for the logs & later loops. but before we proceed, let's do a sanity check on the sensor data. 
    #if there has been a sudden/significant change in glucose direction, let's wait one loop for confirmation before reacting. sudden changes in direction are one of the signs of a potentially faulty sensor.
    if not SensorSanityCheck(): 
        return -1
      
    #time to start treatment. first deal with whether or not the pump is, or should be, suspended.
    try: 
        p = GetStatus()         #returns: 0 - status(str), 1 - bolusing(bln), 2 -suspended(bln)
    except:
        AppendLog("CalculateBolus()", 5080)
        return -1
    else:
        PumpSuspended = p[2]

    AppendLog("CalculateBolus()", 7000, "PumpSuspended is " + str(PumpSuspended))
	
    if floatUnits <= -0.5 and PredictionCorrection < 0 and not PumpSuspended:   #if we're on our way down, and the pump is not suspended...
	    #the use of -0.5 is to prevent the pump being suspended (which includes basal) when IOB is reasonably close to my usual basal insulin requirement.
        #PredictionCorrection is being used here as an indicator whether the glucose is going up or down.
        AppendLog("CalculateBolus()",6003, "Negative bolus of %s units requested." % (floatUnits))
        try:
            SuspendPump(True)
        except:
            AppendLog("CalculateBolus()", 5008)
        return floatUnits                   #exit function now. negative bolus will trigger no action from Bolus()

    elif floatUnits > -0.5 and PumpSuspended: 
        #activate pump
        AppendLog("CalculateBolus()",6004, "Bolus of %s units is requested." % (floatUnits))   #pump status is correct
        try:
            SuspendPump(False)
        except:
            AppendLog("CalculateBolus()", 5008)
		
    else:
        AppendLog("CalculateBolus()",6006)   #pump status is already correct

    if glucose < TargetGlucose:   #also, don't bolus if under the target glucose level.
        AppendLog("CalculateBolus()",5011)
        return -1

    #now deal with the bolus.
    #negative bolus situation is already dealt with above in the suspend pump section.
    #first check it will not exceed MaxIOB. If yes, then ask user for confirmation.
        
    if IOB+floatUnits < MaxIOB:
        AppendLog("CalculateBolus()",6010)
        Proceed=1
    else:
        AppendLog("CalculateBolus()",6011)
        Proceed=ConfirmBolus(floatUnits, IOB)  #get user confirmation. 1= Go ahead, 2= reduce to Max_IOB, else do nothing
        if Proceed==1:
            AppendLog("CalculateBolus()", 6000)
        elif Proceed==2:
            floatUnits = MaxIOB-IOB
            AppendLog("CalculateBolus()", 5002, "Proposed bolus reduced to %s units." % (floatUnits))
        else:    #proceed remains 0 or -1
            AppendLog("CalculateBolus()", 5001, "%s units" % (floatUnits))

    #alright, if all is still well, return the calculated outcome and exit.
    if Proceed>0:
        AppendLog("CalculateBolus()", 6001)
        return floatUnits
    else:
        AppendLog("CalculateBolus()", 5007)
        return -1

def Bolus(units): 
#v1 done, tested

    if units >= 0:
        try:
            p = GetStatus()         #returns: 0 - status(str), 1 - bolusing(bln), 2 -suspended(bln)
        except:
            AppendLog("Bolus()", 5080)
            return -1
        else:
	        PumpBolusing = p[1]

        if PumpBolusing:
            AppendLog("Bolus()",5004)
            return -1
        else:
            AppendLog("Bolus()",5009)

        try:
            tempval = GetReservoir()
        except:
            AppendLog("Bolus()", 5081)
            Reservoir = 300
        else:
		    Reservoir = tempval
			
        if units > Reservoir:
            AppendLog("Bolus()", 5003, "Reservoir contains %s units." % (Reservoir))
            return -1
        else:
            roundedUnits = float(str(round(units,1)))    #dirty hack (which works though!) to deal with the fact that round() only affects the displayed number, not the actual number
            AppendLog("Bolus()", 7000, "%s units rounded to %s units" % (units, roundedUnits))

            if roundedUnits>0:
                AppendLog("Bolus()", 6002, "Sending %s units." % (roundedUnits))
	
                #initiate comms
                GPIO.output(RemoteAct, GPIO.HIGH)
                time.sleep(3)
                GPIO.output(RemoteAct, GPIO.LOW)

                #count up bolus in Easy Bolus
                ticks=int(roundedUnits*10)       #Easy Bolus configured on the pump to count up in tenths of a unit
                for tick in range(1,ticks+2):    #needs two extra button presses, regardless of the value of ticks. don't know why, but this is the outcome of testing.
                    GPIO.output(RemoteBolus, GPIO.HIGH)
                    time.sleep(0.2)
                    GPIO.output(RemoteBolus, GPIO.LOW)
                    time.sleep(0.7)

                #execute
                GPIO.output(RemoteAct, GPIO.HIGH)
                time.sleep(0.2)
                GPIO.output(RemoteAct, GPIO.LOW)
                time.sleep(2 + (ticks*1.1))      #wait while Easy Bolus counts up the dose for confirmation

                #confirm
                GPIO.output(RemoteAct, GPIO.HIGH)
                time.sleep(0.5)
                GPIO.output(RemoteAct, GPIO.LOW)
               
                return 1

            else:
                AppendLog("Bolus()",5007, "%s units requested." % (roundedUnits))
                return -1              
    else:
        AppendLog("Bolus()",5007, "%s units requested." % (units))
        return -1

def SensorSanityCheck():

    a = GlucoseHistory[2]
    b = GlucoseHistory[1]
    c = GlucoseHistory[0]

    if abs(c - b) > 10 and ((c < b and b > a) or (c > b and b < a)):
        AppendLog("SensorSanityCheck()", 5012, "Erratic data: %s followed by %s followed by %s." % (a,b,c))
        return False
    else:
        AppendLog("SensorSanityCheck()", 6016, "Consistent data: %s followed by %s followed by %s." % (a,b,c))
        return True
        
def GetIOB():
#v1 done, tested

    try:
        p=subprocess.Popen (["openaps", "monitor-pump"], stdout=subprocess.PIPE)
        (output, err) = p.communicate()
        print output
    except:
        AppendLog("GetIOB()", 5000, "Could not retrieve current pump data.")
        raise IOError("Could not retrieve current pump data.")
	
    try:
        p=subprocess.Popen (["openaps", "get-iob"], stdout=subprocess.PIPE)   #get-iob is an alias for "use iob shell monitor/pump_history.json settings/profile.json monitor/clock.json"
        (output, err) = p.communicate()
        IOBdata = json.loads(output)
    except:
        AppendLog("GetIOB()", 5051)
        raise IOError("Could not calculate IOB.")
    else:
        return IOBdata["iob"]

def PrepIOB():
#v1 done, tested

    try:
        p=subprocess.Popen (["openaps", "get-settings"], stdout=subprocess.PIPE)
        (output, err) = p.communicate()
        print output
    except:
        AppendLog("PrepIOB()", 5000, "Could not get pump settings.")
        raise IOError("Could not get pump settings.")
		
def GetMaxIOB():
#v1 done, tested

    try:    
        with open("max_iob.json") as maxIOBfile:    
            maxIOBdata = json.load(maxIOBfile)
    except:
        AppendLog("GetMaxIOB()", 5050)
        raise IOError("Could not get Max IOB.")
    else:
        return maxIOBdata["max_iob"]
        maxIOBfile.close()
        
def GetDIA():
#v1 done, tested

    try:    
        with open("settings/profile.json") as settingsfile:    
            settingsdata = json.load(settingsfile)
    except:
        AppendLog("GetDIA()", 5052)
        raise IOError("Could not get DIA from profile.json.")
    else:
        return settingsdata["dia"]
        settingsfile.close()
        
def ConfirmBolus(units, IOB):
#v1 in progress

    #get user confirmation
    return 2  # 0= do nothing, 1= go ahead full amount, 2= reduce to Max_IOB (default)

def GetGlucose():
#v1 done, tested

    try:
        command = "openaps most-recent-reading"
        p=subprocess.Popen(command, stdout=subprocess.PIPE, shell=True)   #most-recent-reading is an alias for "use pump iter_glucose 5"    ["openaps", "most-recent-reading"]
        (txtglucose, err) = p.communicate()
        glucosedata = json.loads(txtglucose)
    except:
        #failed to communicate: 
        AppendLog("GetGlucose()", 5000)
        raise IOError("Could not communicate with the pump")
    else:
        try:
            for stanza in range(0,6):
                if glucosedata[stanza]["name"] == "GlucoseSensorData":
                    lastreadingtime = glucosedata[stanza]["date"]
                    currentglucose = glucosedata[stanza]["sgv"]
                    break
        except:
            AppendLog("GetGlucose", 5061, "Could not find the date or value of most recent reading in the output.")
            raise IOError("Could not find the date or value of the most recent reading in the output.")
        else:
            lastreadingtuple = (int(lastreadingtime[0:4]), int(lastreadingtime[5:7]), int(lastreadingtime[8:10]), int(lastreadingtime[11:13]), int(lastreadingtime[14:16]), int(lastreadingtime[17:19]), 0, 0, -1)
            if time.time() - time.mktime(lastreadingtuple) > 299:      #if last reading is older than 4 minutes 59 seconds
                AppendLog("GetGlucose()", 5060)
                return -1
        
        return currentglucose

def GetReservoir():
#v1 done, tested

    try:
        p=subprocess.Popen(["openaps", "get-reservoir"], stdout=subprocess.PIPE)   #get-reservoir is an alias for "use pump reservoir"
        (output, err) = p.communicate()
        reservoirdata = json.loads(output)
    except:
        AppendLog("GetReservoir()", 5000)
        return 300
    else:
        return reservoirdata

def GetCorrectionFactor(file):
#v1 done, tested

    try:    
        with open(file) as CorrectionFactorFile:    
            CorrectionFactordata = json.load(CorrectionFactorFile)       
    except:
        AppendLog("GetCorrectionFactor()", 5070)
        raise IOError("Could not get correction factor.")
    else:
        for result in CorrectionFactordata["sensitivities"]:
            return result["sensitivity"]
        CorrectionFactorFile.close()

def GetStatus():
#v1 done, tested
#returns tuple: position 0 - status(str), position 1 - bolusing(bln), position 2 -suspended(bln)

    try:
        p=subprocess.Popen(["openaps", "get-status"], stdout=subprocess.PIPE)   #get-status is an alias for "use pump status"
        (output, err) = p.communicate()
        statusdata = json.loads(output)
    except:
        AppendLog("GetStatus()", 5080)
        raise IOError("Could not get pump status.")
    else:
        AppendLog("GetStatus()",6005)
        return (statusdata["status"],statusdata["bolusing"],statusdata["suspended"])

def SuspendPump(desiredstatus):
#v1 done, tested
#desiredstatus is boolean. True=Suspend. False=Resume.

    if desiredstatus:
        AppendLog("SuspendPump()", 6003)  #suspending
    else:
        AppendLog("SuspendPump()", 6004)  #resuming

    try:
        p = GetStatus()
    except:
        AppendLog("SuspendPump()", 5080)
        raise IOError("Could not get pump status.")

    if p[2] != desiredstatus:

        #initiate comms
        GPIO.output(RemoteAct, GPIO.HIGH)
        time.sleep(3)
        GPIO.output(RemoteAct, GPIO.LOW)
        time.sleep(0.5)

        #send Suspend/Resume command
        GPIO.output(RemoteSuspend, GPIO.HIGH)
        time.sleep(0.5)
        GPIO.output(RemoteSuspend, GPIO.LOW)
        time.sleep(0.5)

        #execute
        GPIO.output(RemoteAct, GPIO.HIGH)
        time.sleep(0.5)
        GPIO.output(RemoteAct, GPIO.LOW)

        time.sleep(15)  #the pump seems somewhat knocked out after a change from suspend to resume. takes a while for comms to work again.
			
    try:
        sessionrefresh=subprocess.Popen(["openaps", "get-session"], stdout=subprocess.PIPE)  #get-session is an alias for "use pump Session"
        (output, err) = sessionrefresh.communicate()                                         #doing this to reset the session, otherwise second call to GetStatus() fails
    except:
        AppendLog("SuspendPump()", 5082, "Trying again in 15 seconds.")
        time.sleep(15)
        try:    #smash, bang, hit it again.
            sessionrefresh=subprocess.Popen(["openaps", "get-session"], stdout=subprocess.PIPE)  #get-session is an alias for "use pump Session"
            (output, err) = sessionrefresh.communicate()     			#doing this to reset the session, otherwise second call to GetStatus() fails
        except:
            #and if this didn't work, with a resigned sigh, we move on. we'll figure it out later.
            AppendLog("SuspendPump()", 5082, "Moving on in 15 seconds.")
            time.sleep(15)

    try:
        p = GetStatus()
    except:
        AppendLog("SuspendPump()", 5080)
        raise IOError("Could not get pump status.")
		
    if p[2] == desiredstatus:
        AppendLog("SuspendPump()",6007,"Status is: %s." % (p[2]))
        return 1
    else:
        AppendLog("SuspendPump()",5006,"Status is: %s." % (p[2]))
        return -1

def GetTargetGlucose(file):
#v1 done, tested

    try:    
        with open(file) as TargetGlucoseFile:    
            TargetGlucosedata = json.load(TargetGlucoseFile)       
    except:
        AppendLog("GetTargetGlucose()", 5071)
        raise IOError("Could not get target glucose.")
    else:
        for result in TargetGlucosedata["targets"]:
            return result["high"]
        TargetGlucoseFile.close()
        
def UpdateNightscout():
#v1 done, tested

    try:
        response=urllib2.urlopen('http://77.154.221.246',timeout=2)
    except:
        AppendLog("UpdateNightScout()",5040)
        return -1
    else:
        try:
            p=subprocess.Popen("/home/pi/update-nightscout.sh", stdout=subprocess.PIPE, shell=True)
            (output, err) = p.communicate()    
        except:
            AppendLog("UpdateNightScout()",5041)
            return -1
        else:
            AppendLog("UpdateNightScout()",6015)
            return 1

def InitUI():
#v1 in progress
    #on error raise IOError
    return 1

def UpdateUI(glucose, IOB, timestamp, reservoir, apsbattery, exercisemode):
#v1 in progress
#include:
# - pump busy or not
# - 

    return 1

def UserInput():
#v1 in progress
    #on error raise IOError

#include:
# - set/release exercise mode
# - reload settings
# - exit to shell
# - shutdown/restart
# - confirm bolus
# - _maybe_ switch on and off HDMI port (power saving)
# - prevent user input during a loop
# - bolus suspend mode e.g. for when in the shower or otherwise disconnected from the pump.
# - keepalive mode: shut off USB power management

    return 1

def ExerciseMode():
#v1 in progress
#user sets it and unsets it
#in this mode, ExerciseFactor becomes something more, like 3 (divides the aggressiveness of the algorithm by 3)
#needs also the option to have it time out and return to normal for PWD like me, who will forget to set it back to normal.

    return 1

def BolusSuspend():
#v1 in progress
#mode to suspend bolusing
#user can set/unset (same mechanisms as exercise mode) for when disconnected from the pump, e.g. in the shower.

    return 1

def Preflight():
#v1 in progress
#To do: Check the clock

    command="./hub-ctrl.c/hub-ctrl -h 0 -P 2 -p 0"    #put USB and Ethernet to sleep
    try:
        p=subprocess.Popen (command, stdout=subprocess.PIPE, shell=True)
        (output, err) = p.communicate()
    except:
        pass
	
    time.sleep(1)
		
    command="./hub-ctrl.c/hub-ctrl -h 0 -P 2 -p 1"    #wake USB and Ethernet up
    try:
        p=subprocess.Popen (command, stdout=subprocess.PIPE, shell=True)
        (output, err) = p.communicate()
    except:
        AppendLog("Preflight()",5092)
        command="sudo reboot"
        p=subprocess.Popen (command, stdout=subprocess.PIPE, shell=True)

    try:
        p=subprocess.Popen(["openaps", "get-model"], stdout=subprocess.PIPE)  #get-model is an alias for "use pump model"
        (output, err) = p.communicate()
    except:
        #failed to communicate: 
        AppendLog("Preflight()",5000)
        raise IOError("Could not communicate with the pump.")
    else:
        AppendLog("Preflight()",6009,"Model is %s" % output)
        return 1

def ResetPredictionJSON():

    data={"lg4p":0,"lcf4p":0.0,"lpc4p":0.0}
    try:
        with open (GlucosePredictFile,"w") as GlucosePredict:
            json.dump(data,GlucosePredict)
    except:
        AppendLog("ResetPredictionJSON()", 5093)
    else:
        GlucosePredict.close()
        
def WaitAWhile(LastGlucose):
#v1 done, tested
#wait up to 15 minutes to let previous treatment start to work. However, check each 5 minutes whether the glucose is changing rapidly, in which case break early and rerun the loop.
#also break and rerun the loop if glucose is too high to be waiting around.
#switch off peripherals while waiting - waste of energy.

    for iteration in range(0,3):
        AppendLog("WaitAWhile()", 6012)
        
        if not KeepAlive:
            command="./hub-ctrl.c/hub-ctrl -h 0 -P 2 -p 0"    #put USB and Ethernet to sleep
            try:
                p=subprocess.Popen (command, stdout=subprocess.PIPE, shell=True)
                (output, err) = p.communicate()
            except:
                pass

        time.sleep(LoopWaitTime-10)   #four minutes (240 seconds) is the minimum for Medtronic CGM due to update interval of the glucose readings. additional missing 10 seconds are below, after USB wake-up.

        if not KeepAlive:
            command="./hub-ctrl.c/hub-ctrl -h 0 -P 2 -p 1"    #wake USB and Ethernet up
            try:
                p=subprocess.Popen (command, stdout=subprocess.PIPE, shell=True)
                (output, err) = p.communicate()
            except:
                AppendLog("WaitAWhile",5092)
                command="sudo reboot"
                p=subprocess.Popen (command, stdout=subprocess.PIPE, shell=True)

        time.sleep(10)
	
        try:
            Glucose=GetGlucose()
        except:
            AppendLog("WaitAWhile()", 5061, "Exiting wait state.")
            break
        else:
            AppendLog("WaitAWhile()", 7000, "Current glucose is %i and previous glucose was %i." % (Glucose, LastGlucose))
            if abs(Glucose-LastGlucose) > 5:
                AppendLog("WaitAWhile()", 6014)
                break
            elif Glucose - TargetGlucose > WaitingThreshold:
                AppendLog("WaitAWhile()", 6013, "However, glucose is above waiting threshold of %i. Exiting wait state." % (TargetGlucose + WaitingThreshold))
                break
            else:
                AppendLog("WaitAWhile()", 6013)
                UpdateNightscout()

    AppendLog("WaitAWhile()", 7000, "Wait is over.")

def MonitorBattery():
#v1 in progress, hardware dependent

    return 1

def ShutdownRestart():
#v1 in progress

    return 1
        
        
def InitLog():
#v1 done, tested

    logfilename = "./logs/" + time.strftime("%Y%m%d-%H%M%S") + "-APSlog.txt"
    print "Initiating log file " + logfilename
    LogFile=open(logfilename,"w")
    LogFile.write("Initiating log file " + logfilename + "\n")
    LogFile.close()
          
    return logfilename

LogF=InitLog()

def AppendLog(function, code, message=" ", file=LogF):
#v1 done, tested

    Now = time.strftime("%Y-%m-%d %H:%M:%S")

    LogFile=open(file, "a")
	
    try:    
        with open(ErrorCodeFile) as codetextfile:    
            codetextdata = json.load(codetextfile)
    except:
        print "Could not open error code file."
        LogFile.write("Could not open error code file")
		
    LogLine = Now + " " + str(code) + " " + function + " " + codetextdata[str(code)] + " " + message
    print LogLine

    LogFile.write(LogLine + "\n")

    sys.stdout.flush()
    os.fsync(LogFile.fileno())
    codetextfile.close()
    LogFile.close()

    return 1
	
#main program
#v1 done, tested

# deleted. for testing purposes.
