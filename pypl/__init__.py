"""
PyPl - a protocol for interacting between Python and Perl
"""

from functools import wraps

import importlib

import json

import socket
import SocketServer


class RemoteObject(object):
    """
    A representation of a remote object
    """
    def __init__(self, remote, ref):
        self.remote = remote
        self.ref = ref

    def __getattr__(self, method):
        return lambda *args: self.remote.call(self.ref, method, *args)


class RemoteError(Exception):
    """
    An exception occurred on the remote side
    """
    pass


class RemoteJSONEncoder(json.JSONEncoder):
    """
    An encoder recognizing remote object proxies
    """
    def __init__(self, endpoint):
        super(RemoteJSONEncoder, self).__init__()
        self.endpoint = endpoint

    def default(self, obj): # pylint:disable=method-hidden
        if isinstance(obj, RemoteObject):
            return obj.ref

        index = len(self.endpoint.objects)
        self.endpoint.objects.append(obj)

        return {
            '_remote_proxy': index,
            'instance': self.endpoint.identity,
        }


class RemoteJSONDecoder(json.JSONDecoder):
    """
    A decoder recognizing remote object proxies
    """
    def __init__(self, endpoint):
        super(RemoteJSONDecoder, self).__init__(object_hook=self.decode_object)
        self.endpoint = endpoint

    def decode_object(self, obj):
        """
        Decode remote object proxies
        """
        if isinstance(obj, dict):
            if '_remote_proxy' in obj:
                if obj['instance'] == self.endpoint.identity:
                    return self.endpoint.objects[obj['_remote_proxy']]
                else:
                    return RemoteObject(self.endpoint, obj)
        return obj

class TransportException(Exception):
    """
    An error due to incomprehensible data received from the remote side
    """
    pass


def callback(func):
    """
    Within Endpoint, execute a function, send its result (or an error)
    back and continue listening for the next command
    """
    @wraps(func)
    def decorated(self, *args):
        """
        Wrapped function
        """
        try:
            val = func(self, *args)
            returned = True
        except Exception, exc: # pylint:disable=broad-except
            returned = False

        # pylint:disable=protected-access
        # This will be a part of the Endpoint class
        if returned:
            self._send('return', val)
        else:
            self._send('error', str(exc))
        return self._receive()
    return decorated


class Endpoint(object):
    """
    Base class for clients and servers
    """
    def __init__(self, transport, identity):
        self.encoder = RemoteJSONEncoder(self)
        self.decoder = RemoteJSONDecoder(self)

        self.objects = []

        self.transport = transport

        self.identity = identity

    def _send(self, command, *args):
        """
        Send a command to the remote side
        """
        line = self.encoder.encode([command] + list(args))
        print >> self.transport, line
        self.transport.flush()

    def _receive(self):
        """
        Receive a command from the remote side and act on it
        """
        line = self.transport.readline()

        line = self.decoder.decode(line)
        command = line[0]
        args = line[1:]

        command_function = 'command_%s' % command
        if hasattr(self, command_function):
            return getattr(self, command_function)(*args)
        raise TransportException("Invalid command %s" % command)

    def _send_receive(self, command, *args):
        """
        Send a command to the remote side and return the result received
        """
        self._send(command, *args)
        return self._receive()

    def use(self, module):
        """
        Import a module
        """
        return self._send_receive('import', module)

    def get_global(self, obj):
        """
        Return the value of a global object
        """
        return self._send_receive('global', obj)

    def call(self, obj, method, *args):
        """
        Call a method on an object
        """
        return self._send_receive('call', obj, method, *args)

    # Command handlers only use 'self' via the decorator
    # pylint:disable=no-self-use

    @callback
    def command_call(self, obj, method, *args):
        """
        Process the call command
        """
        return getattr(obj, method)(*args)

    @callback
    def command_global(self, obj):
        """
        Process a 'get global object value' command
        """
        return globals()[obj]

    @callback
    def command_import(self, module):
        """
        Process an 'import module' command
        """
        return importlib.import_module(module)

    def command_error(self, err):
        """
        Process a 'raise error' command
        """
        raise RemoteError(err)

    def command_return(self, value):
        """
        Process 'return a value' command
        """
        return value


class ServerEndpoint(Endpoint):
    """
    An endpoint passively executing commands from the remote side
    """
    def __init__(self, sock):
        super(ServerEndpoint, self).__init__(sock, 'server')

    def run(self):
        """
        Start executing commands from the remote side
        """
        while True:
            self._receive()


class ServerHandler(SocketServer.StreamRequestHandler):
    """
    Request handler for the Server
    """
    def handle(self):
        endpoint = ServerEndpoint(self.rfile)
        endpoint.run()


class Server(SocketServer.TCPServer, object):
    """
    A server listening for commands from the remote side
    """
    def __init__(self, address):
        super(Server, self).__init__(address, ServerHandler)


class Client(Endpoint):
    """
    A client sending commands to the remote side
    """
    def __init__(self, address):
        self.socket = socket.create_connection(address)
        super(Client, self).__init__(self.socket.makefile(), 'client')
