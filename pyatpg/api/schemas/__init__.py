from typing import List, Dict, Any, Optional
from pydantic import BaseModel


class GateFeatureSchema(BaseModel):
    gate_id: str
    gate_type: str
    role: str
    level: int
    fanin: int
    fanout: int
    pi_distance: int
    po_distance: int
    cc0: int
    cc1: int
    co: int
    toggle_rate: float
    detectable: bool = False
    undetected: bool = False
    redundant: bool = False
    fault_count: int = 0
    is_trojan_gate: bool = False
    is_trigger_gate: bool = False
    is_payload_gate: bool = False
    trojan_id: Optional[int] = None
    extra_features: Dict[str, Any] = {}


class FeatureSetSchema(BaseModel):
    circuit_name: str
    gate_features: List[GateFeatureSchema]
    atpg_coverage: Optional[float] = None
    extraction_time_ms: float = 0.0


class RankedGateSchema(BaseModel):
    gate_id: str
    score: float
    rank: int
    reason: Optional[str] = None


class RankingResultSchema(BaseModel):
    circuit_name: str
    ranked_gates: List[RankedGateSchema]
    ranker_name: str
    execution_time: float


class RankingRequestSchema(BaseModel):
    feature_set: FeatureSetSchema
    ranker_name: str
    #: Optional per-request credential for LLM-backed rankers. Applies to this
    #: call only and is never persisted or echoed back.
    api_key: str | None = None


class EvaluationResultSchema(BaseModel):
    ranker_name: str
    circuit_name: str
    top1_accuracy: float
    top5_accuracy: float
    top10_accuracy: float
    recall_at_k: float
    precision_at_k: float
    mean_reciprocal_rank: float
    search_space_reduction: float
    k: int


class BenchmarkStartRequest(BaseModel):
    dataset_dir: str
    ranker_name: str
    k: int = 5


class BenchmarkStatusResponse(BaseModel):
    job_id: str
    status: str  # "RUNNING", "COMPLETED", "FAILED"
    progress: float  # 0.0 to 100.0
    message: str


class BenchmarkResultResponse(BaseModel):
    job_id: str
    status: str
    results: Optional[List[EvaluationResultSchema]] = None
    error: Optional[str] = None
