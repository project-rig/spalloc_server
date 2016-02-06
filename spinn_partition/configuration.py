"""Server configuration object container."""

from collections import namedtuple

from spinn_partition.machine import Machine

from six import iteritems, itervalues


class Configuration(namedtuple("Configuration",
                               "machines,port,ip,timeout_check_interval,"
                               "max_retired_jobs")):
    
    def __new__(cls, machines=[], port=10203, ip="",
                timeout_check_interval=5.0,
                max_retired_jobs=1200):
        """Defines a server configuration.
        
        Paramters
        ----------
        machines : [:py:class:`~spinn_partition.machine.Machine`, ...]
            The list of machines, highest priority first, the server is to
            manage.
        port : int
            The port number the server should listen on.
        ip : str
            The IP the server should listen on.
        timeout_check_interval : float
            The number of seconds between the server's checks for job timeouts.
        max_retired_jobs : int
            The number of retired jobs to keep track of.
        """
        # Validate machine definitions
        used_names = set()
        used_bmp_ips = set()
        used_spinnaker_ips = set()
        for m in machines:
            # Typecheck...
            if not isinstance(m, Machine):
                raise TypeError("All machines must be of type Machine.")
            
            # Machine names must be unique
            if m.name in used_names:
                raise ValueError("Machine name '{}' used multiple "
                                 "times.".format(m.name))
            used_names.add(m.name)
            
            # All BMP IPs must be unique
            for bmp_ip in itervalues(m.bmp_ips):
                if bmp_ip in used_bmp_ips:
                    raise ValueError("BMP IP '{}' used multiple "
                                     "times.".format(bmp_ip))
                used_bmp_ips.add(bmp_ip)
            
            # All SpiNNaker IPs must be unique
            for spinnaker_ip in itervalues(m.spinnaker_ips):
                if spinnaker_ip in used_spinnaker_ips:
                    raise ValueError("SpiNNaker IP '{}' used multiple "
                                     "times.".format(spinnaker_ip))
                used_spinnaker_ips.add(spinnaker_ip)

        return super(Configuration, cls).__new__(cls, machines, port, ip,
                                                 timeout_check_interval,
                                                 max_retired_jobs)
