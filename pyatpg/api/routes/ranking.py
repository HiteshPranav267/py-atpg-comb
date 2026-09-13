from fastapi import APIRouter, HTTPException
from typing import List

from pyatpg.api.schemas import RankingRequestSchema, RankingResultSchema, FeatureSetSchema
from pyatpg.api.services.ranking_service import RankingService
from pyatpg.analysis.models import FeatureSet, GateFeature, GateRole
from pyatpg.core.analytics_store import get_analytics_store

router = APIRouter(prefix="/api/ranking", tags=["ranking"])


@router.get("/rankers", response_model=List[str])
def get_rankers():
    """Return all dynamically available ranker names."""
    return RankingService.get_available_rankers()


@router.post("/run", response_model=RankingResultSchema)
def run_ranking(req: RankingRequestSchema):
    """Execute a ranking on a provided FeatureSet."""
    try:
        # Reconstruct the domain FeatureSet from the Pydantic schema
        gate_features = [
            GateFeature(
                gate_id=gf.gate_id,
                gate_type=gf.gate_type,
                role=GateRole(gf.role),
                level=gf.level,
                fanin=gf.fanin,
                fanout=gf.fanout,
                pi_distance=gf.pi_distance,
                po_distance=gf.po_distance,
                cc0=gf.cc0,
                cc1=gf.cc1,
                co=gf.co,
                toggle_rate=gf.toggle_rate,
                detectable=gf.detectable,
                undetected=gf.undetected,
                redundant=gf.redundant,
                fault_count=gf.fault_count,
                is_trojan_gate=gf.is_trojan_gate,
                is_trigger_gate=gf.is_trigger_gate,
                is_payload_gate=gf.is_payload_gate,
                trojan_id=gf.trojan_id,
                extra_features=gf.extra_features,
            )
            for gf in req.feature_set.gate_features
        ]
        fs = FeatureSet(
            circuit_name=req.feature_set.circuit_name,
            gate_features=gate_features,
            atpg_coverage=req.feature_set.atpg_coverage,
            extraction_time_ms=req.feature_set.extraction_time_ms,
        )

        result = RankingService.rank(
            fs,
            req.ranker_name,
            api_key=req.api_key,
            llm_provider=req.llm_provider,
            llm_model=req.llm_model,
            llm_base_url=req.llm_base_url,
        )

        get_analytics_store().record(
            "ranking_run",
            circuit_name=result.circuit_name,
            ranker_name=result.ranker_name,
            gate_count=len(result.ranked_gates),
        )

        return RankingResultSchema(
            circuit_name=result.circuit_name,
            ranker_name=result.ranker_name,
            execution_time=result.execution_time,
            ranked_gates=[
                {"gate_id": g.gate_id, "score": g.score, "rank": g.rank, "reason": g.reason}
                for g in result.ranked_gates
            ],
        )
    except (ValueError, ImportError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
