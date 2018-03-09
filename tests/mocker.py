try:
    import unittest.mock as mock
except ImportError:
    import mock

Mock = mock.Mock  # @UndefinedVariable
PropertyMock = mock.PropertyMock  # @UndefinedVariable
call = mock.call  # @UndefinedVariable
