"""Plug-in effects. Every module in this folder is imported automatically; each
registers effects with @effect / @frame_effect (from goveelights.effects) or FX with
@fx (from goveelights.fx). Add a feature by adding a file here: no shared files to
edit, and the app's pickers group effects by their `group`."""
import importlib
import logging
import pkgutil

log = logging.getLogger(__name__)

for _m in pkgutil.iter_modules(__path__):
    try:
        importlib.import_module(f"{__name__}.{_m.name}")
    except Exception:  # noqa: BLE001  - one broken plug-in must not take the app down
        log.exception("effect plug-in %s failed to load", _m.name)
