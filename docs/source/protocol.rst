Communication Protocol
======================

The SpiNNaker partition server uses a simple JSON-based protocol over TCP to
communicate with clients. The protocol has no security features what-so-ever,
just like SpiNNaker hardware, and it is assumed that the server is operated
within the same trusted network as the boards it manages.

By default, the server listens on TCP port number 22244. The client and server
communicate by sending and receiving newline (``\n``) delimited JSON objects
(i.e. lines of the form ``{...}``). Clients may send *commands* to the server
to which *return values* are sent by the server. The server may also
asynchronously send *notifications* to the client, if requested by the client.

As soon as a client connects to the server it should send a
:py:func:`~commands.version` command and ensure the server version is
compatible with the client.

Sending Commands
----------------

Commands map exactly to Python function calls. A command has a name and
arguments and returns a value. To send a command, a client must sent a JSON
object the following keys, followed by a newline:

"command"
    A string. The name of the command to be executed.
"args"
    An array. A list of positional arguments for the command.
"kwargs"
    An object. A list of keyword arguments for the command.

For example, if a client sent the following to the server::

    {"command": "create_job", "args": [4, 2], "kwargs": {"owner": "me"}}\n

This would be interpreted as a function call like::
    
    create_job(4, 2, owner="me")

.. note::
    
    In all examples, ``\n`` means a newline character (ASCII 10), **not** the
    ``\`` and ``n`` characters.

The server will then respond with a JSON object with a single key, "return",
whose value is the value returned by the command. For example::

    {"return": 42}\n

Commands are processed and return values sent in FIFO order. No blocking
commands are implemented by the server and the server will make a best-effort
attempt to respond to all commands as quickly as possible. If any command is
malformed or causes an error for any reason, the client is immediately
disconnected.

Receiving Asynchronous Notifications
------------------------------------

If the client requests to be notified of certain events (using a command, as
described above), the server may send a JSON object to the client which does
not contain the key "return". Notifications may be sent at any time, once
requested, including between a function being called and the return value
being sent. The exact format of the notification depends on its type. An
example notification may look like the following::

    {"jobs_changed": [42, 10, 3]}\n


Available Commands
------------------

.. automodule:: commands
    :members:
