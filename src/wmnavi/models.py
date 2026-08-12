"""Shared data models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ItemInfo:
    id: str
    name: str
    short_name: str
    icon_url: str
    flea_price: int = 0
    trader_price: int = 0
    trader_name: str = ""

    @property
    def best_trader_price(self) -> int:
        return self.trader_price

    @property
    def best_price(self) -> int:
        return max(self.flea_price, self.trader_price)

    def effective_price(self, use_flea: bool = True, use_trader: bool = True, match: str = "either") -> int:
        flea = self.flea_price if use_flea else 0
        trader = self.trader_price if use_trader else 0
        if match == "both":
            return min(flea, trader) if flea and trader else 0
        if match == "flea":
            return flea
        if match == "trader":
            return trader
        return max(flea, trader)


@dataclass
class LootSpot:
    id: str
    x: float
    y: float
    z: float
    item_ids: list[str] = field(default_factory=list)


@dataclass
class MapPoint:
    id: str
    x: float
    y: float
    z: float
    label: str = ""
    kind: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class ContainerTypeInfo:
    id: str
    name: str
    spots: list[MapPoint] = field(default_factory=list)


@dataclass
class MapLayerData:
    extracts_pmc: list[MapPoint] = field(default_factory=list)
    extracts_scav: list[MapPoint] = field(default_factory=list)
    extracts_coop: list[MapPoint] = field(default_factory=list)
    transits: list[MapPoint] = field(default_factory=list)
    containers: dict[str, ContainerTypeInfo] = field(default_factory=dict)
    loose_loot: list[LootSpot] = field(default_factory=list)
    locks: list[MapPoint] = field(default_factory=list)
    switches: list[MapPoint] = field(default_factory=list)
    stationary_weapons: list[MapPoint] = field(default_factory=list)
    map_items: dict[str, ItemInfo] = field(default_factory=dict)
