#!/usr/bin/python3
# -*- encoding: utf-8 -*-
"""
    BLEHeartRateLogger
    ~~~~~~~~~~~~~~~~~~~

    A tool to log your heart rate using a Bluetooth low-energy (BLE) heart rate
    monitor (HRM). The tool uses system commands (hcitool and gatttool) to
    connect to the BLE HRM and parses the output of the tools. Data is
    interpreted according to the Bluetooth specification for HRM and saved in a
    sqlite database for future processing. In case the connection with the BLE
    HRM is lost, connection is restablished.

    :copyright: (c) 2015 by fg1
    :license: BSD, see LICENSE for more details
"""

__version__ = "0.1.1"

import os
import sys
import time
import logging
import sqlite3
import pexpect
import argparse
import configparser
import threading
import queue

logging.basicConfig(format="%(asctime)-15s  %(message)s")
log = logging.getLogger("BLEHeartRateLogger")


def parse_args():
    """
    Command line argument parsing
    """
    parser = argparse.ArgumentParser(description="Bluetooth heart rate monitor data logger")
    parser.add_argument("-m", metavar='MAC', type=str, help="MAC address of BLE device (default: auto-discovery)")
    parser.add_argument("-g", metavar='PATH', type=str, help="gatttool path (default: system available)", default="gatttool")
    parser.add_argument("-H", metavar='HR_HANDLE', type=str, help="Gatttool handle used for HR notifications (default: none)")
    parser.add_argument("-v", action='store_true', help="Verbose output")
    parser.add_argument("-d", action='store_true', help="Enable debug of gatttool")

    confpath = os.path.join(os.path.dirname(os.path.realpath(__file__)), "BLEHeartRateLogger.conf")
    if os.path.exists(confpath):

        config = configparser.ConfigParser()
        config.read([confpath])
        config = dict(config.items("config"))

        # We compare here the configuration given in the config file with the
        # configuration of the parser.
        args = vars(parser.parse_args([]))
        err = False
        for key in config.iterkeys():
            if key not in args:
                log.error("Configuration file error: invalid key '" + key + "'.")
                err = True
        if err:
            sys.exit(1)

        parser.set_defaults(**config)

    return parser.parse_args()


class HRMThread(threading.Thread):
    def __init__(self, addr = None, gatttool="gatttool", hr_handle=None, debug_gatttool=False):
        threading.Thread.__init__(self)
        self.addr = addr
        self.gatttool = gatttool
        self.hr_handle = hr_handle
        self.debug_gatttool = debug_gatttool
        self.queue = queue.Queue()
        self.done = False
        
    def interpret(self, data):
        """
        data is a list of integers corresponding to readings from the BLE HR monitor
        """
	
        byte0 = data[0]
        res = {}
        res["hrv_uint8"] = (byte0 & 1) == 0
        sensor_contact = (byte0 >> 1) & 3
        if sensor_contact == 2:
            res["sensor_contact"] = "No contact detected"
        elif sensor_contact == 3:
            res["sensor_contact"] = "Contact detected"
        else:
            res["sensor_contact"] = "Sensor contact not supported"
        res["ee_status"] = ((byte0 >> 3) & 1) == 1
        res["rr_interval"] = ((byte0 >> 4) & 1) == 1
        
        if res["hrv_uint8"]:
            res["hr"] = data[1]
            i = 2
        else:
            res["hr"] = (data[2] << 8) | data[1]
            i = 3
            
        if res["ee_status"]:
            res["ee"] = (data[i + 1] << 8) | data[i]
            i += 2
            
        if res["rr_interval"]:
            res["rr"] = []
            while i < len(data):
                # Note: Need to divide the value by 1024 to get in seconds
                res["rr"].append((data[i + 1] << 8) | data[i])
                i += 2
                
            return res

    def run(self):
        """
        main routine to which orchestrates everything
        """
        
        if self.addr is None:
            print("no BLE address?")
            return
            
        hr_ctl_handle = None
        retry = True
        while retry:
            while 1:
                log.info("Establishing connection to " + self.addr)
                gt = pexpect.spawn(self.gatttool + " -b " + self.addr + " -t random --interactive")
                if self.debug_gatttool:
                    gt.logfile = sys.stdout

                gt.expect(r"\[LE\]>")
                gt.sendline("connect")

                try:
                    i = gt.expect(["Connection successful.", r"\[CON\]"], timeout=15)
                    if i == 0:
                        gt.expect(r"\[LE\]>", timeout=30)

                except pexpect.TIMEOUT:
                    log.info("Connection timeout. Retrying.")
                    if self.done:
                        log.info("Or not.  Just giving up.")
                        retry = False
                        break
                    continue

                if self.done:
                    log.info("Received keyboard interrupt. Quitting cleanly.")
                    retry = False
                    break
                
                break

            if not retry:
                break

            log.info("Connected to " + self.addr)

            if self.hr_handle == None:
                # We determine which handle we should read for getting the heart rate
                # measurement characteristic.
                gt.sendline("char-desc")

                while 1:
                    try:
                        gt.expect(r"handle: (0x[0-9a-f]+), uuid: ([0-9a-f]{8})", timeout=10)
                    except pexpect.TIMEOUT:
                        break
                    handle = gt.match.group(1).decode()
                    uuid = gt.match.group(2).decode()

                    if uuid == "00002902" and self.hr_handle:
                        hr_ctl_handle = handle
                        break

                    elif uuid == "00002a37":
                        self.hr_handle = handle

                if self.hr_handle == None:
                    log.error("Couldn't find the heart rate measurement handle?!")
                    return

            if hr_ctl_handle:
                # We send the request to get HRM notifications
                gt.sendline("char-write-req " + hr_ctl_handle + " 0100")
                
            # Time period between two measures. This will be updated automatically.
            period = 1.
            last_measure = time.time() - period
            hr_expect = "Notification handle = " + self.hr_handle + " value: ([0-9a-f ]+)"

            while 1:
                try:
                    gt.expect(hr_expect, timeout=10)

                except pexpect.TIMEOUT:
                    # If the timer expires, it means that we have lost the
                    # connection with the HR monitor
                    log.warn("Connection lost with " + self.addr + ". Reconnecting.")
                    gt.sendline("quit")
                    try:
                        gt.wait()
                    except:
                        pass
                    time.sleep(1)
                    break
                    
                if self.done:
                    log.info("Received keyboard interrupt. Quitting cleanly.")
                    retry = False
                    break

                # We measure here the time between two measures. As the sensor
                # sometimes sends a small burst, we have a simple low-pass filter
                # to smooth the measure.
                tmeasure = time.time()
                period = period + 1 / 16. * ((tmeasure - last_measure) - period)
                last_measure = tmeasure

                # Get data from gatttool
                datahex = gt.match.group(1).strip()
                data = map(lambda x: int(x, 16), datahex.split(b' '))
                res = self.interpret(list(data))

                log.debug(res)

                self.queue.put(res["hr"])

        # We quit close the BLE connection properly
        gt.sendline("quit")
        try:
            gt.wait()
        except:
            pass
    
    def shutdown(self):
        self.done = True


def cli():
    """
    Entry point for the command line interface
    """
    args = parse_args()
    
    if args.g != "gatttool" and not os.path.exists(args.g):
        log.critical("Couldn't find gatttool path!")
        sys.exit(1)

    # Increase verbose level
    if args.v:
        log.setLevel(logging.DEBUG)
    else:
        log.setLevel(logging.INFO)
    
    t = HRMThread(addr = args.m, gatttool = args.g, hr_handle = args.H, debug_gatttool = args.d)
    t.start()
    print("... HRM thread is running ...")
    try:
        while True:
            print("...pop...")
            print(t.queue.get())
    except KeyboardInterrupt:
        t.shutdown()

if __name__ == "__main__":
    cli()
