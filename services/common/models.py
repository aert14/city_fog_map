from pydantic import BaseModel, Field
from typing import Any, Dict, List, Literal, Optional
from datetime import datetime


class VisitRequest(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)


class Circle(BaseModel):
    lat: float
    lon: float


class RegionStats(BaseModel):
    id: int
    visited_cells: int
    visited_weight: float


class VisitStats(BaseModel):
    total_circles: int
    district: Optional[RegionStats] = None
    okrug: Optional[RegionStats] = None


class VisitResponse(BaseModel):
    added: int
    circle: Circle
    stats: VisitStats


class CirclesResponse(BaseModel):
    hexagons: List[str]


class ProgressBreakdown(BaseModel):
    visited_cells: int
    total_cells: int
    percent: float
    percent_cells: float = 0.0
    percent_weight: float = 0.0
    visited_weight: float = 0.0
    total_weight: float = 0.0


class DistrictFeatureResponse(BaseModel):
    id: int
    name: str
    level: Literal["okrug", "district"]
    parent_id: Optional[int] = None
    bbox: Optional[List[float]] = None
    geom: Dict[str, Any]
    progress: ProgressBreakdown


class DistrictCellResponse(BaseModel):
    h3: str
    coverage: float
    visited: bool
    total_children: Optional[int] = None
    visited_children: Optional[int] = None
    visited_fraction: Optional[float] = None


class DistrictCellsResponse(BaseModel):
    district_id: int
    resolution: int
    base_resolution: int
    cells: List[DistrictCellResponse]


class OkrugSummaryEntry(BaseModel):
    id: int
    name: str
    parent_id: Optional[int] = None
    progress: ProgressBreakdown


class DistrictSummaryEntry(BaseModel):
    id: int
    name: str
    parent_id: Optional[int] = None
    parent_name: Optional[str] = None
    progress: ProgressBreakdown


class StatsSummaryResponse(BaseModel):
    total: ProgressBreakdown
    okrugs: List[OkrugSummaryEntry]
    bottom_districts: List[DistrictSummaryEntry]


class LeaderboardEntry(BaseModel):
    rank: int
    user_id: int
    username: Optional[str]
    visited_cells: int
    visited_weight: float
    percent_cells: float
    percent_weight: float


class LeaderboardResponse(BaseModel):
    level: Literal["district", "okrug"]
    period: Literal["week", "season"]
    generated_at: datetime
    entries: List[LeaderboardEntry]


class DeleteCircleRequest(BaseModel):
    geokey: str = Field(..., min_length=10, max_length=20)


class AuthRequest(BaseModel):
    initData: str


class UserInfo(BaseModel):
    id: int
    tg_id: int
    username: Optional[str]
