# -*- coding: iso-8859-15 -*-
#
# pysynscan
# Copyright (c) July 2020 Nacho Mas


import os
import logging
import synscanComm
import time

UDP_IP = os.getenv("SYNSCAN_UDP_IP","192.168.4.1")
UDP_PORT = os.getenv("SYNSCAN_UDP_PORT",11880)

LOGGING_LEVEL=os.getenv("SYNSCAN_LOGGING_LEVEL",logging.INFO)

class synscanMotors(synscanComm.synscanComm):
    '''
    Implementation of all motor commands and logic
    following the document:
    https://inter-static.skywatcher.com/downloads/skywatcher_motor_controller_command_set.pdf

    IMPORTANT NOTES(based on the above doc):

    In the motor controller, there is a hardware timer T1 that is used to generate stepping pulse
    for stepper motor or reference position for servomotor. The input clock’s frequency of the timer,
    plus the preset value of this timer, determine the slewing speed of the motors. 

    ** For GOTO mode, the motor controller will take care of T1 automatically. **

    When T1 generates an interrupt, it might:
        * Drive the motor to move 1 step (1 micro-step or 1 encoder tick) for low speed slewing.
        * Drive the motor to move up to 32 steps for high speed slewing. This method applies to motor
          controller firmware version 2.xx. For motor controller with firmware 3.xx or above, the motor
          controller always drive the motor controller 1 steps/interrupt.


    Typical session:
        * Check whether the motor is in full stop status. If not, stop it. 
        * Set the motion mode. 
        * Set the parameters, for example, destination or preset value of T1. 
        * Set the "Start" command. 
        * For a GOTO slewing, check the motor status to confirm that the motor stops (Generally means arriving the destination. ).
          For a Speed mode slewing, send "Stop" command to end the session. 

        Generally, the motor controller returns to "Speed Mode" when the motor stops automatically. 
    '''
    def __init__(self):
        '''Init UDP comunication '''      
        logging.basicConfig(
            format='%(asctime)s %(levelname)s:synscanAPI %(message)s',
            level=LOGGING_LEVEL
            )
        super(synscanMotors, self).__init__(udp_ip=UDP_IP,udp_port=UDP_PORT)
        self.init()


    def init(self):
        '''Get main motor parameters. Retry if comm fails'''
        retrySec=2
        try:
            self.params=self.get_parameters()
        except NameError as error:
            logging.warning(error)
            logging.warning(f'Retriying in {retrySec}..')
            time.sleep(retrySec)
            self.init()

    def degrees2counts(self,axis,degrees):
        '''Return position or speed in counts for a given deg or deg/seconds value'''
        CPR=self.params[axis]['countsPerRevolution']
        value=degrees*CPR/360
        return value

    def counts2degrees(self,axis,counts):
        '''Return position or speed in degrees for a given counts or counts/seconds value'''
        CPR=self.params[axis]['countsPerRevolution']
        value=counts*360/CPR
        return value

    def T1preset(self,axis,degreesPerSecond):
        countsPerSecond=self.degrees2counts(axis,degreesPerSecond)
        TMR_Freq=self.params[axis]['TimerInterruptFreq']
        T1preset=TMR_Freq/countsPerSecond
        return T1preset


    def get_values(self,parameterDict):
        '''
        Send all cmd in the parameterDict for both axis and return
        a dictionary with the values.
        Used by get_parameters and get_current_values functions
        '''
        params=dict()
        for axis in range(1,3):
            params[axis]=dict()
            for parameter,cmd in parameterDict.items():
                try:
                    params[axis][parameter]=self.send_cmd(cmd,axis)
                except NameError as error:
                    logging.warning(error)
                    raise(NameError('getValuesError'))
        return params

    def get_parameters(self):
        '''
        Get main motor parameters.
        Some of this parameters are needed for all calculations 
        so they have to be available to the rest of the code
        '''
        parameterDict={ 'countsPerRevolution':'a',
                        'TimerInterruptFreq':'b',
                        'StepPeriod':'i',
                        'MotorBoardVersion':'e'
                        }
        try:
            params=self.get_values(parameterDict)
        except NameError as error:
            logging.warning(error)
            raise(NameError('getParametersError'))
            return {}
        logging.info(f'MOUNT PARAMETERS: {params}')
        self.CPR=params
        return params

    def get_current_values(self):
        '''Get current status and values'''
        parameterDict={ 'GotoTarget':'h',
                        'Position':'j',
                        'StepPeriod':'i',
                        'Status':'f'
                        }
        try:
            params=self.get_values(parameterDict)
        except NameError as error:
            logging.warning(error)
            return {}
        for parameter in ['GotoTarget','Position']:
            for axis in range(1,3):
                #Position values are offseting by 0x800000
                params[axis][parameter]=params[axis][parameter]-0x800000
        for axis in range(1,3):
            params[axis]['Status']=self.decode_status(params[axis]['Status'])
        return params

    def decode_status(self,hexstring):
        ''' Decode Status msg.
        Status msg is 12bits long (3 HEX digits). 
        
        HEX digit1 bits:
        B0: 1=Tracking,0=Goto
        B1: 1=CCW,0=CW
        B2: 1=Fast,0=Slow

        HEX digit2 bits:
        B0: 1=Running,0=Stopped
        B1: 1=Blocked,0=Normal

        HEX digit3 bits:
        B0: 0 = Not Init,1 = Init done
        B1: 1 = Level switch on

        '''
        A=int(hexstring[0],16)       
        B=int(hexstring[1],16)
        C=int(hexstring[2],16)
        logging.debug(f'Decode status {hexstring} A:{A} B:{B} C:{C}')
        status=dict()
        status['Tracking']=bool(A & 0x01)
        status['CCW']=bool((A & 0x02) >> 1)
        status['FastSpeed']=bool((A & 0x04) >> 2)
        status['Stopped']=not(B & 0x01)
        status['Blocked']=bool((B & 0x02) >> 1)
        status['InitDone']=not(C & 0x01)
        status['LevelSwitchOn']=bool((B & 0x02) >> 1)
        return status


    def set_motion_mode(self,axis,Tracking,fastSpeed,CW):
        '''Set Motion Mode.

        Channel will always be set to Tracking Mode after stopped

        1byte msg (2 HEX digits)

        HEX Digit 1 bits:
        B0: 0=Goto, 1=Tracking
        B1: 0=Slow, 1=Fast(T)
            0=Fast, 1=Slow(G)
        B2: 0=S/F, 1=Medium
        B3: 1x SlowGoto

        HEX Digit 2 bits:  
        B0: 0=CW,1=CCW
        B1: 0=Noth,1=South
        B2: 0=Normal Goto,1=Coarse Goto
        '''
        if Tracking:
            if fastSpeed:
                speedBit=0x0
            else:
                speedBit=0x1
        else:
            if fastSpeed:
                speedBit=0x1
            else:
                speedBit=0x0
        value = (Tracking & 0x01) | (speedBit << 1) | ((CW & 0x01) <<7) 
        #Send as two HEX digits
        response=self.send_cmd('G',axis,value,ndigits=2) 
        logging.info(f'AXIS{axis}:Setting Motion Mode: {value}')
        return response        

    def set_step_period(self,axis,value):
        '''Set step period for tracking speed'''
        response=self.send_cmd('I',axis,value)
        logging.info(f'AXIS{axis}:Setting step_period to: {value}')
        return response

    def set_speed(self,axis,degreesPerSecond):
        '''Set the tracking speed in degreesPerSecond'''
        self.set_step_period(axis,int(self.T1preset(axis,degreesPerSecond)))

    def get_axis_pos(self,axis):
        '''Get actual postion in StepsCounts.'''
        #Position values are offseting by 0x800000
        response=self.send_cmd('j',axis)-0x800000
        return response

    def set_goto_target(self,axis,target):
        '''GoTo Target value in StepsCounts. Motors has to be stopped'''
        #Position values are offseting by 0x800000
        response=self.send_cmd('S',axis,target+0x800000)
        logging.info(f'AXIS{axis}:Setting goto target to {target} counts')
        return response

    def start_motion(self,axis):
        '''Start Goto'''
        response=self.send_cmd('J',axis)
        logging.info(f'AXIS{axis}:Starting motion')
        return response

    def stop_motion(self,axis):
        '''Soft stop'''
        response=self.send_cmd('K',axis)
        return response

    def test_goto(self,axis=2,X=90):
        '''Test GOTO'''
        self.stop_motion(axis)
        self.set_motion_mode(axis,False,False,False)
        self.set_speed(axis,5)
        posCounts=self.degrees2counts(axis,X)
        self.set_goto_target(axis,int(posCounts))
        self.start_motion(axis)
        params=self.get_current_values()[axis]
        print(params)
        while not params['Status']['Stopped']:
            time.sleep(2)
            params=self.get_current_values()[axis]
            print(params)

if __name__ == '__main__':
    smc=synscanMotors()
    smc.test_goto()

