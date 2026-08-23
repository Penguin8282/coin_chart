from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class CandleCondition(BaseModel):
    field: str
    op: str
    value_type: Literal["number", "field"] = "number"
    value: float | str
    ref_offset: Optional[int] = None


class CandleRule(BaseModel):
    offset: int = Field(le=0)
    conditions: list[CandleCondition]


class RulePatternCreate(BaseModel):
    name: str
    direction: Literal["bullish", "bearish", "neutral"] = "neutral"
    candles: list[CandleRule]


class ShapePatternCreate(BaseModel):
    name: str
    direction: Literal["bullish", "bearish", "neutral"] = "neutral"
    market: str
    symbol: str
    interval: str = "1d"
    limit: int = 500
    start_idx: int
    end_idx: int


class WatchlistItem(BaseModel):
    market: str
    symbol: str


class RegisterBody(BaseModel):
    username: str
    password: str


class LoginBody(BaseModel):
    username: str
    password: str


class TradeCreate(BaseModel):
    market: str
    symbol: str
    side: str          # 'buy' | 'sell'
    qty: float
    price: float
    note: str = ""
