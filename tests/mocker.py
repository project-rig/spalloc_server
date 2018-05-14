try:
    import unittest.mock as mock
except ImportError:
    import mock

Mock = mock.Mock  # @UndefinedVariable
MagicMock = mock.MagicMock  # @UndefinedVariable
PropertyMock = mock.PropertyMock  # @UndefinedVariable
call = mock.call  # @UndefinedVariable
