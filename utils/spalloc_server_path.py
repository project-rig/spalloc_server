#!/usr/bin/env python

"""Print the path of the local spalloc_server installation."""

if __name__=="__main__":  # pragma: no cover
    import spalloc_server
    import os.path
    print(os.path.dirname(spalloc_server.__file__))
