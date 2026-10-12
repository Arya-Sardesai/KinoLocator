"""Venue scrapers. Every module in this package is imported and every concrete
Scraper subclass found is registered under its venue_id."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

from .base import Scraper

__all__ = ["Scraper", "all_scrapers", "enabled_scrapers", "get_scraper"]


def all_scrapers() -> dict[str, type[Scraper]]:
    registry: dict[str, type[Scraper]] = {}
    for mod_info in pkgutil.iter_modules(__path__):
        if mod_info.name.startswith("_") or mod_info.name == "base":
            continue
        module = importlib.import_module(f"{__name__}.{mod_info.name}")
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, Scraper)
                and not inspect.isabstract(obj)
                and obj.__module__ == module.__name__
            ):
                if obj.venue_id in registry:
                    raise RuntimeError(
                        f"duplicate venue_id {obj.venue_id!r}: "
                        f"{registry[obj.venue_id].__module__} and {module.__name__}"
                    )
                registry[obj.venue_id] = obj
    return dict(sorted(registry.items()))


def enabled_scrapers() -> dict[str, type[Scraper]]:
    return {vid: cls for vid, cls in all_scrapers().items() if cls.enabled}


def get_scraper(venue_id: str) -> type[Scraper]:
    registry = all_scrapers()
    try:
        return registry[venue_id]
    except KeyError:
        raise KeyError(f"unknown venue {venue_id!r}; known: {', '.join(registry)}") from None
