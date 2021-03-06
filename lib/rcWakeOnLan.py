import sys
import socket
import struct
import re

regex_mac = re.compile('[0-9a-f]{2}([-:])[0-9a-f]{2}(\\1[0-9a-f]{2}){4}$')
regex_broadcast = re.compile('^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$')

class wolrequest(object):
    def __init__(self, macaddress, broadcast, udpport=7):
        self.mac = macaddress
        self.broadcast = broadcast
        self.udpport = int(udpport)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def check_mac(self):
        if not ':' and not '-' in self.mac:
            return False
        if regex_mac.match(self.mac.lower()) is None:
            return False
        if ':' in self.mac:
            self.mac = self.mac.replace(':', '')
        if '-' in self.mac:
            self.mac = self.mac.replace('-', '')
        if len(self.mac) != 12:
            return False
        return True

    def check_broadcast(self):
        if regex_broadcast.match(self.broadcast) is None:
            return False
        return True

    def send(self):
        buf = ''.join(['FFFFFFFFFFFF', self.mac * 20])
        payload = ''
        for i in range(0, len(buf), 2):
            payload = ''.join([payload,struct.pack('B', int(buf[i: i + 2], 16))])
        try:
            self.sock.sendto(payload, (self.broadcast, self.udpport))
        except:
            return False
        return True

