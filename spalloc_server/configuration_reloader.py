# Copyright (c) 2017-2019 The University of Manchester
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging as log
import signal
from spinn_utilities.abstract_base import AbstractBase, abstractmethod

_SIGHUP = signal.SIGHUP if hasattr(signal, "SIGHUP") else None


class ConfigurationReloader(object, metaclass=AbstractBase):
    """ This provides the core knowledge about how to load and reload some\
        configuration when a signal is sent to the process. It is probably\
        wise to only make one concrete subclass instance of this class at a\
        time.
    """

    def __init__(self, configuration_file, callback):
        self._config_filename = configuration_file
        # Set up SIGHUP signal handler for config file reloading
        if _SIGHUP is not None:
            signal.signal(_SIGHUP, self._sighup_handler)
            self._callback = callback
            log.info("configuration reloading enabled: "
                     "send SIGHUP to trigger reload")

    def _sighup_handler(self, signum, _frame):
        """ Handler for SIGHUP. If such a signal is delivered, will trigger a\
            reread of the configuration file.

        :param signum: the signal number
        :type signum: int
        :param _frame: the stack frame where the signal occurred (ignored)
        """
        if signum == _SIGHUP:
            self._reload_config = True
            self._callback()

    @property
    def config_needs_reloading(self):
        """ Describes whether a reload is pending.
        """
        return self._reload_config

    @property
    def configuration_file(self):
        """ Describes where the configuration will be (re)loaded from.
        """
        return self._config_filename

    def read_config_file(self):
        """ (Re-)read the server configuration.

        If reading of the configuration file fails, the current configuration\
        is retained, unchanged.

        :return: True if the reading succeeded.
        :rtype: bool
        """
        self._reload_config = False
        try:
            with open(self._config_filename, "r") as f:
                config_script = f.read()
        except (IOError, OSError):  # pragma: no cover
            log.exception("Could not read config file %s",
                          self._config_filename)
            return False

        try:
            parsed_config = self._parse_config(config_script)
        except Exception:  # pylint: disable=broad-except
            # Executing the config file failed, don't update any settings
            log.exception("Error while evaluating config file %s",
                          self._config_filename)
            return False

        # Check that the configuration is meaningful
        validated = self._validate_config(parsed_config)
        if validated is None:
            log.error("Configuration loaded from %s was not valid",
                      self._config_filename)
            return False

        # Update the configuration
        try:
            self._load_valid_config(validated)
        except Exception:  # pylint: disable=broad-except
            # Config loading failed
            log.exception("Configuration loading file resulted in exception")
            return False

        log.info("Config file %s read successfully.", self._config_filename)
        return True

    @abstractmethod
    def _parse_config(self, config_file_contents):
        """ How to parse the contents of the configuration file. Note that\
            this is intended to be a basic parse, not an extended semantic\
            check for high-level validity.

        :param config_file_contents: the contents of the file
        :type config_file_contents: str
        :return: Some parsed description of the configuration. Will be passed\
            to validator method.
        :raises Exception: if the parse fails
        """

    @abstractmethod
    def _validate_config(self, parsed_config):
        """ How to check the parsed contents of the configuration for\
            high-level semantic validity.

        :param parsed_config: Whatever was produced by the parser method.
        :return: \
            The validated configuration object, or `None` if the configuration\
            was invalid.
        """

    @abstractmethod
    def _load_valid_config(self, validated_config):
        """ How to install the validated configuration. Not expected to have\
            a failure mode under normal circumstances.

        :param validated_config: \
            Whatever was produced by the validator method.
        """
