# Crazyhouse OpenBench book

`CRAZYHOUSE_openings.epd` is the 599-position Crazyhouse opening corpus used by
the project's established `variantfishtest.py` methodology. Its exact SHA-256
is `1371e87ce3bdb875d922ad0061c96c4a123bc571daf4ae2bff24e5176287f0fa`.

Every position is derived from `crazyhouse.epd` in
[`fairy-stockfish/books`](https://github.com/fairy-stockfish/books) at commit
`1b0cf1f9473b5412e1631a9327098ac1b38b096b`. The upstream file's SHA-256 is
`2a960cf01c8641f5b79fa214c3ffb51cd11fbf474215ac97a990d3b79b48fe5f`.
This corpus selects positions from that file and removes the trailing EPD
semicolon; it does not alter the FEN payloads.

The upstream book repository is licensed under GNU GPL v3.0. This redistributed
corpus is provided under the same license; see the repository's `Copying.txt`.

`CRAZYHOUSE_openings.epd.zip` is the immutable transport artifact registered by
OpenBench. It is 3,401 bytes with SHA-256
`d24bb6d72015af9930f76f9191ba36c016652a6f2708a2cc79e9e2c8ec600d9c`.
The OpenBench descriptor pins both the archive URL and this digest.
