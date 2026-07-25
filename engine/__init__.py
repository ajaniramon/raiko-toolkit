"""raiko engine — UI/transport-agnostic agent core.

One turn loop, three skins: the Textual TUI (tui.py), the headless runner
(agent.py) and the web layer (web/) all consume this package. The engine emits
`engine.protocol` events and receives `engine.protocol` commands; it never
touches widgets or sockets.
"""
