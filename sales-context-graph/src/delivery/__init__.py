"""Increment 17 — outbound delivery. Currently one channel (Slack, via an
Incoming Webhook URL — no OAuth flow available in this environment). The block
builder is pure and network-free; posting is the one function in this package
that makes an HTTP call, kept separate so it's the only thing a test must not
exercise for real.
"""
