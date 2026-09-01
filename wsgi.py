#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production WSGI entry point.

Local development may still run ``python server.py``. Production processes import this module so
the Flask development server and its process-local request handler are never started.
"""
from server import app

application = app
