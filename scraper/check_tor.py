#!/usr/bin/env python3
"""Prints: ok | no_stem | fail:<reason>"""
try:
    from stem import Signal
    from stem.control import Controller
except ImportError:
    print("no_stem")
    raise SystemExit(0)

try:
    with Controller.from_port(port=9051) as ctrl:
        ctrl.authenticate()
        ctrl.signal(Signal.NEWNYM)
    print("ok")
except Exception as e:
    print(f"fail:{e}")
