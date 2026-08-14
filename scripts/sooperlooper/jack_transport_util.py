"""Helpers for python-jack-client transport state (0.5.x API)."""

from __future__ import annotations


def transport_label(state) -> str:
    """Normalize TransportState to STOPPED / ROLLING / STARTING / …"""
    if state is None:
        return "UNKNOWN"
    text = repr(state).upper()
    for label in ("ROLLING", "STARTING", "STOPPED", "NETSTARTING", "LOOPING"):
        if label in text:
            return label
    return text


def transport_rolling(state) -> bool:
    return transport_label(state) in {"ROLLING", "STARTING"}


def transport_stopped(state) -> bool:
    return transport_label(state) == "STOPPED"
