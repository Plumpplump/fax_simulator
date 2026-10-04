"""Service layer: adapters that connect the app to the outside world.

Currently this holds the fax network protocol built on top of the generic
:mod:`p2p` toolkit. Keeping it separate from ``models`` means the pure image
logic there stays free of sockets, and the reusable networking core in ``p2p``
stays free of any fax-specific ideas.
"""
