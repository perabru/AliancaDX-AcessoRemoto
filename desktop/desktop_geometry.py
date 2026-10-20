"""Physical desktop geometry shared by screen capture and remote input."""

import hashlib
import json
import math


def monitor_rectangle(monitor):
    """Copy geometry from MSS 10 dictionaries or newer monitor objects."""
    return {
        key: int(monitor[key] if isinstance(monitor, dict) else getattr(monitor, key))
        for key in ("left", "top", "width", "height")
    }


def choose_monitor(monitors, monitor_id="all"):
    """Use the virtual desktop by default; fall back if a screen disappeared."""
    if not monitors:
        raise ValueError("Nenhum monitor disponível")
    try:
        index = int(monitor_id) if str(monitor_id) != "all" else 0
    except (TypeError, ValueError):
        index = 0
    if index < 1 or index >= len(monitors):
        index = 0
    rectangle = monitor_rectangle(monitors[index])
    if rectangle["width"] <= 0 or rectangle["height"] <= 0:
        raise ValueError("Resolução de monitor inválida")
    return ("all" if index == 0 else str(index)), rectangle


def screen_metadata(monitors, monitor_id, rectangle, frame_size):
    """Fingerprint physical geometry, including monitors outside the origin."""
    displays = []
    for index, monitor in enumerate(monitors[1:], 1):
        bounds = monitor_rectangle(monitor)
        displays.append({
            "id": str(index),
            "label": f"Monitor {index} — {bounds['width']} × {bounds['height']}",
            **bounds,
        })
    fingerprint = json.dumps({
        "monitorId": str(monitor_id),
        "rectangle": rectangle,
        "monitors": [{key: value for key, value in monitor.items() if key != "label"}
                     for monitor in displays],
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "captureId": hashlib.sha256(fingerprint).hexdigest()[:24],
        **rectangle,
        "frameWidth": int(frame_size[0]),
        "frameHeight": int(frame_size[1]),
        "monitorId": str(monitor_id),
        "monitors": displays,
        "agentVersion": "3.0.0",
    }


def physical_point(screen, x, y):
    """Map normalized image coordinates to inclusive physical screen edges."""
    x, y = float(x), float(y)
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError("Coordenada inválida")
    if not screen or screen["width"] <= 0 or screen["height"] <= 0:
        raise ValueError("Aguardando captura da tela")
    x, y = min(1.0, max(0.0, x)), min(1.0, max(0.0, y))
    return (
        screen["left"] + round(x * (screen["width"] - 1)),
        screen["top"] + round(y * (screen["height"] - 1)),
    )
