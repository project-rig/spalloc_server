from spinnman.messages.sdp.sdp_message import SDPMessage
from spinnman.messages.scp.scp_request_header import SCPRequestHeader
from spinnman.messages.scp.enums.scp_result import SCPResult
from spinnman.messages.sdp.sdp_header import SDPHeader
from spinnman.messages.sdp.sdp_flag import SDPFlag
from spinnman.connections.udp_packet_connections import udp_utils
from spinnman.connections.udp_packet_connections.udp_connection \
    import UDPConnection
from spinnman import constants

from collections import deque
from threading import Thread
import struct
import traceback


class SCPOKMessage(SDPMessage):

    def __init__(self, x, y, sequence=0):
        scp_header = SCPRequestHeader(
            command=SCPResult.RC_OK, sequence=sequence)
        sdp_header = SDPHeader(
            flags=SDPFlag.REPLY_NOT_EXPECTED, destination_port=0,
            destination_cpu=0, destination_chip_x=x, destination_chip_y=y)
        udp_utils.update_sdp_header_for_udp_send(sdp_header, 0, 0)
        SDPMessage.__init__(self, sdp_header, data=scp_header.bytestring)


class SCPVerMessage(SDPMessage):

    def __init__(self, x, y, version):
        self._scp_header = SCPRequestHeader(
            command=SCPResult.RC_OK)
        self._version = version
        self._y = y
        self._x = x
        sdp_header = SDPHeader(
            flags=SDPFlag.REPLY_NOT_EXPECTED, destination_port=0,
            destination_cpu=0, destination_chip_x=x, destination_chip_y=y)
        udp_utils.update_sdp_header_for_udp_send(sdp_header, 0, 0)
        SDPMessage.__init__(self, sdp_header)

    def set_sequence(self, sequence):
        self._scp_header.sequence = sequence

    @property
    def bytestring(self):
        data = self._scp_header.bytestring
        data += struct.pack("<BBBBHHI", 0, 0, self._y, self._x, 0, 0xFFFF, 0)
        data += "BC&MP/Test\0"
        data += self._version + "\0"
        response = SDPMessage.bytestring.fget(self) + data
        return response


class MockBMP(Thread):
    """ A BMP that can be used for testing protocol
    """

    def __init__(self, responses=None):
        """

        :param responses:\
            An optional list of responses to send in the order to be sent. \
            If not specified, OK responses will be sent for every request. \
            Note that responses can include "None" which means that no\
            response will be sent to that request
        """
        Thread.__init__(self, verbose=True)

        # Set up a connection to be the machine
        self._receiver = UDPConnection(local_port=constants.SCP_SCAMP_PORT)
        self._running = False
        self._error = None
        self._responses = deque()
        if responses is not None:
            self._responses.extend(responses)

    @property
    def error(self):
        return self._error

    @property
    def local_port(self):
        return self._receiver.local_port

    def run(self):
        self._running = True
        while self._running:
            try:
                if self._receiver.is_ready_to_receive():
                    data, address = self._receiver.receive_with_address(10)
                    sdp_header = SDPHeader.from_bytestring(data, 2)
                    _, sequence = struct.unpack_from("<2H", data, 10)
                    response = None
                    if len(self._responses) > 0:
                        response = self._responses.popleft()
                    else:
                        response = SCPOKMessage(
                            sdp_header.source_chip_x, sdp_header.source_chip_y,
                            sequence)
                    if hasattr(response, "set_sequence"):
                        response.set_sequence(sequence)
                    if response is not None:
                        self._receiver.send_to(
                            struct.pack("<2x") + response.bytestring, address)
            except Exception as e:
                if self._running:  # pragma: no cover
                    traceback.print_exc()
                    self._error = e

    def stop(self):
        self._running = False
        self._receiver.close()
